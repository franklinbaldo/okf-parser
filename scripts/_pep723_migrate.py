#!/usr/bin/env python3
"""One-shot repository migration to PEP 723 for standalone Python programs."""

from __future__ import annotations

import ast
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()
SKIP_PARTS = {
    ".git",
    ".venv",
    "venv",
    "src",
    "tests",
    "test",
    "vendor",
    "node_modules",
    "build",
    "dist",
    "site-packages",
    "__pycache__",
}
IMPORT_TO_DIST = {
    "bs4": "beautifulsoup4",
    "click": "click",
    "cryptography": "cryptography",
    "cv2": "opencv-python-headless",
    "cyclopts": "cyclopts",
    "duckdb": "duckdb",
    "fastmcp": "fastmcp",
    "fitz": "pymupdf",
    "httpx": "httpx",
    "ibis": "ibis-framework",
    "keyring": "keyring",
    "litellm": "litellm",
    "markdown_it": "markdown-it-py",
    "matplotlib": "matplotlib",
    "mcp": "mcp",
    "mdformat": "mdformat",
    "networkx": "networkx",
    "numpy": "numpy",
    "pandas": "pandas",
    "PIL": "pillow",
    "playwright": "playwright",
    "pyarrow": "pyarrow",
    "pydantic": "pydantic",
    "pytest": "pytest",
    "requests": "requests",
    "rich": "rich",
    "ruamel": "ruamel.yaml",
    "secretstorage": "secretstorage",
    "sklearn": "scikit-learn",
    "yaml": "pyyaml",
}
UV_PYTHON_SCRIPT = re.compile(
    r"\buv run(?:\s+--no-sync)?\s+python\s+((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py)"
)
UV_NOSYNC_SCRIPT = re.compile(
    r"\buv run\s+--no-sync\s+((?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.py)"
)


def norm_dist(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def requirement_name(requirement: str) -> str:
    return norm_dist(re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0])


def load_project() -> tuple[str | None, str, dict[str, str], set[str]]:
    path = ROOT / "pyproject.toml"
    if not path.exists():
        return None, ">=3.11", {}, set()
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    project = data.get("project", {})
    name = project.get("name")
    requires_python = project.get("requires-python", ">=3.11")
    requirements: dict[str, str] = {}
    for req in project.get("dependencies", []):
        requirements[requirement_name(req)] = req
    for reqs in project.get("optional-dependencies", {}).values():
        for req in reqs:
            requirements.setdefault(requirement_name(req), req)
    for reqs in data.get("dependency-groups", {}).values():
        if isinstance(reqs, list):
            for req in reqs:
                if isinstance(req, str):
                    requirements.setdefault(requirement_name(req), req)
    local_names: set[str] = set()
    for base in (ROOT, ROOT / "src"):
        if not base.exists():
            continue
        for child in base.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                local_names.add(child.name)
    if name:
        local_names.add(name.replace("-", "_"))
    return name, requires_python, requirements, local_names


def imports(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".", 1)[0])
    return names


def has_main_guard(tree: ast.Module) -> bool:
    for node in tree.body:
        if not isinstance(node, ast.If):
            continue
        test = node.test
        if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
            continue
        if not isinstance(test.ops[0], ast.Eq):
            continue
        left, right = test.left, test.comparators[0]
        pairs = ((left, right), (right, left))
        for candidate_name, candidate_value in pairs:
            if (
                isinstance(candidate_name, ast.Name)
                and candidate_name.id == "__name__"
                and isinstance(candidate_value, ast.Constant)
                and candidate_value.value == "__main__"
            ):
                return True
    return False


def candidate(path: Path) -> tuple[ast.Module, str] | None:
    rel = path.relative_to(ROOT)
    if path.resolve() == SELF or any(part in SKIP_PARTS for part in rel.parts):
        return None
    if path.name == "__init__.py" or path.name.startswith("test_") or path.name.endswith("_test.py"):
        return None
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text, filename=str(rel))
    except SyntaxError:
        return None
    first = text.splitlines()[0] if text.splitlines() else ""
    executable = (first.startswith("#!") and "python" in first) or has_main_guard(tree)
    return (tree, text) if executable else None


