"""The conformance corpus manifest is the compatibility contract's index.

These tests keep `conformance/MANIFEST.json` from drifting away from the
files it describes, and keep `conformance/regressions.json` shaped the way
`conformance/README.md` documents.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

CONFORMANCE_DIR = Path(__file__).parents[1] / "conformance"
SPEC_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")


def _load(name: str) -> dict:
    return json.loads((CONFORMANCE_DIR / name).read_text(encoding="utf-8"))


def test_manifest_records_a_pinned_spec_revision() -> None:
    manifest = _load("MANIFEST.json")
    spec = manifest["spec"]

    assert spec["version"], "spec.version must name the OKF version this corpus targets"
    assert SPEC_REVISION_RE.match(spec["revision"]), (
        f"spec.revision must be a full 40-character upstream commit SHA, got {spec['revision']!r}"
    )


def test_manifest_matches_the_fixture_files_on_disk() -> None:
    manifest = _load("MANIFEST.json")
    listed = {entry["file"] for entry in manifest["fixtures"]}

    on_disk = {path.name for path in CONFORMANCE_DIR.glob("*.json") if path.name != "MANIFEST.json"}

    missing_from_disk = listed - on_disk
    missing_from_manifest = on_disk - listed
    assert not missing_from_disk, (
        f"MANIFEST.json lists files that do not exist: {missing_from_disk}"
    )
    assert not missing_from_manifest, (
        f"conformance/ has files MANIFEST.json does not list: {missing_from_manifest}"
    )


def test_every_fixture_entry_is_well_formed() -> None:
    manifest = _load("MANIFEST.json")
    for entry in manifest["fixtures"]:
        assert entry["area"], f"{entry['file']}: area must be non-empty"
        assert entry["shared_with"], (
            f"{entry['file']}: shared_with must list at least one implementation"
        )


def test_regressions_corpus_is_append_only_shaped() -> None:
    regressions = _load("regressions.json")

    assert isinstance(regressions["version"], int)
    cases = regressions["cases"]
    assert isinstance(cases, list)

    seen_ids = set()
    for case in cases:
        assert case["id"] not in seen_ids, f"duplicate regression id: {case['id']!r}"
        seen_ids.add(case["id"])
        assert case["issue"], f"{case['id']}: issue must reference the report or fix"
        assert "input" in case, f"{case['id']}: missing input"
        assert "expected" in case, f"{case['id']}: missing expected"
