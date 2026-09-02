#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Derive the PyPI long description from the repository README.

`README.md` is itself an OKF `type: Project` concept, so it must open with YAML
frontmatter. PyPI renders that frontmatter as content: the leading `---` becomes
a thematic break and the key lines, closed by the second `---`, become a setext
heading. The published page therefore opens with `type: Project title: ...`
above the real title.

This script strips that frontmatter and rewrites repository-relative links,
which resolve on GitHub but not on PyPI, into absolute URLs. It is the same
correction `changelog_notes.py` applies to GitHub Release bodies.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Final

BLOB: Final = "https://github.com/franklinbaldo/okf-parser/blob/main/"
FRONTMATTER: Final = re.compile(r"\A﻿?---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
# Markdown inline links whose target is neither absolute nor a bare fragment.
RELATIVE_LINK: Final = re.compile(r"(?<=]\()(?!\w+:|//|#)([^)\s]+)(?=\))")


def render(source: str) -> str:
    """Return the README body with frontmatter removed and links absolute."""
    body, count = FRONTMATTER.subn("", source, count=1)
    if count == 0:
        message = "README.md must open with YAML frontmatter"
        raise ValueError(message)
    return RELATIVE_LINK.sub(lambda match: BLOB + match.group(1), body).lstrip("\n")


def main() -> int:
    """Write or verify the derived PyPI description."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("README.md"))
    parser.add_argument("--output", type=Path, default=Path("README.pypi.md"))
    parser.add_argument(
        "--check",
        action="store_true",
        help="fail instead of writing when the output is stale",
    )
    arguments = parser.parse_args()
    rendered = render(arguments.source.read_text(encoding="utf-8"))
    if arguments.check:
        current = (
            arguments.output.read_text(encoding="utf-8") if arguments.output.exists() else None
        )
        if current != rendered:
            sys.stderr.write(
                f"{arguments.output} is stale; regenerate it with "
                f"`uv run --script scripts/{Path(__file__).name}`\n"
            )
            return 1
        return 0
    arguments.output.write_text(rendered, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
