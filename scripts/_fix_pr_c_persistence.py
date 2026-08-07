from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Persistent DuckDB exports store the typed snapshot as an ordinary column.
# DuckDB 1.5 generated columns are VIRTUAL only, so using one here would
# recompute the typed value on every read and make offset-less TIMESTAMPTZ
# casts depend on the reader's session timezone.
replace(
    "src/okf_parser/typed_tables.py",
    '''        columns.append(f"{_quote_ident(raw_name)} {raw_sql_type}")
        columns.append(
            f"{_quote_ident(field.name)} {declared_type.sql} GENERATED ALWAYS AS "
            f"(TRY_CAST({_quote_ident(raw_name)} AS {declared_type.sql}))"
        )
''',
    '''        columns.append(f"{_quote_ident(raw_name)} {raw_sql_type}")
        columns.append(f"{_quote_ident(field.name)} {declared_type.sql}")
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''def _apply_comments(
''',
    '''def _populate_typed_columns(
    connection: duckdb.DuckDBPyConnection,
    schema: str,
    plan: TypedTablePlan,
) -> None:
    assignments: list[str] = []
    for field in plan.fields:
        declared_type = field.declared_type
        if declared_type is None:
            continue
        raw_name = _insert_name(field)
        assignments.append(
            f"{_quote_ident(field.name)} = "
            f"TRY_CAST({_quote_ident(raw_name)} AS {declared_type.sql})"
        )
    if not assignments:
        return

    timezone_row = connection.execute("SELECT current_setting('TimeZone')").fetchone()
    if timezone_row is None:
        message = "DuckDB did not expose the current TimeZone setting"
        raise TypedTableError(message)
    previous_timezone = str(timezone_row[0])
    connection.execute("SET TimeZone = 'UTC'")
    try:
        connection.execute(
            f"UPDATE {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
            f"SET {', '.join(assignments)}"
        )
    finally:
        connection.execute(f"SET TimeZone = {_quote_literal(previous_timezone)}")


def _apply_comments(
''',
)
replace(
    "src/okf_parser/typed_tables.py",
    '''            connection.executemany(
                f"INSERT INTO {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
                f"({column_sql}) VALUES ({placeholders})",
                [_row_values(row, plan) for row in type_rows],
            )
        _apply_comments(
''',
    '''            connection.executemany(
                f"INSERT INTO {_quote_ident(schema)}.{_quote_ident(plan.concept_type)} "
                f"({column_sql}) VALUES ({placeholders})",
                [_row_values(row, plan) for row in type_rows],
            )
        _populate_typed_columns(connection, schema, plan)
        _apply_comments(
''',
)

# Fail cheap core-table collisions before executing trusted declaration SQL.
replace(
    "src/okf_parser/duckdb.py",
    '''    typed_schema = f"{schema}_types"
    declarations = discover_declared_schemas(
        bundle.root,
        bundle.concept_types,
        spec_template,
    )
    relations = {
''',
    '''    typed_schema = f"{schema}_types"
    relations = {
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''    if collisions and not overwrite:
        raise BundleExportError(schema, collisions)

    connection.execute("BEGIN TRANSACTION")
''',
    '''    if collisions and not overwrite:
        raise BundleExportError(schema, collisions)

    declarations = discover_declared_schemas(
        bundle.root,
        bundle.concept_types,
        spec_template,
    )

    connection.execute("BEGIN TRANSACTION")
''',
)
replace(
    "src/okf_parser/duckdb.py",
    '''    Materializing twice into the same schema raises :class:`BundleExportError`
    unless ``overwrite`` is set, in which case the four tables are replaced.
''',
    '''    Materializing twice into the same schema raises :class:`BundleExportError`
    unless ``overwrite`` is set, in which case the four tables are replaced.
    With ``spec_template``, declared concept types are additionally persisted
    in ``{schema}_types`` as raw columns plus stable typed snapshot columns.
''',
)

replace(
    "docs/cli.md",
    '''  Declared values keep a compiler-owned raw column beside a generated typed
  `TRY_CAST` projection; types without a declaration remain available only in
''',
    '''  Declared values keep a compiler-owned raw column beside a materialized typed
  `TRY_CAST` projection; types without a declaration remain available only in
''',
)
replace(
    "changelog/0.17.0.md",
    '''- each declared field persists a compiler-owned raw `VARCHAR`/`VARCHAR[]`
  source beside a DuckDB generated column using per-value `TRY_CAST`;
''',
    '''- each declared field persists a compiler-owned raw `VARCHAR`/`VARCHAR[]`
  source beside a stable typed snapshot populated with per-value DuckDB `TRY_CAST`;
''',
)

