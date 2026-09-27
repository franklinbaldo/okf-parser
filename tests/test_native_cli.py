"""The commands the native binary answers itself (RFC 0024 phase 3b).

``check``, ``inventory``, ``graph`` and ``init`` run in ``okf-engine``; these
tests drive the real binary and pin its output contract: indented JSON with
sorted keys, and the exit status each command has always had.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from okf_parser.bundle import load_bundle
from okf_parser.parser import parse_document
from okf_parser.rust_core import packaged_rust_core
from okf_parser.service import schema_bundle

_CONFIGURED = os.environ.get("OKF_CORE")
_BINARY = Path(_CONFIGURED) if _CONFIGURED else packaged_rust_core()
pytestmark = pytest.mark.skipif(_BINARY is None, reason="the native okf-parser binary is not built")


def _run(*args: str) -> tuple[int, Any]:
    assert _BINARY is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), *args], capture_output=True, check=False, encoding="utf-8"
    )
    payload = json.loads(completed.stdout)
    assert (
        completed.stdout == json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    )
    return completed.returncode, payload


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_check_exits_nonzero_for_a_non_conformant_bundle(tmp_path: Path) -> None:
    _write(tmp_path / "good.md", "---\ntype: Note\n---\n")
    _write(tmp_path / "bad.md", "# no frontmatter\n")

    code, payload = _run("check", str(tmp_path), "--classify")

    assert code == 1
    assert payload["conformant"] is False
    assert [item["code"] for item in payload["diagnostics"]] == ["OKF001"]
    assert payload["classification"]["invalid_or_untyped"] == ["bad.md"]


def test_check_applies_the_spec_rules_natively(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Revisão Ciência\n---\n")

    advisory, warned = _run("check", str(tmp_path), "--require-spec", "docs/{slug}.md")
    normative, failed = _run(
        "check", str(tmp_path), "--require-spec", "docs/{slug}.md", "--normative-spec"
    )

    assert (advisory, warned["diagnostics"][0]["severity"]) == (0, "warning")
    assert (normative, failed["diagnostics"][0]["severity"]) == (1, "error")
    assert failed["diagnostics"][0]["path"] == "docs/revisao-ciencia.md"


def test_inventory_and_graph(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Node\n---\n[b](b.md)\n")
    _write(tmp_path / "b.md", "---\ntype: Node\n---\n")

    inventory_code, inventory = _run("inventory", str(tmp_path), "--digests")
    graph_code, graph = _run("graph", str(tmp_path))

    assert inventory_code == graph_code == 0
    assert inventory["types"] == [{"concept_count": 2, "concept_type": "Node"}]
    assert [row["concept_id"] for row in inventory["digests"]] == ["a", "b"]
    assert (graph["nodes"], graph["edges"]) == (2, 1)


def test_init_plans_and_exits_nonzero_on_a_collision(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Revisao Ciencia\n---\n")
    _write(tmp_path / "b.md", "---\ntype: Revisão Ciência\n---\n")

    code, payload = _run("init", str(tmp_path), "--spec-template", "docs/{slug}.md")

    assert code == 1
    assert payload["specs"]["collisions"] == [
        {"path": "docs/revisao-ciencia.md", "types": ["Revisao Ciencia", "Revisão Ciência"]}
    ]


def test_a_template_without_the_placeholder_is_a_clean_failure(tmp_path: Path) -> None:
    assert _BINARY is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "check", str(tmp_path), "--require-spec", "docs/types.md"],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert completed.returncode == 1
    assert "must contain {slug}" in completed.stderr


def test_frontmatter_json_has_one_spelling_in_the_binary_and_every_python_record(
    tmp_path: Path,
) -> None:
    # UTF-16 order puts U+10000 (a 0xD800 surrogate pair) before U+FF21;
    # code-point order, which Python's sort_keys used, puts it after. The
    # public spelling is the compact UTF-16 one the parsed digest is taken
    # over, in every surface.
    _write(tmp_path / "a.md", "---\ntype: Note\n\uff21: bmp\n\U00010000: astral\n---\n")

    expected = '{"type":"Note","\U00010000":"astral","\uff21":"bmp"}'

    assert load_bundle(tmp_path).concepts[0].frontmatter_json == expected
    assert parse_document(tmp_path / "a.md").frontmatter_json == expected


def test_top_level_help_lists_native_and_delegated_commands() -> None:
    assert _BINARY is not None
    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "--help"], capture_output=True, check=True, encoding="utf-8"
    )

    listed = {line.split()[0] for line in completed.stdout.splitlines() if line.startswith("  ")}
    native = {
        "check",
        "search",
        "sql",
        "apply",
        "format",
        "inventory",
        "graph",
        "init",
        "duckdb",
        "schema",
        "import",
        "serve",
    }
    delegated = {"packs", "add-pack"}
    assert native | delegated <= listed
    assert not any(name.startswith("__") for name in listed)


def test_a_delegated_command_is_answered_by_the_python_cli() -> None:
    code, payload = _run("packs")

    assert code == 0
    assert "packs" in payload


def test_search_prints_rows_and_full_json(tmp_path: Path) -> None:
    _write(tmp_path / "a.md", "---\ntype: Note\n---\nintro\nthe retry budget\n")
    assert _BINARY is not None

    rows = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "search", str(tmp_path), "RETRY", "--context", "1"],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )
    code, payload = _run("search", str(tmp_path), "retry", "--detail", "full")

    assert rows.stdout == "location\tsnippet\na.md#B1-B2\tintro the retry budget\n"
    assert code == 0
    assert payload["results"][0]["location"] == "a.md#B2"


def test_format_checks_then_writes_the_canonical_form(tmp_path: Path) -> None:
    document = tmp_path / "a.md"
    _write(document, "---\ntitle: T\ntype: Note\n---\n1. a\n1. b\n")

    checked, report = _run("format", str(tmp_path))
    written, _ = _run("format", str(tmp_path), "--write")

    assert (checked, report["changed_paths"], report["clean"]) == (1, ["a.md"], False)
    assert written == 0
    assert document.read_text(encoding="utf-8") == "---\ntype: Note\ntitle: T\n---\n\n1. a\n2. b\n"


def test_format_write_exits_nonzero_when_a_file_was_skipped(tmp_path: Path) -> None:
    (tmp_path / "unreadable.md").write_bytes(b"# Caf\xe9\n")

    code, payload = _run("format", str(tmp_path), "--write")

    assert code == 1
    assert payload["skipped"] == [{"path": "unreadable.md", "reason": "not UTF-8"}]


def test_import_exits_nonzero_for_a_divergent_existing_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,name\nr1,Expected\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    _write(bundle / "example" / "r1.md", "---\ntype: Example\nid: r1\nname: Different\n---\n")

    code, payload = _run(
        "import",
        str(source),
        str(bundle),
        "--type",
        "Example",
        "--id-column",
        "id",
        "--on-conflict",
        "verify-identical",
    )

    assert code == 1
    assert payload["conflicting_existing"] == ["example/r1.md"]


@pytest.mark.parametrize("schema_format", ["zod", "pydantic", "graphql"])
def test_text_schema_prints_the_service_text_with_one_trailing_newline(
    tmp_path: Path, schema_format: str
) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Example\nname: value\n---\nBody\n")
    assert _BINARY is not None

    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "schema", str(tmp_path), "--format", schema_format],
        capture_output=True,
        check=True,
        encoding="utf-8",
    )

    expected = schema_bundle(str(tmp_path), schema_format)
    assert isinstance(expected, str)
    assert completed.stdout == expected.rstrip("\n") + "\n"


def test_json_schema_is_the_service_payload(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Example\ncount: 3\n---\n")

    code, payload = _run("schema", str(tmp_path), "--infer-types")

    assert code == 0
    assert payload == schema_bundle(str(tmp_path), infer_types=True)
    assert payload["schemas"]["Example"]["properties"]["count"]["type"] == "integer"


def test_a_schema_error_exits_nonzero_with_its_message(tmp_path: Path) -> None:
    _write(tmp_path / "concept.md", "---\ntype: Example\ncount: many\n---\n")
    assert _BINARY is not None

    completed = subprocess.run(  # noqa: S603 - fixed argv to the binary under test
        [str(_BINARY), "schema", str(tmp_path), "--cast", "count=integer"],
        capture_output=True,
        check=False,
        encoding="utf-8",
    )

    assert completed.returncode != 0
    assert "cannot cast 'count' to integer" in completed.stderr
