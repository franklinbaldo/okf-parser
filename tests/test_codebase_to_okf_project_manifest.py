"""Contract for PEP 621 project metadata in the codebase-to-OKF skill."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from okf_parser import validate_path

SKILL_ROOT = Path(__file__).parents[1] / "skills" / "codebase-to-okf"
ONE_SHOT = SKILL_ROOT / "scripts" / "codebase_to_okf.py"
MANIFEST_SCRIPT = SKILL_ROOT / "scripts" / "python_project_metadata_to_okf.py"
QUERY_SCRIPT = SKILL_ROOT / "scripts" / "query_codebase_okf.py"
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


def _write_fixture(source: Path) -> None:
    source.mkdir()
    (source / "app.py").write_text('"""Tiny source module."""\n', encoding="utf-8")
    (source / "pyproject.toml").write_text(
        """[project]
name = "example-agent-app"
version = "1.2.3"
description = "Example manifest fixture"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.28; python_version >= '3.12'",
  "rich[markdown]>=13",
]

[project.optional-dependencies]
docs = ["mkdocs>=1.6"]
""",
        encoding="utf-8",
    )


def test_project_manifest_recipe_is_bundled_pep723_code() -> None:
    source = MANIFEST_SCRIPT.read_text(encoding="utf-8")
    assert source.startswith('# /// script\n# requires-python = ">=3.12"\n')
    assert '"okf-parser>=0.45.4,<0.46"' in source
    assert '"packaging>=26,<27"' in source
    compile(source, str(MANIFEST_SCRIPT), "exec")


def test_one_shot_projects_manifest_without_overclaiming_usage(tmp_path: Path) -> None:
    source = tmp_path / "source"
    output = tmp_path / "bundle"
    _write_fixture(source)

    result = _run(ONE_SHOT, source, output)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["source_concepts"] == 1
    assert payload["manifest_concepts"] == 4
    assert payload["projects"] == 1
    assert payload["dependencies"] == 3
    assert payload["dependency_groups"] == ["runtime", "optional:docs"]
    assert payload["concepts"] == 5
    assert payload["spec_count"] == 4
    assert payload["total_concepts"] == 9

    report = validate_path(output, require_spec=SPEC_TEMPLATE, normative_spec=True)
    assert report.is_conformant
    assert report.concept_count == 9
    assert (output / "docs" / "types" / "codeproject.md").is_file()
    assert (output / "docs" / "types" / "codedependency.md").is_file()

    project_files = list((output / "project").glob("*.md"))
    assert len(project_files) == 1
    project_text = project_files[0].read_text(encoding="utf-8")
    assert '"name": "example-agent-app"' in project_text
    assert '"version": "1.2.3"' in project_text
    assert '"requires_python": ">=3.12"' in project_text
    assert "authored manifest evidence" in project_text.lower()

    dependency_files = list((output / "dependencies").glob("*.md"))
    assert len(dependency_files) == 3
    httpx = next(path for path in dependency_files if '"dependency_name": "httpx"' in path.read_text())
    httpx_text = httpx.read_text(encoding="utf-8")
    assert '"group": "runtime"' in httpx_text
    assert '"specifier": ">=0.28"' in httpx_text
    assert '"marker": "python_version >= \'3.12\'"' in httpx_text
    assert '"resolution": "manifest-declared"' in httpx_text
    assert "does not prove installation" in httpx_text

    package_query = _run(QUERY_SCRIPT, output, "--package", "httpx")
    assert package_query.returncode == 0, package_query.stderr
    rows = json.loads(package_query.stdout)
    assert len(rows) == 1
    assert rows[0]["type"] == "CodeDependency"
    assert rows[0]["dependency_name"] == "httpx"
    assert rows[0]["declaration"].startswith("httpx>=0.28")

    before = _snapshot(output)
    repeated = _run(ONE_SHOT, source, output, "--force")
    assert repeated.returncode == 0, repeated.stderr
    assert _snapshot(output) == before
