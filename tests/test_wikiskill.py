"""Tests for optional WikiSkill ergonomics over canonical OKF relations."""

from pathlib import Path

import pytest

from okf_parser import load_bundle
from okf_parser.wikiskill import wikiskill_view


def _write_concept(root: Path, relative: str, concept_type: str, body: str = "") -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ntype: {concept_type}\n---\n\n{body}\n", encoding="utf-8")


def test_wikiskill_inventory_and_lineage_use_existing_bundle_graph(tmp_path: Path) -> None:
    _write_concept(tmp_path, "experiences/run.md", "experience")
    _write_concept(
        tmp_path,
        "wiki/pagination.md",
        "wiki-entry",
        "Evidence: [run](../experiences/run.md)",
    )
    _write_concept(
        tmp_path,
        "skills/acquire.md",
        "agent-skill",
        "Knowledge: [pagination](../wiki/pagination.md)",
    )
    _write_concept(
        tmp_path,
        "proposals/retry.md",
        "skill-proposal",
        "Skill: [acquire](../skills/acquire.md)",
    )
    _write_concept(
        tmp_path,
        "evaluations/retry.md",
        "skill-evaluation",
        "Proposal: [retry](../proposals/retry.md)",
    )

    view = wikiskill_view(load_bundle(tmp_path))

    assert view.inventory().as_dict() == {
        "evaluations": 1,
        "experiences": 1,
        "orphan_wiki_entries": 0,
        "proposals": 1,
        "skills": 1,
        "unevaluated_proposals": 0,
        "wiki_entries": 1,
    }
    assert [item.path for item in view.lineage("skills/acquire.md")] == [
        "evaluations/retry.md",
        "experiences/run.md",
        "proposals/retry.md",
        "skills/acquire.md",
        "wiki/pagination.md",
    ]


def test_wikiskill_surfaces_orphans_and_unevaluated_proposals(tmp_path: Path) -> None:
    _write_concept(tmp_path, "wiki/orphan.md", "wiki-entry")
    _write_concept(tmp_path, "proposals/pending.md", "skill-proposal")

    view = wikiskill_view(load_bundle(tmp_path))

    assert [item.path for item in view.orphan_wiki_entries()] == ["wiki/orphan.md"]
    assert [item.path for item in view.unevaluated_proposals()] == ["proposals/pending.md"]


def test_wikiskill_lineage_rejects_unknown_concept(tmp_path: Path) -> None:
    _write_concept(tmp_path, "wiki/known.md", "wiki-entry")
    view = wikiskill_view(load_bundle(tmp_path))

    with pytest.raises(KeyError, match="not found or ambiguous"):
        view.lineage("wiki/missing.md")
