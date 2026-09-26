"""Read a type's optional declared DuckDB SQL, per RFC 0006's trust model.

An optional ``.schema.sql`` file may sit beside a type's specification
document, at a path *derived* the same way `type_specs.py` derives the spec
document's own path - no `schema:` frontmatter field, for the same reason: a
declared path would be a second fact free to disagree with the first.

``.schema.sql`` is trusted DuckDB SQL, not a restricted data format: CTAS,
joins, `read_csv`/`read_json`, macros, temp tables, generated columns -
anything a dedicated DuckDB connection accepts is accepted here too, run
whole by the native binary (``okf-db/src/declared.rs``). Nothing inspects
statement shape or type; only the *post-condition* is checked afterward,
against the connection's own catalog: exactly one non-temporary table named
for the concept type must exist, with a queryable schema. Auxiliary tables the script created along
the way are not part of the contract and are ignored. Never run this
against a `.schema.sql` from a bundle you would not otherwise trust to
execute arbitrary code - it carries the same power as a Makefile or a
migration script, not the safety of a JSON Schema document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from okf_parser.duckdb_types import logical_type_from_catalog
from okf_parser.rust_core import native_result
from okf_parser.type_specs import spec_relative_path

if TYPE_CHECKING:
    from okf_parser.duckdb_types import DuckDBLogicalType


class DeclaredSchemaError(ValueError):
    """Raised when a `.schema.sql` script fails, or its post-condition doesn't hold."""


@dataclass(frozen=True, slots=True)
class DeclaredSchema:
    """One type's declared columns, decoded from its `.schema.sql` catalog entry."""

    table_name: str
    columns: dict[str, DuckDBLogicalType]
    table_comment: str | None
    column_comments: dict[str, str]


def declared_schema_relative_path(spec_template: str, concept_type: str) -> str | None:
    """Derive the `.schema.sql` path beside a type's specification document.

    Mirrors `type_specs.spec_relative_path`, then swaps the document's own
    extension for `.schema.sql` - the same derived-not-declared relationship
    the spec document itself has to `type`.
    """
    relative = spec_relative_path(spec_template, concept_type)
    if relative is None:
        return None
    stem = relative.rsplit(".", 1)[0] if "." in relative.rsplit("/", 1)[-1] else relative
    return f"{stem}.schema.sql"


class _DeclaredSchemaRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    sql: str
    concept_type: str


class _CatalogType(BaseModel):
    sql: str
    precision: int | None = None
    scale: int | None = None


class _Column(BaseModel):
    name: str
    logical_type: _CatalogType
    comment: str | None = None


class _Declared(BaseModel):
    table_name: str
    columns: list[_Column]
    table_comment: str | None = None


def parse_declared_schema(sql_text: str, concept_type: str) -> DeclaredSchema:
    """Run a `.schema.sql` script whole, then check only its post-condition.

    The binary hands the whole file to one dedicated in-memory DuckDB
    connection; DuckDB's own parser, binder, and planner decide what it means.
    Afterward the catalog must show exactly one non-temporary table whose
    identifier resolves as `concept_type` (DuckDB's ASCII case-insensitive
    identifier rules; the authored spelling is kept). Any other table the
    script created is not looked at.
    """
    result = native_result(
        "__declared-schema",
        _DeclaredSchemaRequest(sql=sql_text, concept_type=concept_type),
        {"declared_schema": DeclaredSchemaError},
    )
    declared = _Declared.model_validate(result)
    return DeclaredSchema(
        table_name=declared.table_name,
        columns={
            column.name: logical_type_from_catalog(
                column.logical_type.sql,
                numeric_precision=column.logical_type.precision,
                numeric_scale=column.logical_type.scale,
            )
            for column in declared.columns
        },
        table_comment=declared.table_comment,
        column_comments={
            column.name: column.comment for column in declared.columns if column.comment is not None
        },
    )
