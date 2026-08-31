# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.4,<0.46",
# ]
# ///
# ruff: noqa: T201
"""Make a codebase-to-OKF bundle self-describing through canonical type specs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from okf_parser import validate_path
from okf_parser.service import init_bundle

SPEC_TEMPLATE = "docs/types/{slug}.md"

TYPE_SPECS: dict[str, tuple[str, str]] = {
    "CodeModule": (
        "A Python source module projected as derived OKF knowledge",
        """A `CodeModule` represents one parsed Python source file. It records syntax-grounded
module identity and source provenance; it does not claim import resolution or runtime behavior.

## Frontmatter

- `type` — always `CodeModule`.
- `title` — source-relative module path.
- `language` — `python` for this recipe.
- `module` — dotted Python module name derived from the source path.
- `source_path` — source-relative path; never an absolute machine path.
- `generated_by` — producer identity, currently `codebase-to-okf`.
""",
    ),
    "CodeClass": (
        "A Python class definition projected from syntax with source provenance",
        """A `CodeClass` represents one Python class definition observed in source syntax. It
preserves compact facts useful for navigation without asserting runtime type resolution.

## Frontmatter

- `type` — always `CodeClass`.
- `title` — source-qualified symbol name.
- `language`, `source_path`, `generated_by` — producer and provenance fields.
- `name` — lexical class name.
- `qualname` — lexical qualified name inside the source module.
- `line_start`, `line_end` — source line range, stored as scalar strings.
- `signature` — compact class declaration text.
- `decorators` — optional syntax-level decorator expressions.
- `bases` — optional syntax-level base expressions; these are not resolved types.
- `fields` — optional names of direct class-body assignments.
- `parent_qualname`, `parent_line_start`, `parent_symbol` — optional immediate lexical parent
  identity and generated concept path; the line component disambiguates same-name redefinitions.
- `child_qualnames`, `child_symbols` — optional immediate lexical children in source order.

Lexical containment is syntax evidence. It does not assert inheritance ownership, descriptor
binding, runtime reachability, or dispatch.
""",
    ),
    "CodeFunction": (
        "A Python function definition outside immediate class scope",
        """A `CodeFunction` represents a Python function definition whose immediate lexical
parent is not a class. Nested functions remain functions and retain lexical provenance.

## Frontmatter

- `type` — always `CodeFunction`.
- `title`, `name`, `qualname` — symbol identity fields.
- `language`, `source_path`, `generated_by` — producer and provenance fields.
- `line_start`, `line_end` — source line range, stored as scalar strings.
- `signature` — deterministic syntax-derived function signature.
- `parameters` — optional rendered parameter fragments.
- `return_annotation` — optional syntax-derived return annotation.
- `decorators` — optional syntax-level decorators.
- `calls_raw` — optional call expressions observed directly in the function body; these are not
  resolved dispatch edges.
- `parent_qualname`, `parent_line_start`, `parent_symbol` — optional immediate lexical parent
  identity and generated concept path; the line component disambiguates same-name redefinitions.
- `child_qualnames`, `child_symbols` — optional immediate lexical children in source order.

Containment is immediate and lexical only; it is not a runtime ownership or reachability claim.
""",
    ),
    "CodeMethod": (
        "A Python function definition whose immediate lexical parent is a class",
        """A `CodeMethod` represents a Python function definition directly inside class scope.
The classification is lexical and does not attempt descriptor or runtime dispatch analysis.

## Frontmatter

- `type` — always `CodeMethod`.
- `title`, `name`, `qualname` — symbol identity fields.
- `language`, `source_path`, `generated_by` — producer and provenance fields.
- `line_start`, `line_end` — source line range, stored as scalar strings.
- `signature` — deterministic syntax-derived method signature.
- `parameters` — optional rendered parameter fragments.
- `return_annotation` — optional syntax-derived return annotation.
- `decorators` — optional syntax-level decorators such as `staticmethod`.
- `calls_raw` — optional call expressions observed directly in the method body; these are not
  resolved dispatch edges.
