"""Executable contract for the self-contained codebase-to-OKF Agent Skill."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
SKILL_PATH = SKILL_ROOT / "SKILL.md"
SCRIPT_PATH = SKILL_ROOT / "scripts" / "python_codebase_to_okf.py"
QUERY_SCRIPT_PATH = SKILL_ROOT / "scripts" / "query_codebase_okf.py"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_recipe(
    source: Path,
    output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT_PATH), str(source), str(output), *extra],
        check=False,
        capture_output=True,
        text=True,
    )


def _run_query(
    bundle: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(QUERY_SCRIPT_PATH), str(bundle), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def test_skill_is_itself_a_typed_okf_concept() -> None:
    report = validate_path(SKILL_ROOT)
    assert report.is_conformant
    assert report.concept_count == 1


def test_recipes_are_bundled_pep723_code_not_skill_context() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")
    recipe = SCRIPT_PATH.read_text(encoding="utf-8")
    query_recipe = QUERY_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "scripts/python_codebase_to_okf.py" in skill
    assert "scripts/query_codebase_okf.py" in skill
    assert "# /// script" not in skill
    for path, source in ((SCRIPT_PATH, recipe), (QUERY_SCRIPT_PATH, query_recipe)):
        assert source.startswith('# /// script\n# requires-python = ">=3.12"\n')
        assert '"okf-parser>=0.45.2,<0.46"' in source
        compile(source, str(path), "exec")


def test_recipe_generates_rich_queryable_deterministic_bundle(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text(
        '''"""Example module."""

import json

class Greeter(object):
    """Provide greeting helpers."""

    prefix: str = "hello"

    @staticmethod
    def hello(name: str = "world") -> str:
        """Return the supplied name."""
        return name


def main() -> str:
    return Greeter().hello("world")


def main() -> str:
    return "second definition"
''',
        encoding="utf-8",
    )

    output = tmp_path / "bundle"
    first = _run_recipe(source, output)
    assert first.returncode == 0, first.stderr

    report = validate_path(output)
    assert report.is_conformant
    assert report.concept_count == 8

    main_files = list((output / "symbols").glob("app-main-*.md"))
    assert len(main_files) == 2

    hello_files = list((output / "symbols").glob("app-greeter-hello-*.md"))
    assert len(hello_files) == 1
    hello = hello_files[0].read_text(encoding="utf-8")
    assert '"signature": "def hello(name: str = \'world\') -> str"' in hello
    assert '"return_annotation": "str"' in hello
    assert '"parameters": [' in hello
    assert "\"name: str = 'world'\"" in hello
    assert '"decorators": [' in hello
    assert '"staticmethod"' in hello
    assert "Return the supplied name." in hello

    class_files = list((output / "symbols").glob("app-greeter-*.md"))
    greeter = next(path for path in class_files if path != hello_files[0])
    greeter_text = greeter.read_text(encoding="utf-8")
    assert '"bases": [' in greeter_text
    assert '"object"' in greeter_text
    assert '"fields": [' in greeter_text
    assert '"prefix"' in greeter_text

    call_files = list((output / "calls").glob("*.md"))
    assert len(call_files) == 2
    hello_call = next(
        path for path in call_files if '"callee": "hello"' in path.read_text(encoding="utf-8")
    )
    hello_call_text = hello_call.read_text(encoding="utf-8")
    assert '"caller": "main"' in hello_call_text
    assert '"expression": "Greeter().hello"' in hello_call_text
    assert '"resolution": "syntactic-unresolved"' in hello_call_text
    assert "app.py::Greeter.hello@" in hello_call_text
    assert "navigation hints, not dispatch claims" in hello_call_text

    query = _run_query(output, "--callee", "hello")
    assert query.returncode == 0, query.stderr
    results = json.loads(query.stdout)
    assert len(results) == 1
    assert results[0]["type"] == "CodeCall"
    assert results[0]["caller"] == "main"
    assert results[0]["callee"] == "hello"
    assert results[0]["candidate_targets"][0].startswith("app.py::Greeter.hello@")

    lookup = _run_query(output, "--name", "hello")
    assert lookup.returncode == 0, lookup.stderr
    symbol_results = json.loads(lookup.stdout)
    assert len(symbol_results) == 1
    assert symbol_results[0]["type"] == "CodeMethod"
    assert symbol_results[0]["signature"] == "def hello(name: str = 'world') -> str"

    first_snapshot = _snapshot(output)
    second = _run_recipe(source, output, "--force")
    assert second.returncode == 0, second.stderr
    assert _snapshot(output) == first_snapshot
