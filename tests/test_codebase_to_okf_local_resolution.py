"""Contract for conservative source-tree import resolution in the codebase skill."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
PROJECT = SKILL_ROOT / "scripts" / "codebase_to_okf.py"
RESOLVE = SKILL_ROOT / "scripts" / "resolve_codebase_okf.py"
QUERY = SKILL_ROOT / "scripts" / "query_codebase_okf.py"
SPEC_TEMPLATE = "docs/types/{slug}.md"


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
    (source / "pkg").mkdir(parents=True)
    (source / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (source / "pkg" / "utils.py").write_text(
        "def helper() -> str:\n    return 'ok'\n",
        encoding="utf-8",
    )
    (source / "pkg" / "service.py").write_text(
        "from .utils import helper\nimport json\n\ndef run() -> str:\n    return helper()\n",
        encoding="utf-8",
    )
    (source / "app.py").write_text(
        "from pkg.utils import helper\nimport pkg.utils, external_lib\n",
        encoding="utf-8",
    )


def test_resolver_adds_separate_evidence_claims_without_rewriting_imports(tmp_path: Path) -> None:
    source = tmp_path / "source"
    bundle = tmp_path / "bundle"
    _write_fixture(source)

    projected = _run(PROJECT, source, bundle)
    assert projected.returncode == 0, projected.stderr
    imports_before = {
        path.name: path.read_bytes() for path in sorted((bundle / "imports").glob("*.md"))
    }

    resolved = _run(RESOLVE, bundle)
    assert resolved.returncode == 0, resolved.stderr
    payload = json.loads(resolved.stdout)
    assert payload["resolved"] == 2
    assert payload["partial"] == 1
    assert payload["unresolved"] == 1
    assert payload["resolution_concepts"] == 3
    assert payload["resolution_method"] == "projected-module-prefix-v1"

    imports_after = {
        path.name: path.read_bytes() for path in sorted((bundle / "imports").glob("*.md"))
    }
    assert imports_after == imports_before

    report = validate_path(bundle, require_spec=SPEC_TEMPLATE, normative_spec=True)
    assert report.is_conformant
    spec = bundle / "docs" / "types" / "codeimportresolution.md"
    assert spec.is_file()
    assert "not a claim about runtime import selection" in spec.read_text(encoding="utf-8")

    resolution_files = sorted((bundle / "resolutions").glob("import-resolution-*.md"))
    assert len(resolution_files) == 3
    texts = [path.read_text(encoding="utf-8") for path in resolution_files]
    assert any('"resolution": "source-tree-partial"' in text for text in texts)
    assert any('"unresolved_targets": [' in text and "external_lib" in text for text in texts)
    assert all("runtime import selection" in text for text in texts)

    query = _run(QUERY, bundle, "--dependency", "pkg.utils")
    assert query.returncode == 0, query.stderr
    matches = json.loads(query.stdout)
    assert len(matches) == 3
    assert {item["type"] for item in matches} == {"CodeImportResolution"}
    assert all("pkg.utils" in item["resolved_modules"] for item in matches)

    before = _snapshot(bundle)
    repeated = _run(RESOLVE, bundle)
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout)["created_specs"] == []
    assert _snapshot(bundle) == before
