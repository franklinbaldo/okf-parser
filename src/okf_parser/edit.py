"""Conflict-safe single-concept Markdown-body editing.

Frontmatter mutation deliberately remains owned by :mod:`okf_parser.apply` and
its DuckDB compiler. This module fills the orthogonal gap a document editor
needs: replacing one concept's Markdown body without exposing arbitrary
filesystem writes. Candidate staging, bundle validation and final freshness
checks reuse the same write-path machinery as ``apply``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from okf_parser.bundle import load_bundle, validate_path
from okf_parser.digests import normalize_newlines
from okf_parser.models import Severity
from okf_parser.parser import parse_document_text
from okf_parser.write_support import (
    BundleSnapshot as _BundleSnapshot,
    ConceptSnapshot as _Concept,
    WriteSupportError,
    build_candidate_tree as _build_candidate_tree,
    snapshot_bundle as _snapshot_bundle,
    stage_validate_write as _stage_validate_write,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class EditError(ValueError):
    """Raised when a single-concept edit request is structurally invalid."""


@dataclass(frozen=True, slots=True)
class EditResult:
    """JSON-ready outcome of a single-concept preview or commit."""

    concept_id: str
    path: str
    source_digest: str
    candidate_source_digest: str
    candidate_parsed_digest: str
    changed: bool
    succeeded: bool = True
    written: bool = False
    validation: tuple[dict[str, object], ...] = ()
    conflict_paths: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON-ready edit result shape."""
        payload: dict[str, object] = {
            "concept_id": self.concept_id,
            "path": self.path,
            "source_digest": self.source_digest,
            "candidate_source_digest": self.candidate_source_digest,
            "candidate_parsed_digest": self.candidate_parsed_digest,
            "changed": self.changed,
            "succeeded": self.succeeded,
            "written": self.written,
            "validation": list(self.validation),
            "conflict_paths": list(self.conflict_paths),
        }
        if self.error is not None:
            payload["error"] = self.error
        return payload


def _select_concept(snapshot: _BundleSnapshot, concept_id: str) -> _Concept:
    matches = [concept for concept in snapshot.concepts if concept.concept_id == concept_id]
    if len(matches) != 1:
        msg = f"concept does not exist exactly once: {concept_id}"
        raise EditError(msg)
    return matches[0]


def _snapshot_parsed_digest(concept: _Concept) -> str:
    """Compute parsed identity from the snapshot bytes without rereading disk."""
    text = "---\n" + concept.raw.frontmatter_text
    if not text.endswith("\n"):
        text += "\n"
    text += "---\n" + concept.raw.body_text
    return parse_document_text(concept.path, text).parsed_digest


