"""Package-internal filesystem safety primitives shared by OKF write services.

The public write APIs remain ``apply`` and the bounded single-concept edit
services. This module owns the mechanics both need: coherent snapshots,
lossless physical document reconstruction, candidate-tree staging, validation,
and the final whole-bundle freshness check.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import validate_path
from okf_parser.discovery import IGNORED_DIRECTORIES, is_markdown_filename
from okf_parser.exclusion import ExclusionRules
from okf_parser.models import Severity
from okf_parser.parser import (
    DocumentParseError,
    concept_id,
    is_reserved_document,
    parse_document_text,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\r?\n(.*?)(?:\r?\n)?---[ \t]*(?:\r?\n(.*))?\Z",
    re.DOTALL,
)


class WriteSupportError(ValueError):
    """Raised when a coherent write snapshot cannot be constructed."""


@dataclass(frozen=True, slots=True)
class WriteResult:
    """Transport-neutral result shared by bounded filesystem write services."""

    changed_paths: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()
    succeeded: bool = True
    written: bool = False
    validation: tuple[dict[str, object], ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-ready write result shape."""
        payload: dict[str, object] = {
            "changed_paths": list(self.changed_paths),
            "skipped_paths": list(self.skipped_paths),
            "succeeded": self.succeeded,
            "written": self.written,
            "validation": list(self.validation),
            "conflict_paths": list(self.conflict_paths),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True, slots=True)
class RawDocument:
    """A concept document's bytes split for lossless reconstruction."""

    bom: bytes
    crlf: bool
    frontmatter_text: str
    body_text: str


@dataclass(frozen=True, slots=True)
class ConceptSnapshot:
    """One concept captured from the exact bytes used by a write plan."""

    path: Path
    relative: str
    concept_id: str
    concept_type: str
    frontmatter: dict[str, object]
    body: str
    content_hash: str
    raw: RawDocument


@dataclass(frozen=True, slots=True)
class BundleSnapshot:
    """One coherent filesystem manifest plus parsed concept snapshots."""

    manifest: dict[str, tuple[int, int]]
    concepts: list[ConceptSnapshot]


type CandidateDocuments = Mapping[str, tuple[RawDocument, str, Path]]


def parse_raw(data: bytes) -> RawDocument | None:
    """Split already-read concept bytes without a second filesystem read."""
    bom = b"\xef\xbb\xbf" if data.startswith(b"\xef\xbb\xbf") else b""
    try:
        text = data[len(bom) :].decode("utf-8")
    except UnicodeDecodeError:
        return None
    crlf = "\r\n" in text
    normalized = text.replace("\r\n", "\n")
    match = _FRONTMATTER_RE.match(normalized)
    if match is None:
        return None
    return RawDocument(
        bom=bom,
        crlf=crlf,
        frontmatter_text=match.group(1),
        body_text=match.group(2) or "",
    )


