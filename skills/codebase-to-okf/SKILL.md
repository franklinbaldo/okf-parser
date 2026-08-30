---
name: codebase-to-okf
type: Skill
title: Codebase to OKF
description: >-
  Project a source code repository into a derived OKF bundle without adding language-specific
  parsers to okf-parser core. Use when an agent needs a compact, queryable representation of code
  structure for validation, graph inspection, schema work, retrieval, or downstream analysis.
when_to_use: >-
  Use when source code should become disposable OKF knowledge. The embedded reference recipe
  supports Python modules, classes, functions, methods, and import statements; adapt the frontend
  rather than the okf-parser core when another language or stronger semantic resolver is needed.
compatibility: >-
  Standalone execution requires uv and Python 3.12+. The embedded PEP 723 recipe installs
  okf-parser for itself and requires no credentials or network access after dependencies are
  available.
---

# Project a codebase into OKF

Treat the codebase as **authored source** and the generated OKF bundle as **derived, disposable
knowledge**.

Keep the boundary:

```text
source code
  → language-specific extraction in this skill
  → derived OKF concepts + Markdown relations
  → okf-parser validation / graph / relations / schema / search
```

Do not add Python AST, Tree-sitter grammars, LSP clients, compiler APIs, or producer-specific code
types to `okf-parser` merely to make this recipe richer. Promote only generic primitives that would
still belong in the parser if this skill disappeared.

## Reference workflow

1. Identify the source root and a separate output directory for the derived bundle.
2. For Python, materialize the uniquely marked recipe block below to a temporary `.py` file.
3. Run it with `uv run`.
4. Inspect the JSON summary and any validation diagnostics.
5. Use normal `okf-parser` surfaces on the generated bundle.
6. Delete/regenerate the bundle whenever the source or extraction policy changes.

For example:

```bash
uv run /tmp/codebase_to_okf.py ./src ./.derived/codebase-okf
okf-parser inventory ./.derived/codebase-okf
okf-parser graph ./.derived/codebase-okf
```

The first run refuses a non-empty destination. Regeneration is explicit:

```bash
uv run /tmp/codebase_to_okf.py ./src ./.derived/codebase-okf --force
```

Use repeated `--exclude-dir NAME` for project-specific generated/vendor directories.

## What the Python recipe asserts

The reference frontend uses only Python's standard-library AST. It emits these producer-defined
concept types:

- `CodeModule` — one per parsed `.py` file;
- `CodeClass` — class definitions;
- `CodeFunction` — functions whose immediate lexical parent is not a class;
- `CodeMethod` — functions whose immediate lexical parent is a class;
- `CodeImport` — `import` and `from ... import ...` statements.

It records source-relative path, qualified name and source line range where structurally known.
Generated Markdown links connect observations to their module and nested symbols to a generated
parent when that parent is structurally known.

It deliberately does **not** claim that imports resolve to runtime modules, that a name identifies a
unique callable, or that a syntactic call is a resolved call-graph edge. Add those facts only with a
frontend capable of supporting them.

## Embedded executable recipe

Materialize exactly the fenced block following the marker to a temporary Python file. PEP 723 keeps
its dependencies local to the recipe instead of changing the `okf-parser` package dependency set.

