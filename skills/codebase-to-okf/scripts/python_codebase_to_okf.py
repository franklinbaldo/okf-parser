# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.2,<0.46",
# ]
# ///
# ruff: noqa
"""Project Python source into a deterministic, disposable OKF bundle."""

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
class Parameter:
    """One Python function parameter observed in source syntax."""

    name: str
    kind: str
    annotation: str
    default: str

    def render(self) -> str:
        """Render a compact deterministic signature fragment."""
        text = self.name
        if self.annotation:
            text += f": {self.annotation}"
        if self.default:
            text += f" = {self.default}"
        return text


@dataclass(frozen=True)
class Field:
    """One class-level field assignment observed in source syntax."""

    name: str
    annotation: str
    default: str
    line: int


@dataclass(frozen=True)
class CallObservation:
    """One syntactic call expression attributed to its lexical callable."""

    expression: str
    callee: str
    line_start: int
    line_end: int


@dataclass(frozen=True)
class Symbol:
    """A source symbol plus syntax-level metadata useful to an agent."""

    concept_type: str
    kind: str
    name: str
    qualname: str
    line_start: int
    line_end: int
    parent_qualname: str | None
    parent_line_start: int | None
    signature: str
    parameters: tuple[Parameter, ...]
    return_annotation: str
    docstring: str
    decorators: tuple[str, ...]
    bases: tuple[str, ...]
    fields: tuple[Field, ...]
    calls: tuple[CallObservation, ...]


@dataclass(frozen=True)
class ImportRecord:
    """One import statement preserved as source-level evidence."""

    targets: tuple[str, ...]
    line_start: int
    line_end: int


@dataclass(frozen=True)
class ModuleRecord:
    """All syntax facts extracted from one Python module."""

    source_path: str
    module_name: str
    docstring: str
    symbols: tuple[Symbol, ...]
    imports: tuple[ImportRecord, ...]


def _unparse(node: ast.AST | None) -> str:
    """Return deterministic source-like text for an AST node."""
    return ast.unparse(node) if node is not None else ""


