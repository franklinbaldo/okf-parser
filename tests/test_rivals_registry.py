"""Keep the registered rivals and the benchmark that interrogates them in step.

The competitive landscape is recorded as `Rival` concepts under
`benchmarks/rivals/`, and `benchmarks/capability_matrix.py` holds the adapters
that interrogate them. Two lists in two places drift, which is exactly how the
README came to name three tools that were not the competition. These tests make
the drift a failure instead of an oversight, and they read the registry through
okf-parser rather than through a YAML library, so the bundle is exercised as a
bundle.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from okf_parser import load_bundle

REPOSITORY = Path(__file__).resolve().parents[1]
RIVALS_BUNDLE = REPOSITORY / "benchmarks" / "rivals"

REQUIRED_FIELDS = ("registry", "package", "surface", "measured")
KNOWN_REGISTRIES = {"pypi", "npm"}


def _flag(value: object) -> bool:
    """Read a YAML boolean the way the parser preserves it.

    Frontmatter scalars survive as authored text rather than as coerced Python
    values; that is the conservative preservation the parser is built on, and
    the typed view lives in the declared schema instead. `true` is therefore the
    string `"true"` here, and treating it as a Python bool would make every
    value truthy.
    """
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


@pytest.fixture(scope="module")
def registered() -> dict[str, dict]:
    """Read every Rival concept through the parser this repository ships."""
    bundle = load_bundle(RIVALS_BUNDLE)
    frame = bundle.concepts.execute()
    return {str(row.concept_id): json.loads(row.frontmatter_json) for row in frame.itertuples()}


@pytest.fixture(scope="module")
def benchmark_rivals() -> dict[str, object]:
    """Import the capability matrix without running it."""
    sys.path.insert(0, str(REPOSITORY / "benchmarks"))
    try:
        from capability_matrix import RIVALS  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    return {rival.name: rival for rival in RIVALS}


def test_every_document_in_the_bundle_is_a_rival(registered: dict[str, dict]) -> None:
    assert registered, "no Rival concepts were registered"
    assert {entry["type"] for entry in registered.values()} == {"Rival"}


def test_every_rival_declares_the_fields_the_type_specifies(registered: dict[str, dict]) -> None:
    for name, entry in registered.items():
        missing = [field for field in REQUIRED_FIELDS if field not in entry]
        assert not missing, f"{name} is missing {missing}"
        assert entry["registry"] in KNOWN_REGISTRIES, f"{name} has registry {entry['registry']!r}"
        assert isinstance(entry["surface"], list), f"{name} surface must be a list"


def test_measured_rivals_are_exactly_the_ones_the_benchmark_interrogates(
    registered: dict[str, dict],
    benchmark_rivals: dict[str, object],
) -> None:
    """A rival marked measured must be run, and a rival run must be marked."""
    claimed = {entry["title"] for entry in registered.values() if _flag(entry["measured"])}
    assert claimed == set(benchmark_rivals), (
        "registry and benchmark disagree about which rivals are measured; "
        f"registered-only={sorted(claimed - set(benchmark_rivals))} "
        f"benchmark-only={sorted(set(benchmark_rivals) - claimed)}"
    )


def test_recorded_surfaces_match_the_ones_the_benchmark_reports(
    registered: dict[str, dict],
    benchmark_rivals: dict[str, object],
) -> None:
    """The surface is the evidence behind an unsupported verdict, so it must agree."""
    by_title = {entry["title"]: entry for entry in registered.values()}
    for name, rival in benchmark_rivals.items():
        recorded = by_title[name]["surface"]
        assert sorted(recorded) == sorted(rival.surface), (
            f"{name}: registry records {sorted(recorded)}, benchmark reports "
            f"{sorted(rival.surface)}"
        )


def test_unmeasured_rivals_are_recorded_rather_than_omitted(registered: dict[str, dict]) -> None:
    """The backlog is the point: an unmeasured rival must still be queryable."""
    unmeasured = [entry["title"] for entry in registered.values() if not _flag(entry["measured"])]
    assert unmeasured, (
        "no unmeasured rivals are recorded; either the ecosystem is fully "
        "measured, which it is not, or the backlog stopped being written down"
    )
    for entry in registered.values():
        if not _flag(entry["measured"]):
            assert "version_measured" not in entry, (
                f"{entry['title']} is unmeasured but records a measured version"
            )
