"""End-to-end contract for the one-shot codebase-to-OKF recipe."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
SCRIPT = SKILL_ROOT / "scripts" / "codebase_to_okf.py"
SPEC_TEMPLATE = "docs/types/{slug}.md"
EXPECTED_SPECS = {
    "codecall.md",
    "codeclass.md",
    "codefunction.md",
    "codeimport.md",
    "codemethod.md",
    "codemodule.md",
    "spec.md",
}


def _run(source: Path, output: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), str(source), str(output), *extra],
        check=False,
        capture_output=True,
        text=True,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _write_fixture(source: Path) -> None:
    source.mkdir()
    (source / "app.py").write_text(
        """import json

class Greeter:
    @staticmethod
    def hello(name: str) -> str:
        return name


def main() -> str:
    return Greeter().hello("world")
""",
        encoding="utf-8",
    )


def test_one_shot_recipe_returns_only_after_normative_fixed_point(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "bundle"
    _write_fixture(source)

    first = _run(source, output)
    assert first.returncode == 0, first.stderr
    payload = json.loads(first.stdout)
    assert payload["conformant"] is True
    assert payload["normative_specs"] is True
    assert payload["concepts"] == 7
    assert payload["spec_count"] == 7
    assert payload["total_concepts"] == 14
    assert set(payload["created_specs"]) == {
        f"docs/types/{name}" for name in EXPECTED_SPECS
    }

    report = validate_path(output, require_spec=SPEC_TEMPLATE, normative_spec=True)
    assert report.is_conformant
    assert report.concept_count == 14
    assert {path.name for path in (output / "docs" / "types").glob("*.md")} == EXPECTED_SPECS

    before = _snapshot(output)
    repeated = _run(source, output, "--force")
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["normative_specs"] is True
    assert _snapshot(output) == before
