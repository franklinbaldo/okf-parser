"""Conformance-kernel tests for the RFC 0012 impact oracle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conformance_kernel import canonicalize_result_impact, impact_result_digest

_ROOT = Path(__file__).parents[1] / "conformance" / "impact" / "v1"
_CASES = (
    "cycle-min-depth",
    "base-only-reachability",
    "head-only-reachability",
    "mixed-snapshot-path-is-not-a-path",
)


@pytest.mark.parametrize("case_name", _CASES)
def test_expected_impact_oracle_is_canonical_and_idempotent(case_name: str) -> None:
    case_root = _ROOT / case_name
    expected = (case_root / "expected.jsonl").read_bytes()

    canonical = canonicalize_result_impact(expected)

    assert canonical == expected
    assert canonicalize_result_impact(canonical) == canonical


@pytest.mark.parametrize("case_name", _CASES)
def test_impact_result_digest_is_independent_of_argument_map_order(case_name: str) -> None:
    case_root = _ROOT / case_name
    case = json.loads((case_root / "case.json").read_text(encoding="utf-8"))
    assert case.pop("operation") == "impact"
    expected = (case_root / "expected.jsonl").read_bytes()

    reverse_order = dict(reversed(tuple(case.items())))

    assert impact_result_digest(case, expected) == impact_result_digest(reverse_order, expected)


def test_empty_impact_result_is_empty_jsonl() -> None:
    assert canonicalize_result_impact(b"") == b""


@pytest.mark.parametrize(
    ("raw_result", "message"),
    [
        (
            b'{"concept_id":"A","depth":0,"support_depths":{"base":0},"planner":"hot"}\n',
            "invalid keys",
        ),
        (
            b'{"concept_id":"A","depth":1,"support_depths":{"base":0}}\n',
            "must equal min",
        ),
        (
            b'{"concept_id":"A","depth":0,"support_depths":{"future":0}}\n',
            "unexpected keys",
        ),
        (
            (
                b'{"concept_id":"A","depth":0,"support_depths":{"base":0}}\n'
                b'{"concept_id":"A","depth":0,"support_depths":{"head":0}}\n'
            ),
            "emit concept_id once",
        ),
    ],
)
def test_impact_oracle_rejects_non_contract_output(raw_result: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        canonicalize_result_impact(raw_result)
