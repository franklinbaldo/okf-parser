from __future__ import annotations

from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:80]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Remove the only layering violation from the shared planner: collision errors
# are domain data here and converted to the public BundleExportError by duckdb.py.
replace(
    "src/okf_parser/typed_tables.py",
    '''class TypedTableError(ValueError):
    """Raised when observed fields cannot form one deterministic type table."""


@dataclass(frozen=True, slots=True)
class TypedFieldPlan:
''',
    '''class TypedTableError(ValueError):
    """Raised when observed fields cannot form one deterministic type table."""


class TypedTableCollisionError(TypedTableError):
    """Raised when the persistent typed schema already owns a planned table name."""

    def __init__(self, schema_name: str, tables: tuple[str, ...]) -> None:
        self.schema_name = schema_name
        self.tables = tables
        super().__init__(f"schema {schema_name!r} already contains {', '.join(tables)}")


@dataclass(frozen=True, slots=True)
class TypedFieldPlan:
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''    if collisions and not overwrite:
        from okf_parser.duckdb import BundleExportError

        raise BundleExportError(schema, tuple(sorted(collisions)))
''',
    '''    if collisions and not overwrite:
        raise TypedTableCollisionError(schema, tuple(sorted(collisions)))
''',
)

# Wire declaration discovery + typed materialization into the persistent DuckDB
# transaction. No spec template means the old four-table contract is untouched.
replace(
    "src/okf_parser/duckdb.py",
    '''from okf_parser.bundle import Bundle, load_bundle
''',
    '''from okf_parser.bundle import Bundle, load_bundle
from okf_parser.typed_tables import (
    TypedTableCollisionError,
    discover_declared_schemas,
    materialize_typed_tables,
)
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''    overwrite: bool = False,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
''',
    '''    overwrite: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''    _validate_schema_name(schema)
    bundle = load_bundle(Path(path), exclude)
    relations = {
''',
    '''    _validate_schema_name(schema)
    bundle = load_bundle(Path(path), exclude)
    typed_schema = f"{schema}_types"
    declarations = discover_declared_schemas(
        bundle.root,
        bundle.concept_types,
        spec_template,
    )
    relations = {
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''        for table_name, relation in relations.items():
            _replace_table(connection, schema, table_name, relation)
        connection.execute("COMMIT")
''',
    '''        for table_name, relation in relations.items():
            _replace_table(connection, schema, table_name, relation)
        typed = None
        if spec_template is not None:
            try:
                typed = materialize_typed_tables(
                    connection,
                    bundle,
                    schema=typed_schema,
                    declarations=declarations,
                    overwrite=overwrite,
                )
            except TypedTableCollisionError as exc:
                raise BundleExportError(exc.schema_name, exc.tables) from exc
        connection.execute("COMMIT")
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''    return {
        "schema": schema,
        "root": str(bundle.root),
        "conformant": bundle.is_conformant,
        "markdown_count": bundle.markdown_count,
        "concept_count": cast("int", bundle.concepts.count().execute()),
        "link_count": cast("int", bundle.links.count().execute()),
        "diagnostic_count": len(bundle.diagnostics),
    }
''',
    '''    result: dict[str, object] = {
        "schema": schema,
        "root": str(bundle.root),
        "conformant": bundle.is_conformant,
        "markdown_count": bundle.markdown_count,
        "concept_count": cast("int", bundle.concepts.count().execute()),
        "link_count": cast("int", bundle.links.count().execute()),
        "diagnostic_count": len(bundle.diagnostics),
    }
    if spec_template is not None and typed is not None:
        result.update(
            {
                "typed_schema": typed.schema,
                "typed_table_count": len(typed.tables),
                "typed_tables": list(typed.tables),
                "unrecognized_type_tables": list(typed.unrecognized_tables),
            }
        )
    return result
''',
)

# Service + CLI pass the same template already used by schema/init.
replace(
    "src/okf_parser/service.py",
    '''    overwrite: bool = False,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Materialize an OKF bundle into a DuckDB database file."""
''',
    '''    overwrite: bool = False,
    exclude: Sequence[str] = (),
    spec_template: str | None = None,
) -> dict[str, object]:
    """Materialize an OKF bundle into a DuckDB database file."""
''',
)
replace(
    "src/okf_parser/service.py",
    '''            overwrite=overwrite,
            exclude=exclude,
        )
''',
    '''            overwrite=overwrite,
            exclude=exclude,
            spec_template=spec_template,
        )
''',
)
replace(
    "src/okf_parser/cli.py",
    '''    overwrite: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
''',
    '''    overwrite: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
''',
)
replace(
    "src/okf_parser/cli.py",
    '''            export_duckdb(path, database, schema, overwrite=overwrite, exclude=exclude or ())
''',
    '''            export_duckdb(
                path,
                database,
                schema,
                overwrite=overwrite,
                exclude=exclude or (),
                spec_template=spec_template,
            )
''',
)

