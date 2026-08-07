"""Scaffold missing type specification documents, per RFC 0006 decision 11.

`--require-spec`/`--spec-template` only ever *reports* a type in use whose
derived `docs/types/{slug}.md` document is absent (`type_specs.py`); nothing
creates one. This module fills that gap: it computes the same derived path
for every type in use and writes a minimal stub for whichever ones are
missing, never touching a document that already exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from okf_parser.type_specs import spec_relative_path

if TYPE_CHECKING:
    from pathlib import Path


def _stub_content(concept_type: str) -> str:
    return (
        "---\ntype: Spec\n---\n\n"
        f"# {concept_type}\n\n"
        "TODO: describe this type's frontmatter fields and semantics.\n"
    )


def _plan(
    root: Path, concept_types: set[str], template: str
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Split types in use into ``(type -> path to create)`` and derived-path collisions.

    A collision - two or more types deriving the same relative path - mirrors
    decision 1's "neither type gets a declaration" fallback: none of the
    colliding types are scheduled for creation.
    """
    by_path: dict[str, list[str]] = {}
    for concept_type in sorted(concept_types):
        if not concept_type:
            continue
        relative = spec_relative_path(template, concept_type)
        if relative is None:
            continue
        by_path.setdefault(relative, []).append(concept_type)

    collisions = {path: types for path, types in by_path.items() if len(types) > 1}
    to_create = {
        types[0]: relative
        for relative, types in by_path.items()
        if relative not in collisions and not (root / relative).is_file()
    }
    return to_create, collisions


def scaffold_missing_specs(
    root: Path,
    concept_types: set[str],
    template: str,
    *,
    write: bool = False,
) -> dict[str, object]:
    """Create a minimal specification stub for every type in use that lacks one.

    A derived-path collision between two types blocks every write for this
    invocation, not just the colliding types', so a caller never gets a
    partial scaffold silently missing the types it couldn't resolve.
    """
    to_create, collisions = _plan(root, concept_types, template)
    if collisions:
        return {
            "created": [],
            "would_create": [],
            "collisions": [
                {"path": path, "types": sorted(types)} for path, types in sorted(collisions.items())
            ],
            "written": False,
        }
    if not write:
        return {
            "created": [],
            "would_create": sorted(to_create.values()),
            "collisions": [],
            "written": False,
        }
    created: list[str] = []
    for concept_type, relative in to_create.items():
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_stub_content(concept_type), encoding="utf-8")
        created.append(relative)
    return {"created": sorted(created), "would_create": [], "collisions": [], "written": True}
