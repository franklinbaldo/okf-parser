"""Executable OKF conformance corpus pinned to an upstream specification revision.

Each case under ``conformance/upstream/<version>/<name>/`` holds a ``bundle/``
directory and a ``case.json`` stating what a consumer must observe. Only the
keys present in ``expected`` are asserted, so a case pins exactly the claim it
names.

A known disagreement is recorded in ``divergence.observed`` as the exact value
each affected engine produces for each affected field. That engine is held to
those values instead of ``expected`` for those fields only; every other field
stays asserted normally. A failure message says what broke:

- ``engine-divergence``: the engines disagree on a field no divergence declares;
- ``normative-regression`` / ``policy-regression``: a field no longer matches
  the case;
- ``divergence-changed``: an engine no longer produces the recorded divergent
  value, because the divergence was fixed or shifted. Update the case.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest

from okf_parser.bundle import Bundle, load_bundle

_ROOT = Path(__file__).parents[1] / "conformance" / "upstream"
_UPSTREAM = json.loads((_ROOT / "UPSTREAM.json").read_text(encoding="utf-8"))
_CASES = sorted(path.parent for path in _ROOT.glob("*/*/case.json"))
_EXECUTABLE = os.environ.get("OKF_CORE")
_ENGINES = {"python", "rust", "typescript"}
_FIELDS = {"conformant", "concepts", "reserved", "diagnostics", "links", "frontmatter"}


def _case(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "case.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def observe(bundle: Bundle) -> dict[str, Any]:
    """Project a loaded bundle onto the engine-neutral shape the corpus asserts."""
    concepts = [concept.model_dump() for concept in bundle.concepts]
    return {
        "conformant": bundle.is_conformant,
        "concepts": {row["path"]: row["concept_type"] for row in concepts},
        "reserved": sorted(reserved.path for reserved in bundle.reserved),
        "diagnostics": [
            {"code": item.code, "severity": item.severity.value, "path": item.path}
            for item in bundle.validate()
        ],
        "links": [
            {
                "source": link.source_id,
                "raw_target": link.raw_target,
                "target": link.target_id,
                "exists": link.exists,
            }
            for link in bundle.links
        ],
        "frontmatter": {row["path"]: json.loads(row["frontmatter_json"]) for row in concepts},
    }


def _engines(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    observed = {"rust": observe(load_bundle(bundle_dir))}
    if _EXECUTABLE is not None:
        observed["rust-pinned"] = observe(load_bundle(bundle_dir, rust_core=Path(_EXECUTABLE)))
    return observed


def _divergent(case: dict[str, Any]) -> dict[str, dict[str, Any]]:
    divergence = case.get("divergence")
    return {} if divergence is None else divergence["observed"]


@pytest.mark.parametrize("case_dir", _CASES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_upstream_case(case_dir: Path) -> None:
    case = _case(case_dir)
    divergent = _divergent(case)
    divergent_fields = {field for fields in divergent.values() for field in fields}
    observed = _engines(case_dir / "bundle")

    reference = observed["rust"]
    for engine, result in observed.items():
        drift = sorted(
            field for field in _FIELDS - divergent_fields if result[field] != reference[field]
        )
        assert not drift, f"engine-divergence: installed != {engine} on {drift}"

    for engine, result in observed.items():
        recorded = divergent.get(engine.removesuffix("-pinned"), {})
        wanted = {**case["expected"], **recorded}
        mismatches = {
            field: {"expected": value, "observed": result[field]}
            for field, value in wanted.items()
            if result[field] != value
        }
        changed = sorted(field for field in mismatches if field in recorded)
        assert not changed, (
            f"divergence-changed in {case_dir.name} for {engine} on {changed}: "
            "the recorded divergence was fixed or shifted; update the case\n"
            + json.dumps(mismatches, ensure_ascii=False, indent=2)
        )
        assert not mismatches, (
            f"{case['category']}-regression in {case_dir.name} for {engine}: {case['claim']}\n"
            + json.dumps(mismatches, ensure_ascii=False, indent=2)
        )


def test_every_case_is_well_formed() -> None:
    clause_ids = {clause["id"] for clause in _UPSTREAM["clauses"]}
    assert _CASES
    for case_dir in _CASES:
        case = _case(case_dir)
        assert case["category"] in {"normative", "policy"}, case_dir
        assert case["clauses"], case_dir
        assert set(case["clauses"]) <= clause_ids, case_dir
        assert set(case["expected"]) <= _FIELDS, case_dir
        assert (case_dir / "bundle").is_dir(), case_dir
        divergence = case.get("divergence")
        if divergence is None:
            continue
        assert divergence["kind"], case_dir
        assert divergence["detail"], case_dir
        assert divergence["observed"], case_dir
        assert set(divergence["observed"]) <= _ENGINES, case_dir
        for fields in divergence["observed"].values():
            assert fields, case_dir
            assert set(fields) <= _FIELDS, case_dir
            # A recorded divergence must actually differ from what the case expects.
            assert all(case["expected"].get(key) != value for key, value in fields.items()), (
                case_dir
            )


def test_every_covered_clause_has_a_case() -> None:
    cited = {clause for case_dir in _CASES for clause in _case(case_dir)["clauses"]}
    for clause in _UPSTREAM["clauses"]:
        if clause["status"] == "covered":
            assert clause["id"] in cited, clause["id"]
        else:
            assert clause["status"] == "not-applicable", clause["id"]
            assert clause["reason"], clause["id"]


def test_upstream_pin_is_a_full_commit() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", _UPSTREAM["commit"])
    assert re.fullmatch(r"[0-9a-f]{64}", _UPSTREAM["specification"]["sha256"])