def sibling_imports(path: Path) -> set[str]:
    names = {item.stem for item in path.parent.glob("*.py")}
    names.discard("__init__")
    return names


def render_block(
    *,
    requires_python: str,
    dependencies: list[str],
    project_name: str | None,
    project_path: str | None,
) -> str:
    lines = ["# /// script", f'# requires-python = "{requires_python}"', "# dependencies = ["]
    for dependency in dependencies:
        lines.append(f'#     "{dependency}",')
    lines.append("# ]")
    if project_name and project_path:
        lines.extend(
            [
                "#",
                "# [tool.uv.sources]",
                f'# {project_name} = {{ path = "{project_path}", editable = true }}',
            ]
        )
    lines.append("# ///")
    return "\n".join(lines)


def insert_metadata(text: str, block: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and lines[0].startswith("#!"):
        lines[0] = "#!/usr/bin/env -S uv run --script\n"
    else:
        lines.insert(0, "#!/usr/bin/env -S uv run --script\n")
    rebuilt = "".join(lines)
    if "# /// script" in rebuilt:
        return rebuilt
    first, sep, rest = rebuilt.partition("\n")
    return f"{first}\n#\n{block}\n{rest}" if sep else f"{first}\n#\n{block}\n"


def map_dependency(module: str, requirements: dict[str, str]) -> str | None:
    dist = IMPORT_TO_DIST.get(module, module.replace("_", "-"))
    req = requirements.get(norm_dist(dist))
    if req:
        return req
    mapped = IMPORT_TO_DIST.get(module)
    return mapped


def main() -> int:
    project_name, requires_python, requirements, local_names = load_project()
    plans: list[tuple[Path, str, list[str]]] = []
    unknown: list[str] = []

    for path in sorted(ROOT.rglob("*.py")):
        found = candidate(path)
        if found is None:
            continue
        tree, text = found
        modules = imports(tree)
        siblings = sibling_imports(path)
        uses_project = bool(modules & local_names)
        external = modules - set(sys.stdlib_module_names) - local_names - siblings
        deps: list[str] = []
        for module in sorted(external):
            dependency = map_dependency(module, requirements)
            if dependency is None:
                unknown.append(f"{path.relative_to(ROOT)}: unknown import {module!r}")
                continue
            deps.append(dependency)
        source_path: str | None = None
        if uses_project and project_name:
            deps.insert(0, project_name)
            source_path = Path(os.path.relpath(ROOT, path.parent)).as_posix()
        deps = list(dict.fromkeys(deps))
        block = render_block(
            requires_python=requires_python,
            dependencies=deps,
            project_name=project_name if uses_project else None,
            project_path=source_path,
        )
        plans.append((path, insert_metadata(text, block), deps))

    if unknown:
        print("Refusing ambiguous dependency inference:", file=sys.stderr)
        for item in unknown:
            print(f"- {item}", file=sys.stderr)
        return 2

    for path, text, deps in plans:
        ast.parse(text, filename=str(path.relative_to(ROOT)))
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"PEP 723: {path.relative_to(ROOT)} -> {deps}")

    # Make existing uv invocations actually activate PEP 723.  Do not rewrite
    # bare `python script.py`: those may intentionally run in the project env.
    for path in ROOT.rglob("*"):
        if not path.is_file() or any(part in {".git", ".venv", "node_modules"} for part in path.parts):
            continue
        if path.resolve() == SELF:
            continue
        try:
            original = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        updated = UV_PYTHON_SCRIPT.sub(r"uv run \1", original)
        updated = UV_NOSYNC_SCRIPT.sub(r"uv run \1", updated)
        if updated != original:
            path.write_text(updated, encoding="utf-8", newline="\n")
            print(f"call sites: {path.relative_to(ROOT)}")

    if not plans:
        print("No standalone Python programs found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