def _edit_concept(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    write: bool,
    exclude: Sequence[str],
) -> dict[str, object]:
    try:
        body.encode("utf-8")
    except UnicodeEncodeError as exc:
        msg = "body must be valid UTF-8 text"
        raise EditError(msg) from exc

    root = Path(path).resolve()
    try:
        snapshot = _snapshot_bundle(root, exclude)
    except WriteSupportError as exc:
        raise EditError(str(exc)) from exc
    concept = _select_concept(snapshot, concept_id)

    current_source_digest = f"sha256:{concept.content_hash}"
    current_parsed_digest = _snapshot_parsed_digest(concept)
    if current_source_digest != expected_source_digest:
        return EditResult(
            concept_id=concept.concept_id,
            path=concept.relative,
            source_digest=current_source_digest,
            candidate_source_digest=current_source_digest,
            candidate_parsed_digest=current_parsed_digest,
            changed=False,
            succeeded=False,
            conflict_paths=(concept.relative,),
            error="concept source changed since it was read",
        ).to_dict()

    normalized_body = normalize_newlines(body)
    changed = normalized_body != concept.raw.body_text
    if not changed:
        return EditResult(
            concept_id=concept.concept_id,
            path=concept.relative,
            source_digest=current_source_digest,
            candidate_source_digest=current_source_digest,
            candidate_parsed_digest=current_parsed_digest,
            changed=False,
        ).to_dict()

    baseline = validate_path(root, exclude)
    baseline_keys = {
        (item.code, item.path, item.message)
        for item in baseline.violations
        if item.severity is Severity.ERROR
    }

    candidate_raw = replace(concept.raw, body_text=normalized_body)
    candidates = {
        concept.relative: (candidate_raw, concept.raw.frontmatter_text, concept.path),
    }

    # Preview uses the exact same staged candidate-tree construction as apply.
    # Loading the staged relation yields the exact digests a successful commit
    # will produce, without rereading the live source document.
    with tempfile.TemporaryDirectory(prefix="okf-edit-") as tmp:
        candidate_root = Path(tmp) / "bundle"
        candidate_root.mkdir()
        try:
            _build_candidate_tree(root, candidate_root, candidates, exclude)
        except OSError as exc:
            return EditResult(
                concept_id=concept.concept_id,
                path=concept.relative,
                source_digest=current_source_digest,
                candidate_source_digest=current_source_digest,
                candidate_parsed_digest=current_parsed_digest,
                changed=True,
                succeeded=False,
                error=f"could not stage the candidate bundle: {exc}",
            ).to_dict()

        candidate_bundle = load_bundle(candidate_root, exclude)
        staged = (
            candidate_bundle.concepts.filter(candidate_bundle.concepts.concept_id == concept_id)
            .select("source_digest", "parsed_digest")
            .execute()
        )
        if len(staged) != 1:
            msg = f"candidate concept does not exist exactly once after staging: {concept_id}"
            raise EditError(msg)
        candidate_source_digest = str(staged.iloc[0]["source_digest"])
        candidate_parsed_digest = str(staged.iloc[0]["parsed_digest"])

        candidate_report = validate_path(candidate_root, exclude)
        candidate_keys = {
            (item.code, item.path, item.message)
            for item in candidate_report.violations
            if item.severity is Severity.ERROR
        }
        new_diagnostics = candidate_keys - baseline_keys
        if new_diagnostics:
            validation = tuple(
                {"code": code, "path": diagnostic_path, "message": message}
                for code, diagnostic_path, message in sorted(new_diagnostics)
            )
            return EditResult(
                concept_id=concept.concept_id,
                path=concept.relative,
                source_digest=current_source_digest,
                candidate_source_digest=candidate_source_digest,
                candidate_parsed_digest=candidate_parsed_digest,
                changed=True,
                succeeded=False,
                validation=validation,
                error="candidate bundle introduces new normative diagnostics",
            ).to_dict()

    if not write:
        return EditResult(
            concept_id=concept.concept_id,
            path=concept.relative,
            source_digest=current_source_digest,
            candidate_source_digest=candidate_source_digest,
            candidate_parsed_digest=candidate_parsed_digest,
            changed=True,
        ).to_dict()

    write_result = _stage_validate_write(
        root,
        exclude,
        candidates,
        baseline_keys,
        [concept.relative],
        {concept.relative: concept.content_hash},
        snapshot.manifest,
        conflict_error="the bundle changed since edit validated it",
        temp_prefix="okf-edit-write-",
    )
    succeeded = bool(write_result.get("succeeded", False))
    written = bool(write_result.get("written", False))
    return EditResult(
        concept_id=concept.concept_id,
        path=concept.relative,
        source_digest=current_source_digest,
        candidate_source_digest=candidate_source_digest,
        candidate_parsed_digest=candidate_parsed_digest,
        changed=True,
        succeeded=succeeded,
        written=written,
        validation=tuple(write_result.get("validation", ())),
        conflict_paths=tuple(write_result.get("conflict_paths", ())),
        error=write_result.get("error") if isinstance(write_result.get("error"), str) else None,
    ).to_dict()


def preview_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Preview one body replacement without touching the live bundle."""
    return _edit_concept(
        path,
        concept_id,
        body,
        expected_source_digest,
        write=False,
        exclude=exclude,
    )


def write_concept_edit(
    path: str,
    concept_id: str,
    body: str,
    expected_source_digest: str,
    *,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Commit one body replacement after staging, validation and freshness checks."""
    return _edit_concept(
        path,
        concept_id,
        body,
        expected_source_digest,
        write=True,
        exclude=exclude,
    )
