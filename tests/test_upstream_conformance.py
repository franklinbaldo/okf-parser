"""Executable OKF conformance corpus pinned to an upstream specification revision.

Each case under ``conformance/upstream/<version>/<name>/`` holds a ``bundle/``
directory and a ``case.json`` stating what a consumer must observe. Only the
keys present in ``expected`` are asserted, so a case pins exactly the claim it
names. A failure message says which of three things broke:

- ``engine-divergence``: the Python and Rust engines disagree with each other;
- ``normative-regression`` / ``policy-regression``: the engines agree with each
  other but no longer match the case;
- a case carrying ``divergence`` is a known disagreement with upstream and is a
  strict xfail, so fixing it forces the case to be updated.
"""

from __future__ import annotations

import json
import math
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


def _case(case_dir: Path) -> dict[str, Any]:
    return json.loads((case_dir / "case.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _text(value: object) -> str | None:
    return None if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def observe(bundle: Bundle) -> dict[str, Any]:
    """Project a loaded bundle onto the engine-neutral shape the corpus asserts."""
    concepts = bundle.concepts.execute().to_dict(orient="records")
    return {
        "conformant": bundle.is_conformant,
        "concepts": {row["path"]: row["concept_type"] for row in concepts},
        "reserved": sorted(
            row["path"] for row in bundle.reserved.execute().to_dict(orient="records")
        ),
        "diagnostics": [
            {"code": item.code, "severity": item.severity.value, "path": item.path}
            for item in bundle.validate()
        ],
        "links": [
            {
                "source": row["source_id"],
                "raw_target": row["raw_target"],
                "target": _text(row["target_id"]),
                "exists": bool(row["exists"]),
            }
            for row in bundle.links.execute().to_dict(orient="records")
        ],
        "frontmatter": {row["path"]: json.loads(row["frontmatter_json"]) for row in concepts},
    }


def _engines(bundle_dir: Path) -> dict[str, dict[str, Any]]:
    observed = {"python": observe(load_bundle(bundle_dir))}
    if _EXECUTABLE is not None:
        observed["rust"] = observe(load_bundle(bundle_dir, rust_core=Path(_EXECUTABLE)))
    return observed


def _mismatches(expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, object]:
    return {
        key: {"expected": value, "observed": observed[key]}
        for key, value in expected.items()
        if observed[key] != value
    }


@pytest.mark.parametrize("case_dir", _CASES, ids=lambda path: f"{path.parent.name}/{path.name}")
def test_upstream_case(case_dir: Path, request: pytest.FixtureRequest) -> None:
    case = _case(case_dir)
    divergence = case.get("divergence")
    if divergence is not None:
        request.applymarker(
            pytest.mark.xfail(strict=True, reason=f"{divergence['kind']}: {divergence['detail']}")
        )

    observed = _engines(case_dir / "bundle")

    reference = observed["python"]
    for engine, result in observed.items():
        assert result == reference, f"engine-divergence: python != {engine}"

    mismatches = _mismatches(case["expected"], reference)
    assert not mismatches, (
        f"{case['category']}-regression in {case_dir.name}: {case['claim']}\n"
        + json.dumps(mismatches, ensure_ascii=False, indent=2)
    )


def test_every_case_is_well_formed() -> None:
    clause_ids = {clause["id"] for clause in _UPSTREAM["clauses"]}
    allowed = {"conformant", "concepts", "reserved", "diagnostics", "links", "frontmatter"}
    assert _CASES
    for case_dir in _CASES:
        case = _case(case_dir)
        assert case["category"] in {"normative", "policy"}, case_dir
        assert case["clauses"], case_dir
        assert set(case["clauses"]) <= clause_ids, case_dir
        assert set(case["expected"]) <= allowed, case_dir
        assert (case_dir / "bundle").is_dir(), case_dir


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
