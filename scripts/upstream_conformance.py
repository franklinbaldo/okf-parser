#!/usr/bin/env -S uv run --script
#
# /// script
# requires-python = ">=3.12"
# dependencies = [
# ]
# ///
"""Check the pinned upstream OKF revision against a local checkout of it.

Run this when bumping ``conformance/upstream/UPSTREAM.json`` to a new upstream
commit:

    git clone https://github.com/GoogleCloudPlatform/knowledge-catalog /tmp/kc
    git -C /tmp/kc checkout <commit>
    uv run --script scripts/upstream_conformance.py /tmp/kc

It reports, without changing any file:

- whether the checkout is the pinned commit and the specification digest matches;
- every registered clause whose quoted text no longer appears in the
  specification, which is the reviewable semantic diff of the bump;
- every example bundle upstream ships whose error codes differ from the pin
  (``examples`` records known divergences, such as ``acme_retail``'s OKF006).

The exit status is non-zero when any of those disagrees with the pin. Example
bundles are checked with this repository's own ``okf-parser check``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

_REPOSITORY = Path(__file__).resolve().parents[1]
_PIN = _REPOSITORY / "conformance" / "upstream" / "UPSTREAM.json"


def _normalized(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _run(*command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, check=False, text=True)  # noqa: S603


def _head(checkout: Path) -> str:
    return _run("git", "-C", str(checkout), "rev-parse", "HEAD").stdout.strip()


def _error_codes(bundle: Path) -> list[str]:
    result = _run("uv", "run", "--project", str(_REPOSITORY), "okf-parser", "check", str(bundle))
    diagnostics = json.loads(result.stdout)["diagnostics"]
    return sorted({item["code"] for item in diagnostics if item["severity"] == "error"})


def check(checkout: Path) -> list[str]:
    """Return one problem line per disagreement between the pin and a checkout."""
    pin = json.loads(_PIN.read_text(encoding="utf-8"))
    problems: list[str] = []

    head = _head(checkout)
    if head != pin["commit"]:
        problems.append(f"commit: pinned {pin['commit']}, checkout is {head}")

    spec_path = checkout / pin["specification"]["path"]
    spec = spec_path.read_bytes()
    digest = hashlib.sha256(spec).hexdigest()
    if digest != pin["specification"]["sha256"]:
        problems.append(f"sha256: pinned {pin['specification']['sha256']}, spec is {digest}")

    text = _normalized(spec.decode("utf-8"))
    problems.extend(
        f"clause {clause['id']} (§{clause['section']}) no longer quoted verbatim: {clause['quote']}"
        for clause in pin["clauses"]
        if _normalized(clause["quote"]) not in text
    )

    examples = pin["examples"]
    bundles = sorted(path for path in (spec_path.parent / "bundles").iterdir() if path.is_dir())
    problems.extend(
        f"example {name}: pinned but no longer shipped upstream"
        for name in sorted(set(examples) - {bundle.name for bundle in bundles})
    )
    for bundle in bundles:
        codes = _error_codes(bundle)
        expected = examples.get(bundle.name)
        print(f"example {bundle.name}: errors {codes or 'none'}")  # noqa: T201
        if expected is None:
            problems.append(f"example {bundle.name}: new upstream example, not pinned")
        elif codes != sorted(expected):
            problems.append(f"example {bundle.name}: pinned errors {expected}, observed {codes}")
    return problems


def main(argv: list[str] | None = None) -> int:
    """Command-line entry point."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("checkout", type=Path, help="local clone of the upstream repository")
    problems = check(parser.parse_args(argv).checkout)
    for line in problems:
        print(line, file=sys.stderr)  # noqa: T201
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
