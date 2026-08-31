# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.4,<0.46",
#   "packaging>=26,<27",
# ]
# ///
# ruff: noqa: T201
"""Generate and normatively finalize a Python codebase OKF bundle in one command."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GENERATOR = SCRIPT_DIR / "python_codebase_to_okf.py"
PROJECT_METADATA = SCRIPT_DIR / "python_project_metadata_to_okf.py"
FINALIZER = SCRIPT_DIR / "finalize_codebase_okf.py"


def _run(script: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one trusted sibling recipe with the current PEP 723 environment."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(script), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _payload(result: subprocess.CompletedProcess[str], step: str) -> dict[str, object] | None:
    """Decode one successful recipe payload or report a malformed contract."""
    try:
        value = json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"error: {step} returned non-JSON success output", file=sys.stderr)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="")
        return None
    if not isinstance(value, dict):
        print(f"error: {step} returned a non-object JSON payload", file=sys.stderr)
        return None
    return value


def _relay_failure(result: subprocess.CompletedProcess[str], step: str) -> int:
    """Preserve the lower-level recipe diagnostic and exit classification."""
    print(f"error: {step} failed", file=sys.stderr)
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    elif result.stdout:
        print(result.stdout, file=sys.stderr, end="")
    return result.returncode or 2


def build_parser() -> argparse.ArgumentParser:
    """Build the one-shot codebase projection CLI."""
    parser = argparse.ArgumentParser(
        description="Generate a Python OKF projection and finalize its normative type specs."
    )
    parser.add_argument("source", type=Path, help="Python project/source tree")
    parser.add_argument("output", type=Path, help="OKF bundle destination")
    parser.add_argument(
        "--force",
        action="store_true",
        help="replace a non-empty output directory",
    )
    parser.add_argument(
        "--exclude-dir",
        action="append",
        default=[],
        help="additional directory name to exclude; may repeat",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run source, manifest, and normative finalization as one agent-facing transaction."""
    args = build_parser().parse_args(argv)
    generation_args = [str(args.source), str(args.output)]
    if args.force:
        generation_args.append("--force")
    for name in args.exclude_dir:
        generation_args.extend(["--exclude-dir", name])

    generated = _run(GENERATOR, generation_args)
    if generated.returncode != 0:
        return _relay_failure(generated, "generation")
    generation = _payload(generated, "generation")
    if generation is None:
        return 2

    projected_metadata = _run(PROJECT_METADATA, [str(args.source), str(args.output)])
    if projected_metadata.returncode != 0:
        return _relay_failure(projected_metadata, "project metadata projection")
    metadata = _payload(projected_metadata, "project metadata projection")
    if metadata is None:
        return 2

    finalized = _run(FINALIZER, [str(args.output)])
    if finalized.returncode != 0:
        return _relay_failure(finalized, "type finalization")
    finalization = _payload(finalized, "type finalization")
    if finalization is None:
        return 2

    source_concepts = int(generation.get("concepts", 0))
    manifest_concepts = int(metadata.get("concepts", 0))
    projected_concepts = source_concepts + manifest_concepts
    spec_count = int(finalization.get("spec_count", 0))
    print(
        json.dumps(
            {
                **generation,
                "concepts": projected_concepts,
                "source_concepts": source_concepts,
                "manifest_concepts": manifest_concepts,
                "manifest": metadata.get("manifest"),
                "projects": metadata.get("projects", 0),
                "dependencies": metadata.get("dependencies", 0),
                "dependency_groups": metadata.get("dependency_groups", []),
                "created_specs": finalization.get("created_specs", []),
                "spec_count": spec_count,
                "total_concepts": projected_concepts + spec_count,
                "normative_specs": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