# Bring the RFC summary and decisions back into line with the implementation.
replace(
    "rfcs/0006-declared-column-types.md",
    '''Declaration buys **validation**, not obedience: a declared type that the
data doesn't support degrades that one column back to `VARCHAR` and reports
it. The bundle still opens, the table still builds, the export still
emits. Divergence is advisory by default and changes only the exit code
when the caller explicitly asks it to (decision 8).

Scope is deliberately the *reading* half. v1 reads three things from a
declaration — column names, column types, comments — over a closed set of
types (decision 5a). Constraints are not read (decision 5), typed columns
are readable but not writable in `apply` (decision 7a), and nothing writes
back into the declaration file (deferred to RFC 0007). Each of those is a
serialization or evaluation problem that does not have to be solved for a
declared type to be useful today.

**A declared column is never the only copy of a document's data.**
Underneath every declared column sits a hidden, unconditionally lossless
raw column — `VARCHAR` for a scalar field, `VARCHAR[]` for a declared
`T[]` field — holding the field exactly as authored; the declared column
is a DuckDB *generated* column computed from it. Casting a document into a
physical type can round, truncate, or fail outright — that is inherent to
what a physical type is — but it never has to be the thing that decides
whether the original text survives. The raw column is compiler-owned:
under the `__okf_` prefix `apply` already reserves, protected the same way
`apply` already protects its other internal columns, never a target an
`--sql` script can rename or overwrite. Decision 5 is where this is built
and why two earlier drafts of the cast rule needed it.
''',
    '''Declaration buys **typed intent without sacrificing source fidelity**. A
declared field stays typed even when individual rows diverge: DuckDB's
`TRY_CAST` is applied per value, and an incompatible value becomes `NULL`
only in the typed projection while the raw carrier remains available. One
bad document never widens the whole column back to `VARCHAR`. Divergence is
advisory by default (decisions 5 and 8).

v1 reads column names, normalized logical types, and comments from the
declaration catalog. Constraints are not read. `schema` exports the lossless
type IR; persistent `duckdb` materializes raw plus typed snapshot columns;
`apply` is the remaining integration step and will use the same projection as
a generated read-only column (decision 7a). Nothing writes back into the
declaration file (deferred to RFC 0007).

**A declared column is never the only copy of a document's data.** The
compiler keeps a raw carrier beside the typed projection — `VARCHAR` for a
scalar field and `VARCHAR[]` for declared `T[]`. `duckdb` stores the typed
projection as a snapshot value because DuckDB generated columns are currently
virtual; `apply` uses a generated column because there the database-enforced
read-only property is useful. Both consumers use the same DuckDB `TRY_CAST`
semantics. The raw column is compiler-owned under the existing `__okf_`
boundary.
''',
)
replace(
    "rfcs/0006-declared-column-types.md",
    '''### 5. Raw is the truth; typed is a per-value generated projection over it

This decision is closed. A declared field has two physical representations when
`apply`/`duckdb` materialization lands: a compiler-owned lossless raw column and a
public typed generated column. The raw column is `VARCHAR` for a scalar and
`VARCHAR[]` for `T[]`; the typed column is exactly DuckDB's own projection:

```sql
"__okf_raw_custo" VARCHAR,
"custo" DECIMAL(18,4) GENERATED ALWAYS AS (
    TRY_CAST(__okf_raw_custo AS DECIMAL(18,4))
)
```
''',
    '''### 5. Raw is the truth; typed is a per-value DuckDB projection over it

This decision is closed. A declared field has two logical representations: a
compiler-owned raw carrier and a public typed projection. The raw column is
`VARCHAR` for a scalar and `VARCHAR[]` for `T[]`; the typed value is always
DuckDB's own `TRY_CAST` of that carrier.

The two relational consumers choose different physical storage for the same
projection. Persistent `duckdb` stores the typed value as an ordinary column,
computed once during export with `TimeZone = 'UTC'`, so reopening the database
under a different session timezone cannot change the snapshot. `apply` uses a
DuckDB generated column over the same raw carrier, because DuckDB then enforces
that the typed projection is read-only. Generated columns are virtual in the
DuckDB version targeted here, so they are deliberately not used as persistent
snapshot storage.
''',
)
replace(
    "rfcs/0006-declared-column-types.md",
    '''- **failing-cast fallback**, when the declaration is well-formed and the
  column's type is supported, but the data does not satisfy it (decision
  5). That one column materializes as `VARCHAR`; the declared type is
  **still exported** (decision 9), because it remains an intelligible
  statement of intent; the comment still applies; every other column is
  untouched;
- **unsupported-type fallback**, when the column's declared type is
  outside the v1 set (decision 5a). That column materializes *and exports*
  as if undeclared — inference, not declaration — because decision 10's
  mapping has no form for it. The comment still applies. The declared type
  survives only in the diagnostic.
''',
    '''- **data divergence has no schema fallback**. When the declaration is
  well-formed but one value does not cast (decision 5), only that row's typed
  projection is `NULL`; its raw carrier remains available, the column stays
  declared, and the comment still applies;
- **target capability is local to the target**. A DuckDB physical type that a
  JSON/Zod exporter cannot standardize remains representable by the DuckDB
  materializer using its catalog spelling; narrower exporters keep the exact
  type in metadata rather than inventing a false standard type (decisions 5a
  and 10).
''',
)
replace(
    "rfcs/0006-declared-column-types.md",
    '''takes decision 5's predicate returning false for any row. Unsupported-type
fallback takes decision 5a's set, and a declared reserved column (decision
5b) behaves the same way. Undeclared observed fields and undeclared
catalog comments are diagnostics without a fallback — nothing degrades,
because nothing about them was declared.
''',
    '''no longer has a failing-cast branch: row-level `TRY_CAST` owns data
divergence. Target-specific representation limits are handled by each exporter,
not by degrading the DuckDB materialization. A declared reserved column
(decision 5b) remains outside the public typed field set. Undeclared observed
fields and undeclared catalog comments do not degrade any declaration.
''',
)
replace(
    "rfcs/0006-declared-column-types.md",
    '''A forgeable marker comment, or a manifest that can itself drift from the
schema it describes, added a second source of truth for something the
codebase already decides correctly with an existence check plus an
explicit flag.
''',
    '''A forgeable marker comment, or a manifest that can itself drift from the
schema it describes, added a second source of truth for something the
codebase already decides correctly with an existence check plus an
explicit flag.

**The persistent typed column is a stored snapshot, not a virtual generated
column.** DuckDB 1.5 supports only virtual generated columns, which are
recomputed when read. That is ideal for `apply`'s ephemeral read-only
projection but wrong for an exported database: in particular, converting an
offset-less string to `TIMESTAMPTZ` depends on the session `TimeZone`. The
persistent materializer therefore fills ordinary typed columns with the same
per-value `TRY_CAST` under a temporary `TimeZone = 'UTC'` and restores the
caller's setting immediately afterward. The exported typed value is stable
across later sessions; the raw carrier remains beside it for audit.
''',
)

