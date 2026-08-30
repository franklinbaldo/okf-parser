"""Validate prospective Git commit messages for commit-msg hooks."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from okf_parser.git_commit import GitCommitMessageError, validate_git_commit_message


def build_parser() -> argparse.ArgumentParser:
    """Build the small hook-oriented command-line parser."""
    parser = argparse.ArgumentParser(
        prog="okf-commit-msg",
        description="Validate one prospective Git commit message without rewriting it.",
    )
    parser.add_argument("path", type=Path, help="Path supplied by Git to a commit-msg hook")
    parser.add_argument(
        "--require-envelope",
        action="store_true",
        help="Reject plain legacy messages that have no subject-first OKF envelope",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Return a hook-compatible process status after validating one message file."""
    args = build_parser().parse_args(argv)
    try:
        source = args.path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        sys.stderr.write(f"okf-commit-msg: cannot read {args.path}: {exc}\n")
        return 1

    try:
        validate_git_commit_message(source, require_envelope=args.require_envelope)
    except GitCommitMessageError as exc:
        location = f"{args.path}:{exc.line}" if exc.line is not None else str(args.path)
        sys.stderr.write(f"{location}: {exc.code}: {exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