def _decorators(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[str, ...]:
    """Return source-like decorator expressions without leading at signs."""
    return tuple(_unparse(item) for item in node.decorator_list)


def _parameters(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[Parameter, ...]:
    """Normalize Python's argument model into ordered parameter records."""
    args = node.args
    positional = [*args.posonlyargs, *args.args]
    defaults_offset = len(positional) - len(args.defaults)
    parameters: list[Parameter] = []

    for index, item in enumerate(positional):
        default = (
            _unparse(args.defaults[index - defaults_offset]) if index >= defaults_offset else ""
        )
        kind = "positional_only" if index < len(args.posonlyargs) else "positional_or_keyword"
        parameters.append(Parameter(item.arg, kind, _unparse(item.annotation), default))

    if args.vararg is not None:
        parameters.append(
            Parameter(
                f"*{args.vararg.arg}",
                "var_positional",
                _unparse(args.vararg.annotation),
                "",
            )
        )

    for item, default_node in zip(args.kwonlyargs, args.kw_defaults, strict=True):
        parameters.append(
            Parameter(
                item.arg,
                "keyword_only",
                _unparse(item.annotation),
                _unparse(default_node),
            )
        )

    if args.kwarg is not None:
        parameters.append(
            Parameter(
                f"**{args.kwarg.arg}",
                "var_keyword",
                _unparse(args.kwarg.annotation),
                "",
            )
        )

    return tuple(parameters)


def _signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    parameters: tuple[Parameter, ...],
    return_annotation: str,
) -> str:
    """Render a compact semantic signature without embedding the function body."""
    parts: list[str] = []
    posonly_count = sum(item.kind == "positional_only" for item in parameters)
    has_vararg = any(item.kind == "var_positional" for item in parameters)
    keyword_only_started = False

    for index, item in enumerate(parameters):
        if item.kind == "keyword_only" and not has_vararg and not keyword_only_started:
            parts.append("*")
            keyword_only_started = True
        parts.append(item.render())
        if posonly_count and index + 1 == posonly_count:
            parts.append("/")

    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    signature = f"{prefix} {node.name}({', '.join(parts)})"
    if return_annotation:
        signature += f" -> {return_annotation}"
    return signature


def _class_fields(node: ast.ClassDef) -> tuple[Field, ...]:
    """Collect direct class-body assignments without descending into methods."""
    fields: list[Field] = []
    for item in node.body:
        if isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
            fields.append(
                Field(
                    item.target.id,
                    _unparse(item.annotation),
                    _unparse(item.value),
                    item.lineno,
                )
            )
        elif isinstance(item, ast.Assign):
            value = _unparse(item.value)
            for target in item.targets:
                if isinstance(target, ast.Name):
                    fields.append(Field(target.id, "", value, item.lineno))
    return tuple(fields)


class CallCollector(ast.NodeVisitor):
    """Collect calls without attributing nested definitions to their parent."""

    def __init__(self) -> None:
        self.calls: list[CallObservation] = []

    def visit_Call(self, node: ast.Call) -> None:
        """Record the call expression and continue through its arguments/value."""
        expression = _unparse(node.func)
        if isinstance(node.func, ast.Name):
            callee = node.func.id
        elif isinstance(node.func, ast.Attribute):
            callee = node.func.attr
        else:
            callee = expression
        self.calls.append(
            CallObservation(
                expression=expression,
                callee=callee,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Do not descend into a nested function definition."""

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Do not descend into a nested async function definition."""

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Do not descend into a nested class definition."""


def _calls(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> tuple[CallObservation, ...]:
    """Collect calls directly attributable to a function's executable body."""
    collector = CallCollector()
    for statement in node.body:
        collector.visit(statement)
    return tuple(collector.calls)


class PythonFacts(ast.NodeVisitor):
    """Extract syntax-grounded code facts while preserving lexical provenance."""

    def __init__(self) -> None:
        self.scope: list[tuple[str, str, int]] = []
        self.symbols: list[Symbol] = []
        self.imports: list[ImportRecord] = []

    def _qualname(self, name: str) -> str:
        return ".".join([*(item[1] for item in self.scope), name])

    def _parent_qualname(self) -> str | None:
        return ".".join(item[1] for item in self.scope) if self.scope else None

    def _parent_line_start(self) -> int | None:
        return self.scope[-1][2] if self.scope else None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Record a class and then inspect nested definitions."""
        qualname = self._qualname(node.name)
        bases = tuple(_unparse(item) for item in node.bases)
        signature = f"class {node.name}({', '.join(bases)})" if bases else f"class {node.name}"
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
                signature=signature,
                parameters=(),
                return_annotation="",
                docstring=ast.get_docstring(node, clean=False) or "",
                decorators=_decorators(node),
                bases=bases,
                fields=_class_fields(node),
                calls=(),
            )
        )
        self.scope.append(("class", node.name, node.lineno))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Record a synchronous function or method."""
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Record an asynchronous function or method."""
        self._visit_function(node)

    def _visit_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        is_method = bool(self.scope and self.scope[-1][0] == "class")
        parameters = _parameters(node)
        return_annotation = _unparse(node.returns)
        self.symbols.append(
            Symbol(
                concept_type="CodeMethod" if is_method else "CodeFunction",
                kind="method" if is_method else "function",
                name=node.name,
                qualname=self._qualname(node.name),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                parent_qualname=self._parent_qualname(),
                parent_line_start=self._parent_line_start(),
                signature=_signature(node, parameters, return_annotation),
                parameters=parameters,
                return_annotation=return_annotation,
                docstring=ast.get_docstring(node, clean=False) or "",
                decorators=_decorators(node),
                bases=(),
                fields=(),
                calls=_calls(node),
            )
        )
        self.scope.append(("function", node.name, node.lineno))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Import(self, node: ast.Import) -> None:
        """Record an import statement without claiming resolution."""
        self.imports.append(
            ImportRecord(
                targets=tuple(alias.name for alias in node.names),
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Record a from-import statement without claiming resolution."""
        prefix = "." * node.level + (node.module or "")
        separator = "" if not prefix or prefix.endswith(".") else "."
        targets = tuple(
            f"{prefix}{separator}{alias.name}" if prefix else alias.name for alias in node.names
        )
        self.imports.append(
            ImportRecord(
                targets=targets,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
            )
        )


def module_name(path: PurePosixPath) -> str:
    """Convert a source-relative path into a dotted Python module name."""
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts) or path.stem


def slug(value: str) -> str:
    """Create a deterministic human-readable filename component."""
    compact = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return compact[:72] or "item"


def stable_filename(label: str, identity: str) -> str:
    """Create a collision-resistant deterministic Markdown filename."""
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    return f"{slug(label)}-{digest}.md"


def is_under(path: Path, parent: Path) -> bool:
    """Return whether path is equal to or below parent."""
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
    """Discover deterministic source input while skipping generated/vendor trees."""
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


def parse_modules(
    source_root: Path,
    paths: list[Path],
) -> tuple[ModuleRecord, ...]:
    """Parse every source file before output mutation to preserve atomic failure."""
    records: list[ModuleRecord] = []
    errors: list[str] = []
    for path in paths:
        relative = PurePosixPath(path.relative_to(source_root).as_posix())
        try:
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(relative))
        except (SyntaxError, UnicodeDecodeError) as exc:
            errors.append(f"{relative}: {exc}")
            continue
        visitor = PythonFacts()
        visitor.visit(tree)
        records.append(
            ModuleRecord(
                source_path=relative.as_posix(),
                module_name=module_name(relative),
                docstring=ast.get_docstring(tree, clean=False) or "",
                symbols=tuple(visitor.symbols),
                imports=tuple(visitor.imports),
            )
        )
    if errors:
        message = "\n".join(f"- {item}" for item in errors)
        raise ValueError(f"source parsing failed; no output was written:\n{message}")
    return tuple(records)


def frontmatter_text(fields: dict[str, str | list[str]]) -> str:
    """Serialize JSON, a strict YAML subset, for deterministic frontmatter."""
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def write_concept(
    path: Path,
    fields: dict[str, str | list[str]],
    body: str,
) -> None:
    """Write one deterministic OKF Markdown concept."""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = f"---\n{frontmatter_text(fields)}\n---\n\n{body.rstrip()}\n"
    path.write_text(content, encoding="utf-8")


def prepare_output(
    source_root: Path,
    output_root: Path,
    *,
    force: bool,
) -> None:
    """Validate destructive boundaries and prepare an empty destination."""
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


def _parameter_lines(parameters: tuple[Parameter, ...]) -> list[str]:
    """Render parameter metadata as an agent-readable table."""
    if not parameters:
        return []
    lines = [
        "",
        "## Parameters",
        "",
        "| Name | Kind | Type | Default |",
        "| --- | --- | --- | --- |",
    ]
    for item in parameters:
        lines.append(
            f"| `{item.name}` | `{item.kind}` | `{item.annotation or '—'}` | "
            f"`{item.default or '—'}` |"
        )
    return lines


def _field_lines(fields: tuple[Field, ...]) -> list[str]:
    """Render class fields as compact structured evidence."""
    if not fields:
        return []
    lines = [
        "",
        "## Fields",
        "",
        "| Name | Type | Default | Line |",
        "| --- | --- | --- | ---: |",
    ]
    for item in fields:
        lines.append(
            f"| `{item.name}` | `{item.annotation or '—'}` | "
            f"`{item.default or '—'}` | {item.line} |"
        )
    return lines


def _symbol_body(
    symbol: Symbol,
    module_path: str,
    module_file: str,
    parent_file: str | None,
) -> str:
    """Render rich symbol context without duplicating the full source body."""
    body = [
        f"# {symbol.qualname}",
        "",
        f"Defined in [{module_path}](../modules/{module_file}) at lines "
        f"{symbol.line_start}–{symbol.line_end}.",
        "",
        "## Signature",
        "",
        f"`{symbol.signature}`",
    ]
    if parent_file is not None and symbol.parent_qualname is not None:
        body.extend(["", f"Parent: [{symbol.parent_qualname}](../symbols/{parent_file})."])
    if symbol.docstring:
        body.extend(["", "## Docstring", "", symbol.docstring])
    if symbol.decorators:
        body.extend(["", "## Decorators", "", *[f"- `{item}`" for item in symbol.decorators]])
    if symbol.bases:
        body.extend(["", "## Bases", "", *[f"- `{item}`" for item in symbol.bases]])
    body.extend(_parameter_lines(symbol.parameters))
    if symbol.return_annotation:
        body.extend(["", "## Returns", "", f"`{symbol.return_annotation}`"])
    body.extend(_field_lines(symbol.fields))
    if symbol.calls:
        body.extend(
            [
                "",
                "## Syntactic calls",
                "",
                "These are observed call expressions, not resolved dispatch edges.",
                "",
            ]
        )
        body.extend(
            f"- `{item.expression}` (callee `{item.callee}`, lines "
            f"{item.line_start}–{item.line_end})"
            for item in symbol.calls
        )
    return "\n".join(body)


def emit_bundle(
    output_root: Path,
    modules: tuple[ModuleRecord, ...],
) -> dict[str, int]:
    """Emit a conformant projection plus explicit syntax-level call observations."""
    module_files: dict[str, str] = {}
    symbol_files: dict[tuple[str, str, int], str] = {}
    import_files: dict[tuple[str, int, tuple[str, ...]], str] = {}
    call_files: dict[tuple[str, str, int, int, str], str] = {}
    candidates_by_name: dict[str, list[tuple[str, Symbol]]] = {}

    for module in modules:
        module_files[module.source_path] = stable_filename(
            PurePosixPath(module.source_path).stem,
            f"module|{module.source_path}",
        )
        for symbol in module.symbols:
            symbol_files[(module.source_path, symbol.qualname, symbol.line_start)] = (
                stable_filename(
                    f"{PurePosixPath(module.source_path).stem}-{symbol.qualname}",
                    f"{symbol.concept_type}|{module.source_path}|"
                    f"{symbol.qualname}|{symbol.line_start}",
                )
            )
            candidates_by_name.setdefault(symbol.name, []).append((module.source_path, symbol))
            for call in symbol.calls:
                key = (
                    module.source_path,
                    symbol.qualname,
                    symbol.line_start,
                    call.line_start,
                    call.expression,
                )
                call_files[key] = stable_filename(
                    f"{PurePosixPath(module.source_path).stem}-"
                    f"{symbol.qualname}-call-{call.callee}",
                    "|".join(str(item) for item in ("call", *key)),
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
        if module.docstring:
            module_body.extend(["", "## Docstring", "", module.docstring])
        if module.symbols:
            module_body.extend(["", "## Symbols", ""])
            for symbol in module.symbols:
                target = symbol_files[(module.source_path, symbol.qualname, symbol.line_start)]
                module_body.append(f"- [{symbol.qualname}](../symbols/{target}) — {symbol.kind}")
        if module.imports:
            module_body.extend(["", "## Imports", ""])
            for item in module.imports:
                target = import_files[(module.source_path, item.line_start, item.targets)]
                module_body.append(f"- [{', '.join(item.targets)}](../imports/{target})")

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
            symbol_file = symbol_files[(module.source_path, symbol.qualname, symbol.line_start)]
            parent_file = None
            if symbol.parent_qualname and symbol.parent_line_start is not None:
                parent_file = symbol_files.get(
                    (
                        module.source_path,
                        symbol.parent_qualname,
                        symbol.parent_line_start,
                    )
                )
            fields: dict[str, str | list[str]] = {
                "type": symbol.concept_type,
                "title": symbol.qualname,
                "language": "python",
                "name": symbol.name,
                "qualname": symbol.qualname,
                "source_path": module.source_path,
                "line_start": str(symbol.line_start),
                "line_end": str(symbol.line_end),
                "signature": symbol.signature,
                "generated_by": "codebase-to-okf",
            }
            if symbol.decorators:
                fields["decorators"] = list(symbol.decorators)
            if symbol.bases:
                fields["bases"] = list(symbol.bases)
            if symbol.return_annotation:
                fields["return_annotation"] = symbol.return_annotation
            if symbol.parameters:
                fields["parameters"] = [item.render() for item in symbol.parameters]
            if symbol.fields:
                fields["fields"] = [item.name for item in symbol.fields]
            if symbol.calls:
                fields["calls_raw"] = [item.expression for item in symbol.calls]
            write_concept(
                output_root / "symbols" / symbol_file,
                fields,
                _symbol_body(
                    symbol,
                    module.source_path,
                    module_file,
                    parent_file,
                ),
            )

            for call in symbol.calls:
                call_key = (
                    module.source_path,
                    symbol.qualname,
                    symbol.line_start,
                    call.line_start,
                    call.expression,
                )
                call_file = call_files[call_key]
                candidates = candidates_by_name.get(call.callee, [])
                candidate_labels: list[str] = []
                candidate_links: list[str] = []
                for candidate_module, candidate in candidates:
                    candidate_file = symbol_files[
                        (
                            candidate_module,
                            candidate.qualname,
                            candidate.line_start,
                        )
                    ]
                    label = f"{candidate_module}::{candidate.qualname}@{candidate.line_start}"
                    candidate_labels.append(label)
                    candidate_links.append(f"- [{label}](../symbols/{candidate_file})")
                body = [
                    f"# {symbol.qualname} → {call.expression}",
                    "",
                    f"Observed in [{symbol.qualname}](../symbols/{symbol_file}) at lines "
                    f"{call.line_start}–{call.line_end}.",
                    "",
                    "Resolution status: `syntactic-unresolved`.",
                    "",
                    "The callee name/expression is syntax evidence. Name-matched "
                    "candidates below are navigation hints, not dispatch claims.",
                ]
                if candidate_links:
                    body.extend(["", "## Name-matched candidates", "", *candidate_links])
                write_concept(
                    output_root / "calls" / call_file,
                    {
                        "type": "CodeCall",
                        "title": f"{symbol.qualname} → {call.expression}",
                        "language": "python",
                        "source_path": module.source_path,
                        "caller": symbol.qualname,
                        "caller_line_start": str(symbol.line_start),
                        "callee": call.callee,
                        "expression": call.expression,
                        "line_start": str(call.line_start),
                        "line_end": str(call.line_end),
                        "resolution": "syntactic-unresolved",
                        "candidate_targets": candidate_labels,
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
                    "resolution": "syntactic-unresolved",
                    "generated_by": "codebase-to-okf",
                },
                "\n".join(
                    [
                        f"# {', '.join(item.targets)}",
                        "",
                        f"Declared in [{module.source_path}](../modules/{module_file}).",
                        "",
                        "Resolution status: `syntactic-unresolved`.",
                    ]
                ),
            )

    (output_root / "index.md").write_text(
        "\n".join(index_lines).rstrip() + "\n",
        encoding="utf-8",
    )
    symbol_count = sum(len(module.symbols) for module in modules)
    import_count = sum(len(module.imports) for module in modules)
    call_count = sum(len(symbol.calls) for module in modules for symbol in module.symbols)
    return {
        "modules": len(modules),
        "symbols": symbol_count,
        "imports": import_count,
        "calls": call_count,
        "concepts": len(modules) + symbol_count + import_count + call_count,
    }


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone recipe CLI."""
    parser = argparse.ArgumentParser(description="Project a Python codebase into an OKF bundle.")
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
    """Run extraction, emission, validation, and a machine-readable summary."""
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
        prepare_output(source_root, output_root, force=args.force)
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

    warnings = sum(violation.severity.value == "warning" for violation in report.violations)
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
