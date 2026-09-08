"""Conflict-safe structured creation and frontmatter patching for OKF concepts.

This module is the public write boundary for consumers that own domain semantics but
must not reimplement OKF serialization, candidate staging, validation, freshness, or
atomic filesystem writes.
"""

from __future__ import annotations

import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import cast

from okf_parser.bundle import load_bundle, validate_path
from okf_parser.digests import normalize_newlines
from okf_parser.models import Severity, YamlValue
from okf_parser.serialization import OKFDocument, render_frontmatter, to_okf
from okf_parser.write_support import (
    BundleSnapshot,
    ConceptSnapshot,
    RawDocument,
    WriteSupportError,
    build_candidate_tree,
    snapshot_bundle,
    stage_validate_write,
)


class ConceptWriteError(ValueError):
    """Raised when a structured concept write request is invalid."""


@dataclass(frozen=True, slots=True)
class ConceptWriteResult:
    """JSON-ready outcome of a concept create or patch preview/commit."""

    concept_id: str
    path: str
    changed: bool
    succeeded: bool = True
    written: bool = False
    source_digest: str | None = None
    candidate_source_digest: str | None = None
    candidate_parsed_digest: str | None = None
    validation: tuple[dict[str, object], ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the stable transport-neutral result shape."""
        payload: dict[str, object] = {
            "concept_id": self.concept_id,
            "path": self.path,
            "changed": self.changed,
            "succeeded": self.succeeded,
            "written": self.written,
            "validation": list(self.validation),
            "conflict_paths": list(self.conflict_paths),
        }
        if self.source_digest is not None:
            payload["source_digest"] = self.source_digest
        if self.candidate_source_digest is not None:
            payload["candidate_source_digest"] = self.candidate_source_digest
        if self.candidate_parsed_digest is not None:
            payload["candidate_parsed_digest"] = self.candidate_parsed_digest
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _select_concept(snapshot: BundleSnapshot, concept_id: str) -> ConceptSnapshot:
    matches = [concept for concept in snapshot.concepts if concept.concept_id == concept_id]
    if len(matches) != 1:
        msg = f"concept does not exist exactly once: {concept_id}"
        raise ConceptWriteError(msg)
    return matches[0]


def _normalize_frontmatter(frontmatter: Mapping[str, object]) -> dict[str, YamlValue]:
    try:
        document = to_okf(frontmatter)
    except (TypeError, ValueError) as exc:
        raise ConceptWriteError(str(exc)) from exc
    return dict(document.frontmatter)


def _validated_relative_path(relative_path: str) -> str:
    raw = str(relative_path).replace("\\", "/")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise ConceptWriteError("relative_path must stay inside the bundle root")
    if relative.suffix.lower() not in {".md", ".markdown"}:
        raise ConceptWriteError("relative_path must name a Markdown concept document")
    return relative.as_posix()


def _baseline_error_keys(root: Path, exclude: Sequence[str]) -> set[tuple[str, str, str]]:
    report = validate_path(root, exclude)
    return {
        (item.code, item.path, item.message)
        for item in report.violations
        if item.severity is Severity.ERROR
    }


def _candidate_digests(
    root: Path,
    candidates: Mapping[str, tuple[RawDocument, str, Path]],
    exclude: Sequence[str],
    *,
    concept_id: str | None = None,
    relative_path: str | None = None,
) -> tuple[str, str, str]:
    with tempfile.TemporaryDirectory(prefix="okf-concept-write-preview-") as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        build_candidate_tree(root, candidate_root, candidates, exclude)
        bundle = load_bundle(candidate_root, exclude)
        relation = bundle.concepts
        if concept_id is not None:
            relation = relation.filter(relation.concept_id == concept_id)
        elif relative_path is not None:
            relation = relation.filter(relation.path == relative_path)
        else:
            raise AssertionError("candidate lookup requires concept_id or relative_path")
        staged = relation.select("concept_id", "source_digest", "parsed_digest").execute()
        if len(staged) != 1:
            raise ConceptWriteError("candidate concept does not exist exactly once after staging")
        row = staged.iloc[0]
        return str(row["concept_id"]), str(row["source_digest"]), str(row["parsed_digest"])


def _decode_write_result(payload: Mapping[str, object]) -> tuple[
    bool,
    bool,
    tuple[dict[str, object], ...],
    tuple[str, ...],
    str | None,
]:
    validation_value = payload.get("validation", ())
    validation = (
        cast("tuple[dict[str, object], ...]", tuple(validation_value))
        if isinstance(validation_value, (list, tuple))
        else ()
    )
    conflict_value = payload.get("conflict_paths", ())
    conflict_paths = (
        tuple(item for item in conflict_value if isinstance(item, str))
        if isinstance(conflict_value, (list, tuple))
        else ()
    )
    error_value = payload.get("error")
    error = error_value if isinstance(error_value, str) else None
    return (
        bool(payload.get("succeeded", False)),
        bool(payload.get("written", False)),
        validation,
        conflict_paths,
        error,
    )


def _candidate_validation(
    root: Path,
    candidates: Mapping[str, tuple[RawDocument, str, Path]],
    exclude: Sequence[str],
    baseline_keys: set[tuple[str, str, str]],
) -> tuple[dict[str, object], ...]:
    with tempfile.TemporaryDirectory(prefix="okf-concept-write-validation-") as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        build_candidate_tree(root, candidate_root, candidates, exclude)
        report = validate_path(candidate_root, exclude)
    keys = {
        (item.code, item.path, item.message)
        for item in report.violations
        if item.severity is Severity.ERROR
    }
    return tuple(
        {"code": code, "path": diagnostic_path, "message": message}
        for code, diagnostic_path, message in sorted(keys - baseline_keys)
    )


def _patch_concept(  # noqa: PLR0913 -- each argument is an independent safety input.
    path: str,
    concept_id: str,
    updates: Mapping[str, object],
    expected_source_digest: str,
    *,
    remove: Sequence[str],
    body: str | None,
    write: bool,
    exclude: Sequence[str],
) -> dict[str, object]:
    root = Path(path).resolve()
    try:
        snapshot = snapshot_bundle(root, exclude)
    except WriteSupportError as exc:
        raise ConceptWriteError(str(exc)) from exc
    concept = _select_concept(snapshot, concept_id)
    if concept.source_digest != expected_source_digest:
        return ConceptWriteResult(
            concept_id=concept.concept_id,
            path=concept.relative,
            source_digest=concept.source_digest,
            changed=False,
            succeeded=False,
            conflict_paths=(concept.relative,),
            error="concept source changed since it was read",
        ).to_dict()

    forbidden = ({"type", "id"} & set(updates)) | ({"type", "id"} & set(remove))
    if forbidden:
        names = ", ".join(sorted(forbidden))
        raise ConceptWriteError(f"concept patch cannot change identity fields: {names}")

    merged: dict[str, object] = dict(concept.frontmatter)
    merged.update(updates)
    for key in remove:
        merged.pop(str(key), None)
    normalized = _normalize_frontmatter(merged)
    frontmatter_text = render_frontmatter(normalized)
    candidate_body = concept.raw.body_text if body is None else normalize_newlines(body)
    candidate_raw = replace(concept.raw, body_text=candidate_body)
    changed = (
        frontmatter_text.rstrip("\n") != concept.raw.frontmatter_text.rstrip("\n")
        or candidate_body != concept.raw.body_text
    )
    if not changed:
        return ConceptWriteResult(
            concept_id=concept.concept_id,
            path=concept.relative,
            source_digest=concept.source_digest,
            candidate_source_digest=concept.source_digest,
            candidate_parsed_digest=concept.parsed_digest,
            changed=False,
        ).to_dict()

    candidates = {concept.relative: (candidate_raw, frontmatter_text, concept.path)}
    candidate_id, source_digest, parsed_digest = _candidate_digests(
        root,
        candidates,
        exclude,
        concept_id=concept.concept_id,
    )
    baseline_keys = _baseline_error_keys(root, exclude)
    if not write:
        candidate_report = _candidate_validation(root, candidates, exclude, baseline_keys)
        if candidate_report:
            return ConceptWriteResult(
                concept_id=candidate_id,
                path=concept.relative,
                source_digest=concept.source_digest,
                candidate_source_digest=source_digest,
                candidate_parsed_digest=parsed_digest,
                changed=True,
                succeeded=False,
                validation=candidate_report,
                error="candidate bundle introduces new normative diagnostics",
            ).to_dict()
        return ConceptWriteResult(
            concept_id=candidate_id,
            path=concept.relative,
            source_digest=concept.source_digest,
            candidate_source_digest=source_digest,
            candidate_parsed_digest=parsed_digest,
            changed=True,
        ).to_dict()

    payload = stage_validate_write(
        root,
        exclude,
        candidates,
        baseline_keys,
        [concept.relative],
        {concept.relative: concept.content_hash},
        snapshot.manifest,
        conflict_error="the bundle changed since concept patch validated it",
        temp_prefix="okf-concept-patch-write-",
    )
    succeeded, written, validation, conflicts, error = _decode_write_result(payload)
    return ConceptWriteResult(
        concept_id=candidate_id,
        path=concept.relative,
        source_digest=concept.source_digest,
        candidate_source_digest=source_digest,
        candidate_parsed_digest=parsed_digest,
        changed=True,
        succeeded=succeeded,
        written=written,
        validation=validation,
        conflict_paths=conflicts,
        error=error,
    ).to_dict()


def preview_concept_patch(
    path: str,
    concept_id: str,
    updates: Mapping[str, object],
    expected_source_digest: str,
    *,
    remove: Sequence[str] = (),
    body: str | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Preview one identity-preserving structured concept patch."""
    return _patch_concept(
        path,
        concept_id,
        updates,
        expected_source_digest,
        remove=remove,
        body=body,
        write=False,
        exclude=exclude,
    )


def write_concept_patch(
    path: str,
    concept_id: str,
    updates: Mapping[str, object],
    expected_source_digest: str,
    *,
    remove: Sequence[str] = (),
    body: str | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Commit one identity-preserving structured concept patch after validation."""
    return _patch_concept(
        path,
        concept_id,
        updates,
        expected_source_digest,
        remove=remove,
        body=body,
        write=True,
        exclude=exclude,
    )


def _create_concept(  # noqa: PLR0913 -- each argument is an independent safety input.
    path: str,
    relative_path: str,
    frontmatter: Mapping[str, object],
    *,
    body: str,
    write: bool,
    exclude: Sequence[str],
) -> dict[str, object]:
    root = Path(path).resolve()
    relative = _validated_relative_path(relative_path)
    target = root / Path(relative)
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ConceptWriteError("relative_path must stay inside the bundle root") from exc
    if target.exists():
        return ConceptWriteResult(
            concept_id=str(frontmatter.get("id") or ""),
            path=relative,
            changed=False,
            succeeded=False,
            conflict_paths=(relative,),
            error="target concept already exists",
        ).to_dict()

    normalized = _normalize_frontmatter(frontmatter)
    document = OKFDocument(frontmatter=normalized, body=normalize_newlines(body))
    frontmatter_text = render_frontmatter(document.frontmatter)
    raw = RawDocument(bom=b"", crlf=False, frontmatter_text="", body_text=document.body)
    try:
        snapshot = snapshot_bundle(root, exclude)
    except WriteSupportError as exc:
        raise ConceptWriteError(str(exc)) from exc
    candidates = {relative: (raw, frontmatter_text, target)}
    candidate_id, source_digest, parsed_digest = _candidate_digests(
        root,
        candidates,
        exclude,
        relative_path=relative,
    )
    baseline_keys = _baseline_error_keys(root, exclude)
    if not write:
        validation = _candidate_validation(root, candidates, exclude, baseline_keys)
        return ConceptWriteResult(
            concept_id=candidate_id,
            path=relative,
            candidate_source_digest=source_digest,
            candidate_parsed_digest=parsed_digest,
            changed=True,
            succeeded=not validation,
            validation=validation,
            error="candidate bundle introduces new normative diagnostics" if validation else None,
        ).to_dict()

    payload = stage_validate_write(
        root,
        exclude,
        candidates,
        baseline_keys,
        [relative],
        {relative: None},
        snapshot.manifest,
        conflict_error="the bundle changed since concept creation validated it",
        temp_prefix="okf-concept-create-write-",
    )
    succeeded, written, validation, conflicts, error = _decode_write_result(payload)
    return ConceptWriteResult(
        concept_id=candidate_id,
        path=relative,
        candidate_source_digest=source_digest,
        candidate_parsed_digest=parsed_digest,
        changed=True,
        succeeded=succeeded,
        written=written,
        validation=validation,
        conflict_paths=conflicts,
        error=error,
    ).to_dict()


def preview_concept_create(
    path: str,
    relative_path: str,
    frontmatter: Mapping[str, object],
    *,
    body: str = "",
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Preview creation of one structured OKF concept."""
    return _create_concept(
        path,
        relative_path,
        frontmatter,
        body=body,
        write=False,
        exclude=exclude,
    )


def write_concept_create(
    path: str,
    relative_path: str,
    frontmatter: Mapping[str, object],
    *,
    body: str = "",
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Create one structured OKF concept after staged validation and freshness checks."""
    return _create_concept(
        path,
        relative_path,
        frontmatter,
        body=body,
        write=True,
        exclude=exclude,
    )
