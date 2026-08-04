"""Tests for the optional type-specification rule."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from okf_parser.bundle import validate_path
from okf_parser.models import Severity
from okf_parser.type_specs import SpecTemplateError, spec_relative_path, type_slug

TEMPLATE = ".okf/specs/{slug}.md"
CASES = json.loads(
    (Path(__file__).parent.parent / "conformance" / "type-spec-slugs.json").read_text(
        encoding="utf-8"
    )
)


def _concept(root: Path, relative: str, concept_type: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {concept_type}\n---\n\n# {concept_type}\n", encoding="utf-8")


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_type_slug_matches_shared_conformance(case: dict[str, str]) -> None:
    assert type_slug(case["concept_type"]) == case["slug"]


def test_spec_relative_path_requires_the_slug_placeholder() -> None:
    with pytest.raises(SpecTemplateError, match="must contain"):
        spec_relative_path(".okf/specs/spec.md", "Spec")


def test_spec_relative_path_reports_a_type_without_a_slug() -> None:
    assert spec_relative_path(TEMPLATE, "概念") is None


def test_check_is_unchanged_without_the_rule(tmp_path: Path) -> None:
    _concept(tmp_path, "a.md", "Revisão Ciência")
    assert validate_path(tmp_path).violations == ()


def test_missing_specification_is_advisory(tmp_path: Path) -> None:
    _concept(tmp_path, "a.md", "Revisão Ciência")
    report = validate_path(tmp_path, (), TEMPLATE)
    assert report.is_conformant
    assert [(item.code, item.path, item.severity) for item in report.violations] == [
        ("OKF010", ".okf/specs/revisao-ciencia.md", Severity.WARNING)
    ]


def test_missing_specification_is_normative_under_the_flag(tmp_path: Path) -> None:
    _concept(tmp_path, "a.md", "Revisão Ciência")
    report = validate_path(tmp_path, (), TEMPLATE, normative_spec=True)
    assert not report.is_conformant
    assert report.violations[0].severity is Severity.ERROR


def test_present_specification_produces_no_diagnostic(tmp_path: Path) -> None:
    _concept(tmp_path, "a.md", "Revisão Ciência")
    _concept(tmp_path, ".okf/specs/revisao-ciencia.md", "Spec")
    _concept(tmp_path, ".okf/specs/spec.md", "Spec")
    assert validate_path(tmp_path, (), TEMPLATE).violations == ()


def test_each_type_is_reported_once(tmp_path: Path) -> None:
    for index in range(3):
        _concept(tmp_path, f"a{index}.md", "Revisão Ciência")
    report = validate_path(tmp_path, (), TEMPLATE)
    assert len(report.violations) == 1


def test_type_without_a_slug_is_reported_against_the_template(tmp_path: Path) -> None:
    _concept(tmp_path, "a.md", "概念")
    report = validate_path(tmp_path, (), TEMPLATE)
    assert (report.violations[0].code, report.violations[0].path) == ("OKF010", TEMPLATE)


def test_a_type_that_failed_to_parse_is_not_required_to_have_a_specification(
    tmp_path: Path,
) -> None:
    (tmp_path / "broken.md").write_text("no frontmatter\n", encoding="utf-8")
    report = validate_path(tmp_path, (), TEMPLATE)
    assert [item.code for item in report.violations] == ["OKF001"]