def write_raw(path: Path, raw: RawDocument, frontmatter_text: str) -> None:
    """Atomically replace one staged/live document preserving physical style."""
    text = "---\n" + frontmatter_text
    if not text.endswith("\n"):
        text += "\n"
    text += "---\n" + raw.body_text
    if raw.crlf:
        text = text.replace("\n", "\r\n")
    data = raw.bom + text.encode("utf-8")
    tmp = path.with_name(f".{path.name}.okf-write.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _parse_concept(root: Path, path: Path, raw: bytes) -> ConceptSnapshot | None:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    try:
        parsed = parse_document_text(path, text)
    except DocumentParseError:
        return None
    if not parsed.concept_type:
        return None
    raw_document = parse_raw(raw)
    if raw_document is None:
        return None
    return ConceptSnapshot(
        path=path,
        relative=path.relative_to(root).as_posix(),
        concept_id=concept_id(root, path),
        concept_type=parsed.concept_type,
        frontmatter=dict(parsed.frontmatter),
        body=parsed.body,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw=raw_document,
    )


def _file_signature(path: Path) -> tuple[int, int]:
    stat = path.stat()
    return (stat.st_size, stat.st_mtime_ns)


def _prune_walk_directories(
    directory: Path,
    directory_names: list[str],
    base: Path,
    rules: ExclusionRules,
    *,
    prunes: bool,
) -> None:
    directory_names[:] = [
        name
        for name in directory_names
        if name not in IGNORED_DIRECTORIES
        and not (directory / name).is_symlink()
        and not (prunes and rules.excludes((base / name).as_posix(), is_dir=True))
    ]


def snapshot_bundle(root: Path, exclude: Sequence[str]) -> BundleSnapshot:
    """Capture every visible file signature and each concept's bytes coherently."""
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    manifest: dict[str, tuple[int, int]] = {}
    concepts: list[ConceptSnapshot] = []
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            posix = (base / name).as_posix()
            is_concept_candidate = (
                is_markdown_filename(name)
                and not rules.excludes(posix)
                and not is_reserved_document(source)
            )
            if not is_concept_candidate:
                manifest[posix] = _file_signature(source)
                continue
            before_stat = _file_signature(source)
            raw = source.read_bytes()
            after_stat = _file_signature(source)
            if before_stat != after_stat:
                raise WriteSupportError(f"file changed while it was being read: {posix}")
            manifest[posix] = after_stat
            concept = _parse_concept(root, source, raw)
            if concept is not None:
                concepts.append(concept)
    return BundleSnapshot(manifest=manifest, concepts=concepts)


def snapshot_manifest(root: Path, exclude: Sequence[str]) -> dict[str, tuple[int, int]]:
    """Return the exact visible filesystem universe used by the final freshness check."""
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    manifest: dict[str, tuple[int, int]] = {}
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            manifest[(base / name).as_posix()] = _file_signature(source)
    return manifest


def build_candidate_tree(
    root: Path,
    candidate_root: Path,
    candidates: CandidateDocuments,
    exclude: Sequence[str],
) -> None:
    """Mirror the visible bundle into staging, substituting candidate documents."""
    rules = ExclusionRules.read(root, exclude)
    prunes = not rules.has_negation
    for directory, directory_names, filenames in root.walk(follow_symlinks=False):
        base = directory.relative_to(root)
        _prune_walk_directories(directory, directory_names, base, rules, prunes=prunes)
        for name in filenames:
            source = directory / name
            if source.is_symlink():
                continue
            relative = base / name
            destination = candidate_root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            posix = relative.as_posix()
            candidate = candidates.get(posix)
            if candidate is not None:
                raw, frontmatter_text, _ = candidate
                write_raw(destination, raw, frontmatter_text)
                continue
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)


def stage_validate_write(
    root: Path,
    exclude: Sequence[str],
    candidates: CandidateDocuments,
    baseline_keys: set[tuple[str, str, str]],
    changed_paths: Sequence[str],
    touched_hashes: Mapping[str, str],
    baseline_manifest: Mapping[str, tuple[int, int]],
    *,
    conflict_error: str,
    temp_prefix: str = "okf-write-",
) -> dict[str, object]:
    """Stage, validate, recheck freshness, then atomically replace candidate files."""
    with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        try:
            build_candidate_tree(root, candidate_root, candidates, exclude)
        except OSError as exc:
            return WriteResult(
                succeeded=False,
                error=f"could not stage the candidate bundle: {exc}",
            ).to_dict()

        candidate_report = validate_path(candidate_root, exclude)
        candidate_keys = {
            (item.code, item.path, item.message)
            for item in candidate_report.violations
            if item.severity == Severity.ERROR
        }
        new_diagnostics = candidate_keys - baseline_keys
        if new_diagnostics:
            validation_payload: tuple[dict[str, object], ...] = tuple(
                {"code": code, "path": diagnostic_path, "message": message}
                for code, diagnostic_path, message in sorted(new_diagnostics)
            )
            return WriteResult(
                succeeded=False,
                validation=validation_payload,
                error="candidate bundle introduces new normative diagnostics",
            ).to_dict()

        current_manifest = snapshot_manifest(root, exclude)
        baseline_keys_set = set(baseline_manifest)
        current_keys_set = set(current_manifest)
        candidate_keys_set = set(candidates)
        added_or_removed = baseline_keys_set ^ current_keys_set
        stale_untouched = {
            relative
            for relative in baseline_keys_set & current_keys_set - candidate_keys_set
            if baseline_manifest[relative] != current_manifest[relative]
        }
        stale_touched = {
            relative
            for relative, (_, _, real) in candidates.items()
            if _sha256(real) != touched_hashes[relative]
        }
        conflicts = added_or_removed | stale_untouched | stale_touched
        if conflicts:
            return WriteResult(
                succeeded=False,
                conflict_paths=tuple(sorted(conflicts)),
                error=conflict_error,
            ).to_dict()

        for raw, frontmatter_text, real_path in candidates.values():
            write_raw(real_path, raw, frontmatter_text)

    return WriteResult(changed_paths=tuple(sorted(changed_paths)), written=True).to_dict()
