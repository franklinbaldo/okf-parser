"""Contract-first schema export for declared types with no instances yet."""

from pathlib import Path

from okf_parser.schema_export import build_schema_contracts, export_pydantic_source


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_declared_type_without_documents_is_exported(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    specs = tmp_path / "specs"
    knowledge.mkdir()

    _write(
        specs / "run-goal.md",
        """---
type: ConceptSpecification
concept_type: RunGoal
description: Goal contract.
---

# RunGoal
""",
    )
    _write(
        specs / "run-goal.schema.sql",
        'CREATE TABLE "RunGoal" ("id" VARCHAR, "goal" VARCHAR);\n',
    )

    contracts = build_schema_contracts(
        str(knowledge),
        spec_template="../specs/{slug}.md",
    )

    assert {contract.concept_type for contract in contracts} == {"RunGoal"}
    source = export_pydantic_source(
        str(knowledge),
        spec_template="../specs/{slug}.md",
    )
    assert "class RunGoalConcept(BaseModel):" in source
    assert "id: str" in source
    assert "goal: str" in source


def test_declared_empty_type_joins_observed_types(tmp_path: Path) -> None:
    knowledge = tmp_path / "knowledge"
    specs = tmp_path / "specs"
    _write(
        knowledge / "observed.md",
        """---
type: Observed
id: observed-1
---
""",
    )
    _write(
        specs / "observed.md",
        """---
type: ConceptSpecification
concept_type: Observed
description: Observed type.
---
""",
    )
    _write(specs / "observed.schema.sql", 'CREATE TABLE "Observed" ("id" VARCHAR);\n')
    _write(
        specs / "future.md",
        """---
type: ConceptSpecification
concept_type: Future
description: Future type.
---
""",
    )
    _write(specs / "future.schema.sql", 'CREATE TABLE "Future" ("id" VARCHAR);\n')

    contracts = build_schema_contracts(
        str(knowledge),
        spec_template="../specs/{slug}.md",
    )

    assert {contract.concept_type for contract in contracts} == {"Observed", "Future"}
