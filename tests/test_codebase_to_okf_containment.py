"""Contract for explicit lexical containment in codebase-to-OKF projections."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
ONE_SHOT = SKILL_ROOT / "scripts" / "codebase_to_okf.py"
QUERY = SKILL_ROOT / "scripts" / "query_codebase_okf.py"
SPEC_TEMPLATE = "docs/types/{slug}.md"


def _run(script: Path, *args: object) -> subprocess.CompletedProcess[str]:
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


def _query(bundle: Path, *args: str) -> list[dict[str, object]]:
    result = _run(QUERY, bundle, *args)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, list)
    return payload


def test_projection_exposes_bidirectional_immediate_lexical_containment(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "bundle"
    source.mkdir()
    (source / "app.py").write_text(
        "class Greeter:\n"
        "    def hello(self, name: str) -> str:\n"
        "        def normalize(value: str) -> str:\n"
        "            return value.strip()\n"
        "\n"
        "        return normalize(name)\n",
        encoding="utf-8",
    )

    generated = _run(ONE_SHOT, source, output)
    assert generated.returncode == 0, generated.stderr
    assert validate_path(
        output,
        require_spec=SPEC_TEMPLATE,
        normative_spec=True,
    ).is_conformant

    greeter = _query(output, "--name", "Greeter")
    assert len(greeter) == 1
    class_row = greeter[0]
    assert class_row["type"] == "CodeClass"
    assert class_row["child_qualnames"] == ["Greeter.hello"]
    child_symbols = class_row["child_symbols"]
    assert isinstance(child_symbols, list)
    assert len(child_symbols) == 1

    methods = _query(output, "--parent", "Greeter")
    assert len(methods) == 1
    method_row = methods[0]
    assert method_row["type"] == "CodeMethod"
    assert method_row["qualname"] == "Greeter.hello"
    assert method_row["parent_qualname"] == "Greeter"
    assert method_row["parent_line_start"] == "1"
    assert method_row["parent_symbol"] == class_row["path"]
    assert method_row["child_qualnames"] == ["Greeter.hello.normalize"]

    nested = _query(output, "--parent", "Greeter.hello")
    assert len(nested) == 1
    nested_row = nested[0]
    assert nested_row["type"] == "CodeFunction"
    assert nested_row["qualname"] == "Greeter.hello.normalize"
    assert nested_row["parent_symbol"] == method_row["path"]
    assert "child_qualnames" not in nested_row

    class_text = (output / str(class_row["path"])).read_text(encoding="utf-8")
    method_text = (output / str(method_row["path"])).read_text(encoding="utf-8")
    assert Path(str(method_row["path"])).name in class_text
    assert Path(str(class_row["path"])).name in method_text

    before = _snapshot(output)
    repeated = _run(ONE_SHOT, source, output, "--force")
    assert repeated.returncode == 0, repeated.stderr
    assert _snapshot(output) == before
