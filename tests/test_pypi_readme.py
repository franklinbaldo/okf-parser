"""Cover the derivation of the PyPI long description from the repository README."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts.pypi_readme import BLOB, render

REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPT = REPOSITORY / "scripts" / "pypi_readme.py"

SOURCE = """---
type: Project
title: okf-parser
---

# okf-parser

See [architecture](docs/architecture.md) and [spec](https://example.invalid/s).

A [fragment](#section) stays put.
"""


def test_strips_the_frontmatter_that_pypi_would_render_as_a_heading() -> None:
    """The body must start at the real title, not at the YAML keys."""
    assert render(SOURCE).startswith("# okf-parser")
    assert "type: Project" not in render(SOURCE)


def test_rewrites_only_repository_relative_links() -> None:
    """Absolute URLs and bare fragments must survive untouched."""
    rendered = render(SOURCE)
    assert f"({BLOB}docs/architecture.md)" in rendered
    assert "(https://example.invalid/s)" in rendered
    assert "(#section)" in rendered


def test_rejects_a_readme_without_frontmatter() -> None:
    """A README that is not an OKF concept is a contract violation, not input."""
    with pytest.raises(ValueError, match="frontmatter"):
        render("# okf-parser\n")


def test_checked_in_description_matches_the_readme() -> None:
    """The committed artifact must not drift from its source."""
    completed = subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), "--check"],
        cwd=REPOSITORY,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