# Document the new persistent typed surface.
replace(
    "docs/cli.md",
    '''uv run okf-parser duckdb path/to/bundle [database] [schema] [--overwrite] [--exclude PATTERN]...
''',
    '''uv run okf-parser duckdb path/to/bundle [database] [schema] [--overwrite] [--spec-template TEMPLATE] [--exclude PATTERN]...
''',
)
replace(
    "docs/cli.md",
    '''- `--overwrite` — replace existing tables in that schema instead of failing.

On a name collision without `--overwrite`, exits `1` with
''',
    '''- `--overwrite` — replace existing tables in that schema instead of failing.
- `--spec-template TEMPLATE` — execute each present sibling `.schema.sql` and
  materialize declared concept types into a second `{schema}_types` schema.
  Declared values keep a compiler-owned raw column beside a generated typed
  `TRY_CAST` projection; types without a declaration remain available only in
  the complete untyped `concepts` table.

On a name collision without `--overwrite`, exits `1` with
''',
)

# Version 0.17.0 across all synchronized release surfaces.
for path in (
    "pyproject.toml",
    "typescript/package.json",
    "typescript-duckdb/package.json",
    "typescript/src/version.ts",
):
    text = Path(path).read_text(encoding="utf-8")
    Path(path).write_text(text.replace("0.16.0", "0.17.0"), encoding="utf-8")

Path("changelog/0.17.0.md").write_text(
    '''---
type: Release
title: okf-parser 0.17.0
description: materialize declared RFC 0006 types into persistent DuckDB schemas
---

# okf-parser 0.17.0

## Persistent declared-type tables

`duckdb --spec-template` now carries RFC 0006 declarations into the database
artifact instead of limiting them to schema export.

- declared types materialize in `{schema}_types`, keeping the existing four
  contract tables in `{schema}` unchanged;
- each declared field persists a compiler-owned raw `VARCHAR`/`VARCHAR[]`
  source beside a DuckDB generated column using per-value `TRY_CAST`;
- one incompatible value therefore becomes typed `NULL` only for that row;
  the authored raw value remains queryable and the rest of the column stays typed;
- declared-but-unobserved fields still exist with their declared DuckDB type;
- observed undeclared scalar fields remain `VARCHAR` rather than being suppressed;
- table and column comments from `.schema.sql` are persisted, while existing
  comments not replaced by the declaration survive an explicit overwrite;
- typed-table collisions use the existing `BundleExportError`/`--overwrite`
  policy, and unrecognized tables in `{schema}_types` are reported but never
  deleted automatically.

The field planner is shared infrastructure for the next RFC 0006 milestone:
using the same raw-plus-generated representation in `apply`.
''',
    encoding="utf-8",
)

