# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.4,<0.46",
# ]
# ///
# ruff: noqa: T201
"""Add conservative source-tree import-resolution claims to a codebase OKF bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from okf_parser import load_bundle, validate_path
from okf_parser.service import init_bundle

SPEC_TEMPLATE = "docs/types/{slug}.md"
RESOLUTION_TYPE = "CodeImportResolution"
RESOLUTION_METHOD = "projected-module-prefix-v1"


class ResolutionError(ValueError):
    """Expected user-facing failure while resolving projected imports."""


@dataclass(frozen=True)
class ModuleTarget:
    """One uniquely matched projected module for an import target."""

    target: str
    absolute_target: str
    module: str
    path: str


@dataclass(frozen=True)
class ImportObservation:
    """One syntax-level import concept loaded from the generated bundle."""

    path: str
    title: str
    source_path: str
    targets: tuple[str, ...]


def _fail(message: str) -> None:
    raise ResolutionError(message)


def _rows(root: Path) -> list[dict[str, Any]]:
    bundle = load_bundle(root)
    if not bundle.is_conformant:
        _fail("bundle is not conformant")
    result: list[dict[str, Any]] = []
    for row in bundle.concepts.execute().to_dict(orient="records"):
        frontmatter = json.loads(row["frontmatter_json"])
        result.append(
            {
                "path": row["path"],
                "type": row["concept_type"],
                "title": row["title"],
                **frontmatter,
            }
        )
    return result


def _module_package(module: str, source_path: str) -> tuple[str, ...]:
    parts = tuple(part for part in module.split(".") if part)
    if PurePosixPath(source_path).name == "__init__.py":
        return parts
    return parts[:-1]


def _absolute_target(target: str, source_module: str, source_path: str) -> str | None:
    if not target.startswith("."):
        return target
    level = len(target) - len(target.lstrip("."))
    remainder = target[level:]
    package = _module_package(source_module, source_path)
    climb = level - 1
    if climb > len(package):
        return None
    base = package[: len(package) - climb] if climb else package
    tail = tuple(part for part in remainder.split(".") if part)
    resolved = (*base, *tail)
    return ".".join(resolved) if resolved else None


def _match_module(
    target: str,
    source_module: str,
    source_path: str,
    modules: dict[str, list[str]],
) -> ModuleTarget | None:
    absolute = _absolute_target(target, source_module, source_path)
    if absolute is None:
        return None
    parts = absolute.split(".")
    for size in range(len(parts), 0, -1):
        candidate = ".".join(parts[:size])
        paths = modules.get(candidate, [])
        if len(paths) == 1:
            return ModuleTarget(target, absolute, candidate, paths[0])
    return None


def _imports_and_modules(
    rows: list[dict[str, Any]],
) -> tuple[list[ImportObservation], dict[str, list[str]], dict[str, str]]:
    imports: list[ImportObservation] = []
    modules: dict[str, list[str]] = {}
    module_by_source: dict[str, str] = {}
    for row in rows:
        if row["type"] == "CodeModule":
            module = str(row.get("module", ""))
            source_path = str(row.get("source_path", ""))
            if module:
                modules.setdefault(module, []).append(str(row["path"]))
                module_by_source[source_path] = module
        elif row["type"] == "CodeImport":
            imports.append(
                ImportObservation(
                    path=str(row["path"]),
                    title=str(row.get("title", "")),
                    source_path=str(row.get("source_path", "")),
                    targets=tuple(str(item) for item in row.get("targets", [])),
                )
            )
    return imports, modules, module_by_source


def _stable_filename(import_path: str) -> str:
    digest = hashlib.sha256(import_path.encode()).hexdigest()[:12]
    return f"import-resolution-{digest}.md"


def _frontmatter(fields: dict[str, object]) -> str:
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def _relative_link(from_path: str, to_path: str) -> str:
    start = PurePosixPath(from_path).parent.as_posix()
    return posixpath.relpath(to_path, start=start)


def _render_resolution(
    output_path: str,
    observation: ImportObservation,
    matches: list[ModuleTarget],
    unresolved: list[str],
) -> str:
    status = "source-tree-resolved" if not unresolved else "source-tree-partial"
    resolved_modules = list(dict.fromkeys(item.module for item in matches))
    fields: dict[str, object] = {
        "type": RESOLUTION_TYPE,
        "title": f"{observation.source_path}: {observation.title}",
        "source_path": observation.source_path,
        "source_import": observation.path,
        "targets": list(observation.targets),
        "resolved_modules": resolved_modules,
        "resolution": status,
        "resolution_method": RESOLUTION_METHOD,
        "generated_by": "codebase-to-okf",
    }
    if unresolved:
        fields["unresolved_targets"] = unresolved

    body = [
        f"# {observation.source_path}: {observation.title}",
        "",
        f"Observed import: [{observation.path}]({_relative_link(output_path, observation.path)}).",
        "",
        f"Resolution status: `{status}` via `{RESOLUTION_METHOD}`.",
        "",
        "This claim only says that import text maps uniquely to projected modules in this source tree. ",
        "It does not prove Python runtime import selection, import hooks, monkey-patching, or symbol dispatch.",
        "",
        "## Projected module matches",
        "",
    ]
    for match in matches:
        link = _relative_link(output_path, match.path)
        body.append(
            f"- `{match.target}` → [{match.module}]({link}) from normalized target "
            f"`{match.absolute_target}`"
        )
    if unresolved:
        body.extend(["", "## Unresolved targets", ""])
        body.extend(f"- `{target}`" for target in unresolved)

    return f"---\n{_frontmatter(fields)}\n---\n\n{'\n'.join(body).rstrip()}\n"


def _is_own_resolution(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")
        _, raw_frontmatter, _ = text.split("---", 2)
        fields = json.loads(raw_frontmatter.strip())
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return fields.get("type") == RESOLUTION_TYPE and fields.get("generated_by") == "codebase-to-okf"


def _clear_previous(root: Path) -> None:
    directory = root / "resolutions"
    if not directory.is_dir():
        return
    for path in sorted(directory.glob("import-resolution-*.md")):
        if _is_own_resolution(path):
            path.unlink()


def _write_resolutions(
    root: Path,
    imports: list[ImportObservation],
    modules: dict[str, list[str]],
    module_by_source: dict[str, str],
) -> tuple[int, int, int]:
    resolved = partial = unresolved_only = 0
    directory = root / "resolutions"
    directory.mkdir(parents=True, exist_ok=True)

    for observation in imports:
        source_module = module_by_source.get(observation.source_path, "")
        matches: list[ModuleTarget] = []
        unresolved: list[str] = []
        for target in observation.targets:
            match = _match_module(
                target,
                source_module,
                observation.source_path,
                modules,
            )
            if match is None:
                unresolved.append(target)
            else:
                matches.append(match)

        if not matches:
            unresolved_only += 1
            continue
        if unresolved:
            partial += 1
        else:
            resolved += 1

        filename = _stable_filename(observation.path)
        relative = f"resolutions/{filename}"
        (root / relative).write_text(
            _render_resolution(relative, observation, matches, unresolved),
            encoding="utf-8",
        )

    return resolved, partial, unresolved_only


def _author_spec(root: Path) -> list[str]:
    payload = init_bundle(str(root), SPEC_TEMPLATE, write=True)
    collisions = list(payload["specs"]["collisions"])
    if collisions:
        _fail(f"type-spec path collisions: {collisions}")
    created = [str(item) for item in payload["specs"]["created"]]
    for relative in created:
        path = root / relative
        if path.name != "codeimportresolution.md":
            _fail(f"unexpected missing type specification: {relative}")
        path.write_text(
            "---\n"
            "type: Spec\n"
            "title: CodeImportResolution\n"
            "description: A conservative source-tree mapping from a syntax-level import to projected modules\n"
            "---\n\n"
            "# CodeImportResolution\n\n"
            "A `CodeImportResolution` is a derived claim about an existing `CodeImport` observation. "
            "It records only module identities that can be mapped uniquely inside the projected source tree.\n\n"
            "## Frontmatter\n\n"
            "- `type` — always `CodeImportResolution`.\n"
            "- `source_import` — path of the immutable syntax-level `CodeImport` observation.\n"
            "- `source_path` — source-relative file containing that import.\n"
            "- `targets` — original syntax-derived import targets.\n"
            "- `resolved_modules` — projected module names matched uniquely by the resolver.\n"
            "- `unresolved_targets` — optional targets without a unique projected-module match.\n"
            "- `resolution` — `source-tree-resolved` or `source-tree-partial`.\n"
            f"- `resolution_method` — currently `{RESOLUTION_METHOD}`.\n\n"
            "Source-tree resolution is not a claim about runtime import selection, import hooks, "
            "symbol binding, or call dispatch.\n",
            encoding="utf-8",
        )
    return created


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Resolve only uniquely matched local imports in a generated codebase OKF bundle."
    )
    parser.add_argument("bundle", type=Path, help="normatively finalized codebase OKF bundle")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.bundle.resolve()
    try:
        before = validate_path(root, require_spec=SPEC_TEMPLATE, normative_spec=True)
        if not before.is_conformant:
            _fail("bundle must be normatively conformant before resolution")
        rows = _rows(root)
        imports, modules, module_by_source = _imports_and_modules(rows)
        _clear_previous(root)
        resolved, partial, unresolved_only = _write_resolutions(
            root,
            imports,
            modules,
            module_by_source,
        )
        created_specs = _author_spec(root)
        after = validate_path(root, require_spec=SPEC_TEMPLATE, normative_spec=True)
        if not after.is_conformant:
            _fail("resolved bundle is not normatively conformant")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "imports": len(imports),
                "resolved": resolved,
                "partial": partial,
                "unresolved": unresolved_only,
                "resolution_concepts": resolved + partial,
                "created_specs": created_specs,
                "resolution_method": RESOLUTION_METHOD,
                "conformant": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