- `parent_qualname`, `parent_line_start`, `parent_symbol` — optional immediate lexical parent
  identity and generated concept path; the line component disambiguates same-name redefinitions.
- `child_qualnames`, `child_symbols` — optional immediate lexical children in source order.

Containment is immediate and lexical only; it does not strengthen method dispatch semantics.
""",
    ),
    "CodeImport": (
        "A syntax-level Python import observation with unresolved semantics",
        """A `CodeImport` represents one Python import statement as source evidence. It
intentionally does not claim that a module, symbol, environment, or import hook resolved
successfully.

## Frontmatter

- `type` — always `CodeImport`.
- `title` — compact display form of the observed targets.
- `language`, `source_path`, `generated_by` — producer and provenance fields.
- `targets` — syntax-derived imported names.
- `line_start`, `line_end` — source line range, stored as scalar strings.
- `resolution` — currently `syntactic-unresolved`.
""",
    ),
    "CodeCall": (
        "A syntax-level Python call observation attributed to a lexical caller",
        """A `CodeCall` records one call expression observed inside a lexical callable. It is
evidence about syntax, not a resolved runtime dispatch edge.

## Frontmatter

- `type` — always `CodeCall`.
- `title` — caller and observed expression for display.
- `language`, `source_path`, `generated_by` — producer and provenance fields.
- `caller` — lexical caller qualified name.
- `caller_line_start` — line identifying the caller definition when same-name redefinitions exist.
- `callee` — final syntax-level callee name or expression text.
- `expression` — syntax-derived callable expression.
- `line_start`, `line_end` — source line range, stored as scalar strings.
- `resolution` — currently `syntactic-unresolved`.
- `candidate_targets` — optional name-matched navigation candidates; never authoritative dispatch
  claims.
""",
    ),
    "CodeProject": (
        "A Python project declaration observed in standard pyproject metadata",
        """A `CodeProject` represents the authored PEP 621 `[project]` table from
`pyproject.toml`. It records manifest evidence rather than the state of an installed environment.

## Frontmatter

- `type` — always `CodeProject`.
- `title`, `name` — the declared project name, or a deterministic fallback when absent.
- `ecosystem` — `python` for this recipe.
- `manifest_path` — currently `pyproject.toml`.
- `version`, `description`, `requires_python` — optional authored PEP 621 fields.
- `dynamic_fields` — optional fields declared dynamic by the project metadata.
- `dependency_groups` — optional groups that contain authored dependency declarations.
- `generated_by` — producer identity, currently `codebase-to-okf`.

A `CodeProject` does not claim that a wheel was built, a package was installed, or an environment
matches the manifest.
""",
    ),
    "CodeDependency": (
        "A PEP 508 dependency declaration preserved from Python project metadata",
        """A `CodeDependency` represents one authored requirement string from PEP 621 runtime or
optional dependencies. It preserves the original declaration and parsed navigation fields without
claiming installation or use.

## Frontmatter

- `type` — always `CodeDependency`.
- `title` — dependency name and declaration group.
- `ecosystem` — `python` for this recipe.
- `manifest_path` — currently `pyproject.toml`.
- `project`, `project_concept` — declaring project identity and generated project concept path.
- `group` — `runtime` or `optional:<name>`.
- `declaration` — the authored PEP 508 requirement string.
- `dependency_name` — parsed distribution name.
- `extras`, `specifier`, `marker`, `url` — optional parsed PEP 508 components.
- `resolution` — `manifest-declared`; this is a declaration status, not package resolution.
- `generated_by` — producer identity, currently `codebase-to-okf`.

Manifest declaration does not prove installation, source import, reachability, or runtime use.
""",
    ),
    "Spec": (
        "A document that describes one producer-defined concept type",
        """A `Spec` document records the intended meaning and frontmatter contract of one concept
type. The expected path is derived from the type name through the configured specification
template rather than declared in each concept.

## Frontmatter

- `type` — always `Spec`.
- `title` — the type documented by this specification.
- `description` — concise statement of that type's meaning.