# Focused integration coverage.
path = Path("tests/test_duckdb.py")
text = path.read_text(encoding="utf-8")
text = text.replace(
    "from typing import TYPE_CHECKING\n",
    "from decimal import Decimal\nfrom typing import TYPE_CHECKING\n",
    1,
)
text = text.replace(
    "from okf_parser.duckdb import BundleExportError, attach_okf\n",
    "from okf_parser.duckdb import BundleExportError, attach_okf\nfrom okf_parser.service import export_duckdb\n",
    1,
)
text += r'''


def _write_declared_bundle(tmp_path: Path) -> str:
    (tmp_path / "a.md").write_text(
        "---\ntype: Rotina\ncusto: 12.50\nregistrado_em: 2026-08-07T10:00:00Z\n"
        "codigo: alpha\ntags:\n  - 1\n  - 2\n---\nA\n",
        encoding="utf-8",
    )
    (tmp_path / "b.md").write_text(
        "---\ntype: Rotina\ncusto: n/a\nregistrado_em: 2026-08-07T11:00:00Z\n"
        "codigo: beta\ntags:\n  - 3\n  - x\n---\nB\n",
        encoding="utf-8",
    )
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (\n'
        '  custo DECIMAL(18,2),\n'
        '  registrado_em TIMESTAMPTZ,\n'
        '  futuro BIGINT,\n'
        '  tags BIGINT[]\n'
        ');\n'
        'COMMENT ON TABLE "Rotina" IS \'Typed routines\';\n'
        'COMMENT ON COLUMN "Rotina".custo IS \'Exact cost\';\n',
        encoding="utf-8",
    )
    return "docs/types/{slug}.md"


def test_attach_okf_materializes_declared_types_per_value(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()

    result = attach_okf(connection, tmp_path, spec_template=template)

    assert result["typed_schema"] == "okf_types"
    assert result["typed_tables"] == ["Rotina"]
    rows = connection.execute(
        'SELECT "__okf_raw_custo", custo, futuro, codigo, "__okf_raw_tags", tags '
        'FROM okf_types."Rotina" ORDER BY "__okf_path"'
    ).fetchall()
    assert rows[0] == ("12.50", Decimal("12.50"), None, "alpha", ["1", "2"], [1, 2])
    assert rows[1] == ("n/a", None, None, "beta", ["3", "x"], [3, None])

    described = dict(
        connection.execute('DESCRIBE okf_types."Rotina"').fetchall()[i][:2]
        for i in range(len(connection.execute('DESCRIBE okf_types."Rotina"').fetchall()))
    )
    assert described["custo"] == "DECIMAL(18,2)"
    assert described["registrado_em"] == "TIMESTAMP WITH TIME ZONE"
    assert described["futuro"] == "BIGINT"
    assert described["tags"] == "BIGINT[]"
    assert described["codigo"] == "VARCHAR"

    table_comment = connection.execute(
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'"
    ).fetchone()
    column_comment = connection.execute(
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'"
    ).fetchone()
    assert table_comment == ("Typed routines",)
    assert column_comment == ("Exact cost",)


def test_attach_okf_uses_catalog_shape_from_ctas_declaration(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("---\ntype: Rotina\ncusto: 7.25\n---\n", encoding="utf-8")
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        "CREATE TEMP TABLE staging(custo VARCHAR);\n"
        "INSERT INTO staging VALUES ('1.25');\n"
        'CREATE TABLE "Rotina" AS SELECT TRY_CAST(custo AS DECIMAL(8,2)) AS custo FROM staging;\n',
        encoding="utf-8",
    )
    connection = duckdb.connect()

    attach_okf(connection, tmp_path, spec_template="docs/types/{slug}.md")

    assert connection.execute('SELECT custo FROM okf_types."Rotina"').fetchone() == (
        Decimal("7.25"),
    )
    assert connection.execute(
        "SELECT data_type FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='custo'"
    ).fetchone() == ("DECIMAL(8,2)",)


def test_typed_table_collision_uses_existing_overwrite_contract(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute('CREATE SCHEMA okf_types')
    connection.execute('CREATE TABLE okf_types."Rotina" (manual VARCHAR)')

    with pytest.raises(BundleExportError) as excinfo:
        attach_okf(connection, tmp_path, spec_template=template)

    assert excinfo.value.schema_name == "okf_types"
    assert excinfo.value.tables == ("Rotina",)
    assert connection.execute('DESCRIBE okf_types."Rotina"').fetchone()[0] == "manual"


def test_typed_overwrite_preserves_undeclared_comments(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute('CREATE SCHEMA okf_types')
    connection.execute('CREATE TABLE okf_types."Rotina" (codigo VARCHAR)')
    connection.execute('COMMENT ON TABLE okf_types."Rotina" IS \'old table comment\'')
    connection.execute('COMMENT ON COLUMN okf_types."Rotina".codigo IS \'old code comment\'')

    attach_okf(connection, tmp_path, spec_template=template, overwrite=True)

    assert connection.execute(
        "SELECT comment FROM duckdb_columns() "
        "WHERE schema_name='okf_types' AND table_name='Rotina' AND column_name='codigo'"
    ).fetchone() == ("old code comment",)
    # The declaration owns table comments when it supplies one.
    assert connection.execute(
        "SELECT comment FROM duckdb_tables() WHERE schema_name='okf_types' AND table_name='Rotina'"
    ).fetchone() == ("Typed routines",)


def test_typed_materialization_reports_unrecognized_tables_without_dropping(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    connection = duckdb.connect()
    connection.execute('CREATE SCHEMA okf_types')
    connection.execute('CREATE TABLE okf_types.Legacy (id INTEGER)')

    result = attach_okf(connection, tmp_path, spec_template=template)

    assert result["unrecognized_type_tables"] == ["Legacy"]
    assert connection.execute("SELECT count(*) FROM okf_types.Legacy").fetchone() == (0,)
    assert connection.execute('SELECT count(*) FROM okf_types."Rotina"').fetchone() == (2,)


def test_export_duckdb_reopens_with_typed_tables(tmp_path: Path) -> None:
    template = _write_declared_bundle(tmp_path)
    database = tmp_path / "knowledge.duckdb"

    result = export_duckdb(
        str(tmp_path),
        str(database),
        spec_template=template,
    )

    assert result["typed_table_count"] == 1
    reopened = duckdb.connect(database)
    try:
        assert reopened.execute(
            'SELECT count(*) FROM okf_types."Rotina" WHERE custo IS NOT NULL'
        ).fetchone() == (1,)
    finally:
        reopened.close()
'''
path.write_text(text, encoding="utf-8")
