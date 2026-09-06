"""RFC 0021 bundle relation SQL over real RFC 0006 typed tables."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser import load_bundle
from okf_parser.bundle_relations import BundleRelationsError
from okf_parser.typed_relations import compile_bundle_types

if TYPE_CHECKING:
    from pathlib import Path


def _bundle(root: Path) -> str:
    (root / "target.md").write_text(
        "---\ntype: Target\nkey: alpha\n---\n# Target\n",
        encoding="utf-8",
    )
    (root / "source.md").write_text(
        "---\ntype: Source\ntarget_key: alpha\n---\n# Source\n",
        encoding="utf-8",
    )
    types = root / "docs" / "types"
    types.mkdir(parents=True)
    (types / "target.schema.sql").write_text(
        'CREATE TABLE "Target" (key VARCHAR);\n',
        encoding="utf-8",
    )
    (types / "source.schema.sql").write_text(
        'CREATE TABLE "Source" (target_key VARCHAR);\n',
        encoding="utf-8",
    )
    return "docs/types/{slug}.md"


def _relation_sql(root: Path) -> None:
    (root / "okf.relations.sql").write_text(
        """CREATE VIEW okf_relations.edges AS
SELECT
    'Source'::VARCHAR AS source_type,
    source.__okf_concept_id AS source_id,
    'target'::VARCHAR AS predicate,
    'Target'::VARCHAR AS target_type,
    target.__okf_concept_id AS target_id,
    source.target_key AS target_ref,
    target.__okf_concept_id IS NOT NULL AS resolved,
    'producer-sql'::VARCHAR AS origin
FROM okf_types.\"Source\" AS source
LEFT JOIN okf_types.\"Target\" AS target
    ON target.key = source.target_key;
""",
        encoding="utf-8",
    )


def test_relation_sql_runs_after_real_typed_materialization(tmp_path: Path) -> None:
    template = _bundle(tmp_path)
    _relation_sql(tmp_path)

    with compile_bundle_types(load_bundle(tmp_path), template, relations=True) as compiled:
        assert compiled.tables == ("Source", "Target")
        assert compiled.relation_names == ("edges",)
        rows = compiled.bundle_relation("edges").execute().to_dict(orient="records")

    assert rows == [
        {
            "source_type": "Source",
            "source_id": "source",
            "predicate": "target",
            "target_type": "Target",
            "target_id": "target",
            "target_ref": "alpha",
            "resolved": True,
            "origin": "producer-sql",
        }
    ]


def test_relation_sql_is_explicit_opt_in(tmp_path: Path) -> None:
    template = _bundle(tmp_path)
    _relation_sql(tmp_path)

    with compile_bundle_types(load_bundle(tmp_path), template) as compiled:
        assert compiled.relation_names == ()
        with pytest.raises(KeyError, match="edges"):
            compiled.bundle_relation("edges")


def test_missing_relation_sql_is_valid_empty_catalog(tmp_path: Path) -> None:
    template = _bundle(tmp_path)

    with compile_bundle_types(load_bundle(tmp_path), template, relations=True) as compiled:
        assert compiled.relation_names == ()


def test_invalid_relation_sql_fails_explicitly(tmp_path: Path) -> None:
    template = _bundle(tmp_path)
    (tmp_path / "okf.relations.sql").write_text(
        "SELECT * FROM okf_types.missing_table;\n",
        encoding="utf-8",
    )

    with pytest.raises(BundleRelationsError, match="bundle relation SQL failed"):
        compile_bundle_types(load_bundle(tmp_path), template, relations=True)
