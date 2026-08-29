#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Assemble one release's notes from the fragments its changes contributed.

A version number belongs to a release, not to a pull request: several changes
can land together under `0.45.3` because that is the version they will all
carry once merged. One shared `changelog/0.45.3.md` would force every one of
those changes to edit the same list, so each contributes a fragment to
`changelog/0.45.3/` instead and the release assembles them.

Fragment order is the sorted file name, so the assembled notes are the same on
every machine and every rerun. Frontmatter is stripped: it identifies the
fragment inside the repository and has no business in a published release body
-- v0.45.1 shipped its `---` block and a BOM to GitHub because the workflow
passed the raw file through.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


class FragmentError(ValueError):
    """Report a missing or malformed set of release-note fragments."""


def fragment_paths(root: Path, version: str) -> list[Path]:
    """Return the version's fragments, in assembly order."""
    directory = root / "changelog" / version
    if not directory.is_dir():
        message = f"{directory} does not exist; a release needs at least one note fragment"
        raise FragmentError(message)
    paths = sorted(path for path in directory.glob("*.md") if path.is_file())
    if not paths:
        message = f"{directory} has no *.md fragment"
        raise FragmentError(message)
    return paths


def fragment_body(path: Path) -> str:
    """Return a fragment's Markdown, without its frontmatter."""
    try:
        text = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        message = f"cannot read {path}: {exc}"
        raise FragmentError(message) from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        message = f"{path} has no frontmatter"
        raise FragmentError(message)
    try:
        end = lines.index("---", 1)
    except ValueError as exc:
        message = f"{path} has unterminated frontmatter"
        raise FragmentError(message) from exc
    body = "\n".join(lines[end + 1 :]).strip()
    if not body:
        message = f"{path} has frontmatter but no notes"
        raise FragmentError(message)
    return body


def render(root: Path, version: str) -> str:
    """Return the published release body for the given version."""
    bodies = [fragment_body(path) for path in fragment_paths(root, version)]
    return f"# okf-parser {version}\n\n" + "\n\n".join(bodies) + "\n"


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: write the assembled release notes to stdout."""
    parser = argparse.ArgumentParser(description="Assemble release notes from fragments.")
    parser.add_argument("version")
    parser.add_argument("--root", type=Path, default=Path())
    args = parser.parse_args(argv)

    try:
        notes = render(args.root, args.version)
    except FragmentError as exc:
        sys.stderr.write(f"{exc}\n")
        return 1
    sys.stdout.write(notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
