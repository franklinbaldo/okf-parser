"""Contract for canonical type-spec finalization of generated codebase bundles."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
GENERATOR = SKILL_ROOT / "scripts" / "python_codebase_to_okf.py"
FINALIZER = SKILL_ROOT / "scripts" / "finalize_codebase_okf.py"
SPEC_TEMPLATE = "docs/types/{slug}.md"
EXPECTED_SPECS = {
    "docs/types/codecall.md",
    "docs/types/codeclass.md",
    "docs/types/codefunction.md",
    "docs/types/codeimport.md",
    "docs/types/codemethod.md",
    "docs/types/codemodule.md",
    "docs/types/spec.md",
}


def _run(script: Path, *args: Path | str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, str(script), *(str(item) for item in args)],
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


def test_finalizer_uses_init_lifecycle_and_reaches_normative_fixed_point(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "bundle"
    _write_fixture(source)

    generated = _run(GENERATOR, source, output)
    assert generated.returncode == 0, generated.stderr
    assert not (output / "docs" / "types").exists()

    finalized = _run(FINALIZER, output)
    assert finalized.returncode == 0, finalized.stderr
    payload = json.loads(finalized.stdout)
    assert set(payload["created_specs"]) == EXPECTED_SPECS
    assert payload["spec_count"] == len(EXPECTED_SPECS)

    report = validate_path(output, require_spec=SPEC_TEMPLATE, normative_spec=True)
    assert report.is_conformant
    assert report.concept_count == 14

    for relative in EXPECTED_SPECS:
        text = (output / relative).read_text(encoding="utf-8")
        assert text.startswith("---\ntype: Spec\n")
        assert "TODO:" not in text

    call_spec = (output / "docs" / "types" / "codecall.md").read_text(encoding="utf-8")
    assert "not a resolved runtime dispatch edge" in call_spec
    assert "candidate_targets" in call_spec

    before = _snapshot(output)
    repeated = _run(FINALIZER, output)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["created_specs"] == []
    assert _snapshot(output) == before
