"""Executable contract for the self-contained codebase-to-OKF Agent Skill."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_PATH = Path(__file__).parents[1] / "skills" / "codebase-to-okf" / "SKILL.md"
RECIPE_MARKER = "<!-- recipe:python-codebase-to-okf -->"
PYTHON_FENCE = "```python\n"


def _recipe_source() -> str:
    skill = SKILL_PATH.read_text(encoding="utf-8")
    marker = skill.index(RECIPE_MARKER)
    start = skill.index(PYTHON_FENCE, marker) + len(PYTHON_FENCE)
    end = skill.index("\n```", start)
    return skill[start:end] + "\n"


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _run_recipe(
    script: Path,
    source: Path,
    output: Path,
    *extra: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(script), str(source), str(output), *extra],
        check=False,
        capture_output=True,
        text=True,
    )


def test_embedded_recipe_declares_pep723_metadata() -> None:
    recipe = _recipe_source()
    assert recipe.startswith("# /// script\n")
    assert '# requires-python = ">=3.12"' in recipe
    assert '"okf-parser>=0.45.4"' in recipe
    compile(recipe, str(SKILL_PATH), "exec")


def test_embedded_recipe_generates_deterministic_conformant_bundle(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text(
        """import json

class Greeter:
    def hello(self, name: str) -> str:
        return name


def main() -> str:
    return Greeter().hello(\"world\")
""",
        encoding="utf-8",
    )

    script = tmp_path / "codebase_to_okf.py"
    script.write_text(_recipe_source(), encoding="utf-8")
    output = tmp_path / "bundle"

    first = _run_recipe(script, source, output)
    assert first.returncode == 0, first.stderr

    report = validate_path(output)
    assert report.is_conformant
    assert report.concept_count == 5
    first_snapshot = _snapshot(output)

    second = _run_recipe(script, source, output, "--force")
    assert second.returncode == 0, second.stderr
    assert _snapshot(output) == first_snapshot