<!-- recipe:python-codebase-to-okf -->
```python
# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.2,<0.46",
# ]
# ///
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from okf_parser import validate_path

DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "__pycache__",
        "build",
        "dist",
        "node_modules",
        "venv",
    }
)


@dataclass(frozen=True)
class Symbol:
    concept_type: str
    kind: str
    name: str
    qualname: str
    line_start: int
    line_end: int
    parent_qualname: str | None
    parent_line_start: int | None


@dataclass(frozen=True)
class ImportRecord:
    targets: tuple[str, ...]
    line_start: int
    line_end: int


@dataclass(frozen=True)
class ModuleRecord:
    source_path: str
    module_name: str
    symbols: tuple[Symbol, ...]
    imports: tuple[ImportRecord, ...]


class PythonFacts(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: list[tuple[str, str, int]] = []
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRecord] = []

    def _qualname(self, name: str) -> str:
        return ".".join([*(item[1] for item in self.scope), name])

    def _parent_qualname(self) -> str | None:
        if not self.scope:
            return None
        return ".".join(item[1] for item in self.scope)

    def _parent_line_start(self) -> int | None:
        return self.scope[-1][2] if self.scope else None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = self._qualname(node.name)
        self.symbols.append(
            Symbol(
                concept_type="CodeClass",
                kind="class",
                name=node.name,
                qualname=qualname,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                parent_qualname=self._parent_qualname(),
                parent_line_start=self._parent_line_start(),
            )
        )
        self.scope.append(("class", node.name, node.lineno))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        is_method = bool(self.scope and self.scope[-1][0] == "class")
        qualname = self._qualname(node.name)
        self.symbols.append(
            Symbol(
                concept_type="CodeMethod" if is_method else "CodeFunction",
                kind="method" if is_method else "function",
                name=node.name,
                qualname=qualname,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                parent_qualname=self._parent_qualname(),
                parent_line_start=self._parent_line_start(),
            )
        )
        self.scope.append(("function", node.name, node.lineno))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        self.imports.append(
            ImportRecord(
                targets=tuple(alias.name for alias in node.names),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level + (node.module or "")
        separator = "" if not prefix or prefix.endswith(".") else "."
        targets = tuple(
            f"{prefix}{separator}{alias.name}" if prefix else alias.name
            for alias in node.names
        )
        self.imports.append(
            ImportRecord(
                targets=targets,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )


def module_name(path: PurePosixPath) -> str:
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or path.stem


def slug(value: str) -> str:
    compact = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return compact[:72] or "item"


def stable_filename(label: str, identity: str) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:10]
    return f"{slug(label)}-{digest}.md"


def is_under(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def discover_python_files(
    source_root: Path,
    output_root: Path,
    ignored_dirs: frozenset[str],
) -> list[Path]:
    output_resolved = output_root.resolve()
    files: list[Path] = []
    for path in source_root.rglob("*.py"):
        resolved = path.resolve()
        if is_under(resolved, output_resolved):
            continue
        relative = path.relative_to(source_root)
        if any(part in ignored_dirs for part in relative.parts[:-1]):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(source_root).as_posix())


def parse_modules(source_root: Path, paths: list[Path]) -> tuple[ModuleRecord, ...]:
    records: list[ModuleRecord] = []
    errors: list[str] = []
    for path in paths:
        relative = PurePosixPath(path.relative_to(source_root).as_posix())
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(relative))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: {exc}")
            continue
        visitor = PythonFacts()
        visitor.visit(tree)
        records.append(
            ModuleRecord(
                source_path=relative.as_posix(),
                module_name=module_name(relative),
                symbols=tuple(visitor.symbols),
                imports=tuple(visitor.imports),
            )
        )
    if errors:
        message = "\n".join(f"- {item}" for item in errors)
        raise ValueError(f"source parsing failed; no output was written:\n{message}")
    return tuple(records)


def frontmatter_text(fields: dict[str, str | list[str]]) -> str:
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def write_concept(path: Path, fields: dict[str, str | list[str]], body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{frontmatter_text(fields)}\n---\n\n{body.rstrip()}\n"
    path.write_text(content, encoding="utf-8")


def prepare_output(source_root: Path, output_root: Path, force: bool) -> None:
    source_resolved = source_root.resolve()
    output_resolved = output_root.resolve()
    filesystem_root = Path(output_resolved.anchor)

    if output_resolved == filesystem_root:
        raise ValueError("refusing to use a filesystem root as output")
    if output_resolved == source_resolved or is_under(source_resolved, output_resolved):
        raise ValueError("output must not be the source root or one of its ancestors")

    if output_root.exists():
        if not output_root.is_dir():
            raise ValueError(f"output exists and is not a directory: {output_root}")
        if any(output_root.iterdir()):
            if not force:
                raise ValueError("output is not empty; pass --force to replace it")
            shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)


def emit_bundle(output_root: Path, modules: tuple[ModuleRecord, ...]) -> dict[str, int]:
    module_files: dict[str, str] = {}
    symbol_files: dict[tuple[str, str, int], str] = {}
    import_files: dict[tuple[str, int, tuple[str, ...]], str] = {}

    for module in modules:
        module_files[module.source_path] = stable_filename(
            PurePosixPath(module.source_path).stem,
            f"module|{module.source_path}",
        )
        for symbol in module.symbols:
            symbol_files[(module.source_path, symbol.qualname, symbol.line_start)] = stable_filename(
                f"{PurePosixPath(module.source_path).stem}-{symbol.qualname}",
                (
                    f"{symbol.concept_type}|{module.source_path}|{symbol.qualname}|"
                    f"{symbol.line_start}"
                ),
            )
        for item in module.imports:
            key = (module.source_path, item.line_start, item.targets)
            import_files[key] = stable_filename(
                f"{PurePosixPath(module.source_path).stem}-import",
                f"import|{module.source_path}|{item.line_start}|{'|'.join(item.targets)}",
            )

    index_lines = [
        "# Codebase OKF projection",
        "",
        "Derived from Python source by the `codebase-to-okf` recipe.",
        "",
        "## Modules",
        "",
    ]

    for module in modules:
        module_file = module_files[module.source_path]
        index_lines.append(f"- [{module.source_path}](modules/{module_file})")

        module_body = [
            f"# {module.source_path}",
            "",
            f"Python module `{module.module_name}`.",
        ]
        if module.symbols:
            module_body.extend(["", "## Symbols", ""])
            for symbol in module.symbols:
                target = symbol_files[(module.source_path, symbol.qualname, symbol.line_start)]
                module_body.append(
                    f"- [{symbol.qualname}](../symbols/{target}) — {symbol.kind}"
                )
        if module.imports:
            module_body.extend(["", "## Imports", ""])
            for item in module.imports:
                target = import_files[(module.source_path, item.line_start, item.targets)]
                label = ", ".join(item.targets)
                module_body.append(f"- [{label}](../imports/{target})")

        write_concept(
            output_root / "modules" / module_file,
            {
                "type": "CodeModule",
                "title": module.source_path,
                "language": "python",
                "module": module.module_name,
                "source_path": module.source_path,
                "generated_by": "codebase-to-okf",
            },
            "\n".join(module_body),
        )

        for symbol in module.symbols:
            symbol_file = symbol_files[
                (module.source_path, symbol.qualname, symbol.line_start)
            ]
            body = [
                f"# {symbol.qualname}",
                "",
                f"Defined in [{module.source_path}](../modules/{module_file}).",
            ]
            if symbol.parent_qualname and symbol.parent_line_start is not None:
                parent_file = symbol_files.get(
                    (module.source_path, symbol.parent_qualname, symbol.parent_line_start)
                )
                if parent_file is not None:
                    body.extend(
                        [
                            "",
                            f"Parent: [{symbol.parent_qualname}](../symbols/{parent_file}).",
                        ]
                    )
            write_concept(
                output_root / "symbols" / symbol_file,
                {
                    "type": symbol.concept_type,
                    "title": symbol.qualname,
                    "language": "python",
                    "name": symbol.name,
                    "qualname": symbol.qualname,
                    "source_path": module.source_path,
                    "line_start": str(symbol.line_start),
                    "line_end": str(symbol.line_end),
                    "generated_by": "codebase-to-okf",
                },
                "\n".join(body),
            )

        for item in module.imports:
            import_file = import_files[(module.source_path, item.line_start, item.targets)]
            write_concept(
                output_root / "imports" / import_file,
                {
                    "type": "CodeImport",
                    "title": ", ".join(item.targets),
                    "language": "python",
                    "source_path": module.source_path,
                    "targets": list(item.targets),
                    "line_start": str(item.line_start),
                    "line_end": str(item.line_end),
                    "generated_by": "codebase-to-okf",
                },
                "\n".join(
                    [
                        f"# {', '.join(item.targets)}",
                        "",
                        f"Declared in [{module.source_path}](../modules/{module_file}).",
                    ]
                ),
            )

    (output_root / "index.md").write_text(
        "\n".join(index_lines).rstrip() + "\n",
        encoding="utf-8",
    )

    symbol_count = sum(len(module.symbols) for module in modules)
    import_count = sum(len(module.imports) for module in modules)
    return {
        "modules": len(modules),
        "symbols": symbol_count,
        "imports": import_count,
        "concepts": len(modules) + symbol_count + import_count,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project a Python codebase into a disposable OKF bundle.",
    )
    parser.add_argument("source", type=Path, help="Python source tree")
    parser.add_argument("output", type=Path, help="OKF bundle destination")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace a non-empty output directory",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="additional directory name to exclude; may repeat",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    source_root = args.source.resolve()
    output_root = args.output.resolve()

    try:
        if not source_root.is_dir():
            raise ValueError(f"source is not a directory: {source_root}")
        ignored_dirs = frozenset({*DEFAULT_IGNORED_DIRS, *args.exclude_dir})
        paths = discover_python_files(source_root, output_root, ignored_dirs)
        if not paths:
            raise ValueError("no Python source files found")
        modules = parse_modules(source_root, paths)
        prepare_output(source_root, output_root, args.force)
        counts = emit_bundle(output_root, modules)
        report = validate_path(output_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not report.is_conformant:
        for violation in report.violations:
            if violation.severity.value == "error":
                print(
                    f"{violation.path}: {violation.code}: {violation.message}",
                    file=sys.stderr,
                )
        return 1

    warnings = sum(
        1 for violation in report.violations if violation.severity.value == "warning"
    )
    print(
        json.dumps(
            {
                "source_files": len(paths),
                **counts,
                "warnings": warnings,
                "conformant": True,
                "output": str(output_root),
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

## Adapting the recipe

For another language or a stronger code-intelligence frontend, keep the output boundary stable and
replace the extraction layer. Good candidates include Tree-sitter, SCIP/LSIF, an LSP, compiler
metadata, or a language-native parser.

Prefer the following sequence:

1. define which facts the frontend can support without guessing;
2. keep source-relative provenance for each emitted fact;
3. choose producer-defined concept types and relations;
4. put frontend-only dependencies in that recipe's PEP 723 block;
5. emit ordinary OKF Markdown;
6. let `okf-parser` validate and consume the result through its existing generic API.

Do not copy a richer extractor's claims blindly. A Tree-sitter syntax tree and a compiler-resolved
symbol graph support different assertions.

## Guardrails

- Never treat generated OKF as more authoritative than the source it projects.
- Do not execute an untrusted skill or embedded script merely because it is formatted as PEP 723.
- Do not put absolute local paths, timestamps, credentials, or machine-specific state into canonical
  generated concepts.
- Do not use `--force` with a destination you have not verified; the recipe deliberately deletes and
  rebuilds a non-empty destination when force is explicit.
- Do not turn unresolved names or dynamic Python behavior into hard graph edges without evidence.
- Do not add source-specific dependencies to `okf-parser` core to save recipe setup.

## Definition of done

A codebase projection is complete when:

1. extraction succeeds without source parse errors;
2. generated paths/content are deterministic for unchanged input;
3. every generated concept retains source-relative provenance;
4. generated Markdown links resolve inside the bundle when they claim a structural relation;
5. `okf-parser` reports the generated bundle conformant;
6. semantic claims do not exceed what the chosen frontend can establish;
7. the generated bundle can be deleted and reproduced from source.
