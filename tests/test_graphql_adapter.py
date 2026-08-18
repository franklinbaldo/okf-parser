"""Read-only GraphQL projection and executable adapter coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.graphql_adapter import (
    GraphQLNameCollisionError,
    GraphQLReadAdapter,
    export_graphql_sdl,
)

if TYPE_CHECKING:
    from pathlib import Path


def _bundle(root: Path) -> str:
    (root / "a.md").write_text(
        """---
type: Rotina
title: Alpha
custo: 12.50
big: 9223372036854775807
dia: 2026-08-18
uid: 123e4567-e89b-12d3-a456-426614174000
tags:
  - um
  - dois
---
# Alpha

[Beta](b.md)
""",
        encoding="utf-8",
    )
    (root / "b.md").write_text(
        """---
type: Rotina
title: Beta
custo: 3.25
big: 7
dia: 2026-08-19
uid: 123e4567-e89b-12d3-a456-426614174001
tags:
  - tres
---
# Beta
""",
        encoding="utf-8",
    )
    types = root / "types"
    types.mkdir()
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" ('
        "custo DECIMAL(18,2), "
        "big BIGINT, "
        "dia DATE, "
        "uid UUID, "
        "tags VARCHAR[]"
        ");\n",
        encoding="utf-8",
    )
    return "types/{slug}.md"


def test_graphql_sdl_is_deterministic_and_read_only(tmp_path: Path) -> None:
    template = _bundle(tmp_path)

    first = export_graphql_sdl(str(tmp_path), spec_template=template)
    second = export_graphql_sdl(str(tmp_path), spec_template=template)

    assert first == second
    assert "interface Concept" in first
    assert 'type RotinaConcept implements Concept @okfType(name: "Rotina")' in first
    assert "custo: Decimal!" in first
    assert "big: BigInt!" in first
    assert "dia: Date!" in first
    assert "uid: UUID!" in first
    assert "tags: [String!]!" in first
    assert "type Mutation" not in first


def test_graphql_adapter_queries_typed_relations_links_and_pagination(tmp_path: Path) -> None:
    template = _bundle(tmp_path)
    adapter = GraphQLReadAdapter(str(tmp_path), spec_template=template)

    result = adapter.execute(
        """
        query {
          concepts(first: 1, offset: 0) {
            id
            path
            type
            title
            links { targetId exists }
            reverseLinks { sourceId }
            ... on RotinaConcept {
              custo
              big
              dia
              uid
              tags
            }
          }
        }
        """
    )

    assert result.errors == ()
    assert result.data is not None
    concepts = result.data["concepts"]
    assert isinstance(concepts, list)
    assert len(concepts) == 1
    concept = concepts[0]
    assert isinstance(concept, dict)
    assert concept["path"] == "a.md"
    assert concept["type"] == "Rotina"
    assert concept["custo"] == "12.50"
    assert concept["big"] == "9223372036854775807"
    assert concept["dia"] == "2026-08-18"
    assert concept["uid"] == "123e4567-e89b-12d3-a456-426614174000"
    assert concept["tags"] == ["um", "dois"]
    assert concept["links"][0]["exists"] is True

    concept_id = concept["id"]
    by_id = adapter.execute(
        """
        query($id: ID!) {
          concept(id: $id) {
            id
            path
            ... on RotinaConcept { custo }
          }
        }
        """,
        {"id": concept_id},
    )
    assert by_id.errors == ()
    assert by_id.data == {
        "concept": {"id": concept_id, "path": "a.md", "custo": "12.50"}
    }

    filtered = adapter.execute(
        """
        query {
          concepts(type: "missing", first: 10) { id }
        }
        """
    )
    assert filtered.errors == ()
    assert filtered.data == {"concepts": []}


def test_graphql_pagination_fails_closed_outside_bounds(tmp_path: Path) -> None:
    _bundle(tmp_path)
    adapter = GraphQLReadAdapter(str(tmp_path))

    result = adapter.execute("{ concepts(first: 1001) { id } }")

    assert result.data is None
    assert result.errors
    assert "pagination requires" in result.errors[0]


def test_graphql_aliases_invalid_field_names_with_provenance(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        """---
type: "Ação"
"id": producer-id
"bad-name": value
---
# A
""",
        encoding="utf-8",
    )

    sdl = export_graphql_sdl(str(tmp_path))

    assert 'type AcaoConcept implements Concept @okfType(name: "Ação")' in sdl
    assert 'field_id: String! @okfField(name: "id")' in sdl
    assert 'field_bad_name: String! @okfField(name: "bad-name")' in sdl


def test_graphql_field_alias_collisions_fail_explicitly(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        """---
type: Note
"a-b": one
"a b": two
---
# A
""",
        encoding="utf-8",
    )

    with pytest.raises(GraphQLNameCollisionError, match=r"Note\.a-b|Note\.a b"):
        export_graphql_sdl(str(tmp_path))