The generated codebase bundle uses `docs/types/{slug}.md`. A spec documents intent; it does not by
itself prove semantic resolution of source-language behavior.
""",
    ),
}


class FinalizeError(ValueError):
    """Expected failure while finalizing a generated bundle."""

    @classmethod
    def missing_heading(cls, path: Path) -> FinalizeError:
        """Build an error for a malformed canonical scaffold."""
        return cls(f"init scaffold has no type heading: {path}")

    @classmethod
    def unknown_type(cls, concept_type: str) -> FinalizeError:
        """Build an error for producer vocabulary unknown to this skill."""
        return cls(f"no codebase-to-okf type semantics for {concept_type!r}")

    @classmethod
    def collisions(cls, values: list[object]) -> FinalizeError:
        """Build an error for canonical type-spec path collisions."""
        return cls(f"type-spec path collisions: {values}")

    @classmethod
    def no_fixed_point(cls) -> FinalizeError:
        """Build an error for a scaffold loop that never stabilizes."""
        return cls("type-spec scaffold did not reach a fixed point")

    @classmethod
    def invalid_bundle(cls, root: Path) -> FinalizeError:
        """Build an error for a missing or non-directory bundle root."""
        return cls(f"bundle is not a directory: {root}")


def _type_from_stub(path: Path) -> str:
    """Read the concept type from the heading emitted by canonical `init`."""
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            return line.removeprefix("# ").strip()
    raise FinalizeError.missing_heading(path)


def _render_spec(concept_type: str) -> str:
    """Render authored semantics after `init` has chosen the canonical path."""
    try:
        description, body = TYPE_SPECS[concept_type]
    except KeyError as exc:
        raise FinalizeError.unknown_type(concept_type) from exc
    return (
        "---\n"
        "type: Spec\n"
        f"title: {concept_type}\n"
        f"description: {description}\n"
        "---\n\n"
        f"# {concept_type}\n\n"
        f"{body.rstrip()}\n"
    )


def _scaffold_and_author(root: Path) -> list[str]:
    """Run canonical `init` to a fixed point, then author every created stub."""
    created: list[str] = []
    max_passes = len(TYPE_SPECS) + 2
    for _ in range(max_passes):
        payload = init_bundle(str(root), SPEC_TEMPLATE, write=True)
        specs = payload["specs"]
        collisions = list(specs["collisions"])
        if collisions:
            raise FinalizeError.collisions(collisions)
        batch = [str(item) for item in specs["created"]]
        if not batch:
            return sorted(created)
        for relative in batch:
            path = root / relative
            concept_type = _type_from_stub(path)
            path.write_text(_render_spec(concept_type), encoding="utf-8")
            created.append(relative)
    raise FinalizeError.no_fixed_point()


def _require_bundle(root: Path) -> None:
    """Require a usable bundle directory before entering the orchestration try block."""
    if not root.is_dir():
        raise FinalizeError.invalid_bundle(root)


def _print_errors(report: object) -> None:
    """Print validation errors without duplicating parser validation logic."""
    for violation in report.violations:
        if violation.severity.value == "error":
            print(
                f"{violation.path}: {violation.code}: {violation.message}",
                file=sys.stderr,
            )


def build_parser() -> argparse.ArgumentParser:
    """Build the finalizer CLI."""
    parser = argparse.ArgumentParser(
        description="Scaffold and author normative type specs for a generated codebase OKF bundle."
    )
    parser.add_argument("bundle", type=Path, help="generated codebase-to-OKF bundle")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Finalize one generated bundle and verify normative type-spec coverage."""
    args = build_parser().parse_args(argv)
    root = args.bundle.resolve()
    try:
        _require_bundle(root)
        created = _scaffold_and_author(root)
        report = validate_path(
            root,
            require_spec=SPEC_TEMPLATE,
            normative_spec=True,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not report.is_conformant:
        _print_errors(report)
        return 1

    print(
        json.dumps(
            {
                "bundle": str(root),
                "conformant": True,
                "created_specs": created,
                "spec_count": len(list((root / "docs" / "types").glob("*.md"))),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