# Regression: persistent TIMESTAMPTZ values must not change when a reader's
# session timezone changes, and materialization must restore the caller's setting.
path = Path("tests/test_duckdb.py")
text = path.read_text(encoding="utf-8")
text += r'''


def test_persistent_timestamptz_is_utc_materialized_and_session_stable(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text(
        "---\ntype: Rotina\ninstante: 2026-08-07 10:00:00\n---\nA\n",
        encoding="utf-8",
    )
    types = tmp_path / "docs" / "types"
    types.mkdir(parents=True)
    (types / "rotina.schema.sql").write_text(
        'CREATE TABLE "Rotina" (instante TIMESTAMPTZ);\n',
        encoding="utf-8",
    )
    connection = duckdb.connect()
    connection.execute("SET TimeZone = 'America/New_York'")

    attach_okf(connection, tmp_path, spec_template="docs/types/{slug}.md")

    assert connection.execute("SELECT current_setting('TimeZone')").fetchone() == (
        "America/New_York",
    )
    expected = connection.execute(
        "SELECT epoch_us(TIMESTAMPTZ '2026-08-07 10:00:00+00')"
    ).fetchone()
    first = connection.execute(
        'SELECT epoch_us(instante) FROM okf_types."Rotina"'
    ).fetchone()
    connection.execute("SET TimeZone = 'Asia/Tokyo'")
    second = connection.execute(
        'SELECT epoch_us(instante) FROM okf_types."Rotina"'
    ).fetchone()
    assert expected is not None
    assert first == expected
    assert second == expected
'''
path.write_text(text, encoding="utf-8")
