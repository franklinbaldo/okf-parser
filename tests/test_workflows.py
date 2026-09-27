"""Workflows must name test files that exist.

A release-only job that lists a deleted test fails only when a tag is
pushed, long after the pull request that deleted it went green.
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_TEST_PATH = re.compile(r"\btests/[\w/]+\.py\b")


def test_every_test_file_a_workflow_names_exists() -> None:
    named = {
        (workflow.name, match)
        for workflow in (_ROOT / ".github" / "workflows").glob("*.yml")
        for match in _TEST_PATH.findall(workflow.read_text(encoding="utf-8"))
    }

    assert named, "expected the workflows to name at least one test file"
    missing = sorted(f"{name}: {path}" for name, path in named if not (_ROOT / path).is_file())
    assert missing == []
