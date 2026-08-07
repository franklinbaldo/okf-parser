from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:100]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Global renames in the staging script intentionally changed call sites, but
# also matched the suffix of the newly introduced function names themselves.
path = Path("src/okf_parser/typed_tables.py")
text = path.read_text(encoding="utf-8")
text = text.replace("duckdbduckdb_identifier_key", "duckdb_identifier_key")
text = text.replace("declareddeclared_raw_value", "declared_raw_value")
path.write_text(text, encoding="utf-8")

# `cast()` is runtime code; Mapping/Any are needed only by postponed/string
# annotations and therefore stay inside TYPE_CHECKING.
replace(
    "src/okf_parser/apply.py",
    "from typing import TYPE_CHECKING\n",
    "from typing import TYPE_CHECKING, cast\n",
)
replace(
    "src/okf_parser/apply.py",
    '''    from collections.abc import Mapping, Sequence

    import ibis.backends.duckdb as ibis_duckdb

    from okf_parser.models import YamlValue
''',
    '''    from collections.abc import Mapping, Sequence
    from typing import Any

    import ibis.backends.duckdb as ibis_duckdb
''',
)
path = Path("src/okf_parser/apply.py")
text = path.read_text(encoding="utf-8")
text = text.replace('cast("Mapping[str, YamlValue]",', 'cast("Mapping[str, Any]",')
path.write_text(text, encoding="utf-8")

# A pandas-backed `execute()` snapshot re-infers physical types from values:
# DECIMAL(18,2) can shrink to DECIMAL(4,2), all-NULL generated fields become
# null, and empty list data can become array<null>. Arrow preserves DuckDB's
# catalog schema, which is required for relation set-difference checks.
replace(
    "src/okf_parser/apply.py",
    "            table=ibis.memtable(con.table(type_name).execute()),\n",
    "            table=ibis.memtable(con.table(type_name).to_pyarrow()),\n",
)

# Keep the pre-existing user-facing collision diagnostic specific; other
# planner failures retain their typed-materialization message.
replace(
    "src/okf_parser/apply.py",
    '''        except (duckdb.CatalogException, TypedTableError) as exc:
            msg = (
                f'type "{type_name}" could not be materialized under DuckDB identifier '
                f"semantics: {exc}"
            )
            raise ApplyError(msg) from exc
''',
    '''        except duckdb.CatalogException as exc:
            msg = (
                f'type "{type_name}" collides with another type under DuckDB '
                f"identifier equality: {exc}"
            )
            raise ApplyError(msg) from exc
        except TypedTableError as exc:
            msg = (
                f'type "{type_name}" could not be materialized under DuckDB identifier '
                f"semantics: {exc}"
            )
            raise ApplyError(msg) from exc
''',
)

# The generic CLI signature replacement matched `format_command` first.
# Restore it and attach the option to the actual apply function.
replace(
    "src/okf_parser/cli.py",
    '''def format_command(
    path: str,
    *,
    write: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
''',
    '''def format_command(
    path: str,
    *,
    write: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
''',
)
replace(
    "src/okf_parser/cli.py",
    '''def apply(  # each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type: str | None = None,  # the domain name for this flag is `type`.
    field: str | None = None,
    from_: Annotated[str | None, Parameter(name="from")] = None,
    to: str | None = None,
    write: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
''',
    '''def apply(  # each argument is an independent public CLI flag.
    path: str,
    *,
    sql: str | None = None,
    type: str | None = None,  # the domain name for this flag is `type`.
    field: str | None = None,
    from_: Annotated[str | None, Parameter(name="from")] = None,
    to: str | None = None,
    write: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
''',
)

replace(
    "tests/test_apply.py",
    '''    assert "  - 1" in text and "  - 2" in text
''',
    '''    assert "  - 1" in text
    assert "  - 2" in text
''',
)
