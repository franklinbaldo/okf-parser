"""Load an OKF bundle into Ibis relations and validate its structure."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, cast

import ibis
import networkx as nx

from okf_parser.discovery import discover_markdown
from okf_parser.exclusion import ExclusionRules
from okf_parser.models import (
    ConceptRecord,
    LinkRecord,
    ReservedRecord,
    Severity,
    ValidationReport,
    Violation,
    YamlValue,
)
from okf_parser.parser import (
    DocumentParseError,
    concept_id,
    has_markdown_suffix,
    is_reserved_document,
    iter_headings,
    iter_markdown_links,
    looks_like_frontmatter_link,
    parse_document,
    resolve_local_target,
    split_optional_frontmatter,
)
from okf_parser.type_specs import missing_type_specs

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence
    from pathlib import Path

    from ibis.expr.types import Table

_CONCEPT_SCHEMA = ibis.schema(
    {
        "concept_id": "string",
        "logical_key": "string",
        "path": "string",
        "concept_type": "string",
        "title": "string",
        "description": "string",
        "frontmatter_json": "string",
        "body": "string",
    }
)
_RESERVED_SCHEMA = ibis.schema({"path": "string", "filename": "string", "body": "string"})
_LINK_SCHEMA = ibis.schema(
    {
        "source_id": "string",
        "raw_target": "string",
        "target_id": "string",
        "exists": "boolean",
        "origin": "string",
    }
)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TITLE_LEVEL = 1
_DATE_LEVEL = 2
type TableRecord = ConceptRecord | ReservedRecord | LinkRecord


def _ordered(diagnostics: Iterable[Violation]) -> list[Violation]:
    """Order diagnostics deterministically by path, severity, code and message."""
    return sorted(
        diagnostics,
        key=lambda item: (item.path, item.severity.value, item.code, item.message),
    )


def _table(records: Sequence[TableRecord], schema: ibis.Schema) -> Table:
    rows = [record.model_dump() for record in records]
    return ibis.memtable(rows, schema=schema)


def _optional_text(value: object) -> str | None:
    """Normalize an Ibis/pandas cell into a string or ``None``.

    A null string column round-trips through pandas as float ``nan``, which
    must not leak into NetworkX node attributes.
    """
    return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class Bundle:
    """An immutable relational view of one OKF bundle.

    Deliberately not a Pydantic model: the three table fields are live Ibis
    query expressions rather than data crossing a boundary, and ``validate``
    would collide with ``BaseModel.validate``.
    """

    root: Path
    concepts: Table
    reserved: Table
    links: Table
    diagnostics: tuple[Violation, ...]
    markdown_count: int

    def validate(self) -> list[Violation]:
        """Return deterministic diagnostics ordered by path, severity, and code."""
        return _ordered(self.diagnostics)

    @property
    def concept_types(self) -> set[str]:
        """Every producer-defined type observed in the bundle."""
        column = self.concepts.select("concept_type").execute()["concept_type"]
        return {value for value in column if isinstance(value, str)}

    @property
    def is_conformant(self) -> bool:
        """Whether the bundle has no normative errors."""
        return not any(item.severity is Severity.ERROR for item in self.diagnostics)

    def to_networkx(self) -> nx.MultiDiGraph:
        """Project concepts and resolved Markdown links into a directed graph."""
        graph = nx.MultiDiGraph(bundle_root=str(self.root))
        for row in self.concepts.execute().to_dict(orient="records"):
            graph.add_node(
                row["concept_id"],
                path=row["path"],
                type=row["concept_type"],
                title=_optional_text(row["title"]),
            )
        for row in self.links.execute().to_dict(orient="records"):
            target_id = row["target_id"]
            # A target that exists on disk but never became a concept - because
            # it failed to parse - must not appear as an attribute-less node.
            if isinstance(target_id, str) and graph.has_node(target_id):
                graph.add_edge(
                    row["source_id"],
                    target_id,
                    raw_target=row["raw_target"],
                    origin=row["origin"],
                )
        return graph


def _load_concept(
    root: Path,
    path: Path,
    known_paths: set[Path],
) -> tuple[ConceptRecord | None, list[LinkRecord], list[Violation]]:
    relative = path.relative_to(root).as_posix()
    try:
        parsed = parse_document(path)
    except (DocumentParseError, OSError) as exc:
        return (
            None,
            [],
            [Violation(code="OKF001", severity=Severity.ERROR, path=relative, message=str(exc))],
        )

    diagnostics: list[Violation] = []
    if not parsed.concept_type:
        diagnostics.append(
            Violation(
                code="OKF002",
                severity=Severity.ERROR,
                path=relative,
                message="frontmatter must contain a non-empty string type",
            )
        )

    doc_id = concept_id(root, path)
    links: list[LinkRecord] = []
    raw_links = [(target, "body") for target in iter_markdown_links(parsed.body)]
    raw_links.extend(_iter_frontmatter_links(parsed.frontmatter))
    for raw_target, origin in raw_links:
        resolved = resolve_local_target(root, path, raw_target)
        if resolved is None or not has_markdown_suffix(raw_target):
            continue
        exists = resolved in known_paths
        target_id = (
            concept_id(root, resolved) if exists and not is_reserved_document(resolved) else None
        )
        links.append(
            LinkRecord(
                source_id=doc_id,
                raw_target=raw_target,
                target_id=target_id,
                exists=exists,
                origin=origin,
            )
        )
        if not exists:
            diagnostics.append(
                Violation(
                    code="OKF101",
                    severity=Severity.WARNING,
                    path=relative,
                    message=f"local Markdown link does not resolve: {raw_target}",
                )
            )

    record = ConceptRecord(
        concept_id=doc_id,
        logical_key=doc_id,
        path=relative,
        concept_type=parsed.concept_type,
        title=parsed.title,
        description=parsed.description,
        frontmatter_json=parsed.frontmatter_json,
        body=parsed.body,
    )
    return record, links, diagnostics


def _iter_frontmatter_links(
    value: YamlValue,
    field_path: str = "frontmatter",
) -> list[tuple[str, str]]:
    """Find local Markdown references nested in producer-defined frontmatter.

    The frontmatter has already been validated, so the structure is a finite
    tree: a cyclic YAML anchor is rejected before this walk ever runs.
    """
    if isinstance(value, dict):
        return [
            item
            for key, child in value.items()
            for item in _iter_frontmatter_links(child, f"{field_path}.{key}")
        ]
    if isinstance(value, list):
        return [
            item
            for index, child in enumerate(value)
            for item in _iter_frontmatter_links(child, f"{field_path}[{index}]")
        ]
    if isinstance(value, str) and looks_like_frontmatter_link(value):
        return [(value, field_path)]
    return []


def _validate_index(root: Path, path: Path, text: str) -> tuple[str, list[Violation]]:
    relative = path.relative_to(root).as_posix()
    diagnostics: list[Violation] = []
    try:
        frontmatter, body = split_optional_frontmatter(text)
    except DocumentParseError as exc:
        return text, [
            Violation(code="OKF004", severity=Severity.ERROR, path=relative, message=str(exc))
        ]

    if frontmatter is not None:
        if path.parent != root:
            diagnostics.append(
                Violation(
                    code="OKF004",
                    severity=Severity.ERROR,
                    path=relative,
                    message="only the bundle-root index.md may contain frontmatter",
                )
            )
        elif set(frontmatter) - {"okf_version"}:
            diagnostics.append(
                Violation(
                    code="OKF004",
                    severity=Severity.ERROR,
                    path=relative,
                    message="root index.md frontmatter may contain only okf_version",
                )
            )
    if not _has_title(body):
        diagnostics.append(
            Violation(
                code="OKF005",
                severity=Severity.ERROR,
                path=relative,
                message="index.md must contain at least one level-one section",
            )
        )
    return body, diagnostics


def _has_title(body: str) -> bool:
    """Whether the body opens a level-one section with actual text.

    CommonMark emits a heading token for a bare ``#``, so the level alone is
    not enough to satisfy the reserved-document title rules.
    """
    return any(level == _TITLE_LEVEL and text.strip() for level, text in iter_headings(body))


def _validate_log(root: Path, path: Path, text: str) -> tuple[str, list[Violation]]:
    relative = path.relative_to(root).as_posix()
    diagnostics: list[Violation] = []
    try:
        frontmatter, body = split_optional_frontmatter(text)
    except DocumentParseError as exc:
        return text, [
            Violation(code="OKF006", severity=Severity.ERROR, path=relative, message=str(exc))
        ]
    if frontmatter is not None:
        diagnostics.append(
            Violation(
                code="OKF006",
                severity=Severity.ERROR,
                path=relative,
                message="log.md must not contain frontmatter",
            )
        )
    if not _has_title(body):
        diagnostics.append(
            Violation(
                code="OKF007",
                severity=Severity.ERROR,
                path=relative,
                message="log.md must contain a level-one title",
            )
        )

    parsed_dates: list[date] = []
    for level, heading in iter_headings(body):
        if level != _DATE_LEVEL:
            continue
        if _ISO_DATE_RE.fullmatch(heading) is None:
            diagnostics.append(
                Violation(
                    code="OKF008",
                    severity=Severity.ERROR,
                    path=relative,
                    message=f"log date heading must use YYYY-MM-DD: {heading}",
                )
            )
            continue
        try:
            parsed_dates.append(date.fromisoformat(heading))
        except ValueError:
            diagnostics.append(
                Violation(
                    code="OKF008",
                    severity=Severity.ERROR,
                    path=relative,
                    message=f"log date heading is not a real date: {heading}",
                )
            )
    if parsed_dates != sorted(parsed_dates, reverse=True):
        diagnostics.append(
            Violation(
                code="OKF009",
                severity=Severity.ERROR,
                path=relative,
                message="log date groups must be ordered newest first",
            )
        )
    return body, diagnostics


def load_bundle(root: Path, exclude: Sequence[str] = ()) -> Bundle:
    """Scan a directory and compile its OKF documents into Ibis tables.

    Exclusions come from the bundle's ``.okfignore`` and from ``exclude``,
    which a caller supplies for a one-off run.
    """
    root = root.resolve()
    if not root.is_dir():
        msg = f"bundle root is not a directory: {root}"
        raise NotADirectoryError(msg)

    paths = discover_markdown(root, ExclusionRules.read(root, exclude))
    known_paths = {path.resolve() for path in paths}
    concepts: list[ConceptRecord] = []
    reserved: list[ReservedRecord] = []
    links: list[LinkRecord] = []
    diagnostics: list[Violation] = []

    for path in paths:
        relative = path.relative_to(root).as_posix()
        if is_reserved_document(path):
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError) as exc:
                diagnostics.append(
                    Violation(
                        code="OKF003",
                        severity=Severity.ERROR,
                        path=relative,
                        message=str(exc),
                    )
                )
                continue
            if path.name == "index.md":
                body, reserved_diagnostics = _validate_index(root, path, text)
            else:
                body, reserved_diagnostics = _validate_log(root, path, text)
            diagnostics.extend(reserved_diagnostics)
            reserved.append(ReservedRecord(path=relative, filename=path.name, body=body))
            continue

        record, document_links, document_diagnostics = _load_concept(root, path, known_paths)
        if record is not None:
            concepts.append(record)
        links.extend(document_links)
        diagnostics.extend(document_diagnostics)

    return Bundle(
        root=root,
        concepts=_table(concepts, _CONCEPT_SCHEMA),
        reserved=_table(reserved, _RESERVED_SCHEMA),
        links=_table(links, _LINK_SCHEMA),
        diagnostics=tuple(diagnostics),
        markdown_count=len(paths),
    )


def validate_path(
    path: Path,
    exclude: Sequence[str] = (),
    require_spec: str | None = None,
    *,
    normative_spec: bool = False,
) -> ValidationReport:
    """Validate every Markdown file recursively below a path as OKF v0.2.

    ``require_spec`` adds the optional rule that every producer-defined type in
    use has a specification document at the path its template derives.
    """
    bundle = load_bundle(path, exclude)
    diagnostics = list(bundle.diagnostics)
    if require_spec is not None:
        diagnostics.extend(
            missing_type_specs(
                bundle.root,
                bundle.concept_types,
                require_spec,
                normative=normative_spec,
            )
        )
    violations = tuple(_ordered(diagnostics))
    return ValidationReport(
        root=bundle.root,
        markdown_count=bundle.markdown_count,
        concept_count=cast("int", bundle.concepts.count().execute()),
        reserved_count=cast("int", bundle.reserved.count().execute()),
        violations=violations,
    )
