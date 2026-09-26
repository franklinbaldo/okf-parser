"""Expose okf-parser through Cyclopts.

``okf-parser serve`` is answered by the native binary (``rust-core/src/mcp.rs``),
which delegates unported tools to ``okf_parser.mcp_bridge``.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import version as _package_version
from typing import Annotated, Literal, cast

from cyclopts import App, Parameter

from okf_parser.duckdb import BundleExportError
from okf_parser.service import (
    apply_bundle,
    check_bundle,
    check_format,
    export_duckdb,
    graph_bundle,
    import_bundle,
    init_bundle,
    inventory_bundle,
    schema_bundle,
    write_format,
)
from okf_parser.type_packs import install_type_pack, list_type_packs

type SchemaFormat = Literal["json", "zod", "pydantic", "graphql"]
type ZodImport = Literal["zod", "astro"]
type RefsMode = Literal["key", "embed"]
type CliSchemaFormat = Annotated[SchemaFormat, Parameter(name="format")]
type ImportConflictPolicy = Literal["skip", "verify-identical"]
type RepeatableStrings = list[str] | None
type JsonPayload = dict[str, object]


@dataclass(frozen=True, slots=True)
class CliResult[PayloadT]:
    """A JSON payload or plain text string and its intended process exit code."""

    payload: PayloadT
    exit_code: int = 0


def _render_cli_result(result: object) -> None:
    """Render stable JSON/text and preserve command-specific exit status."""
    if not isinstance(result, CliResult):
        return
    if isinstance(result.payload, str):
        text = result.payload
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
    else:
        sys.stdout.write(
            json.dumps(result.payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    if result.exit_code:
        raise SystemExit(result.exit_code)


app = App(
    name="okf-parser",
    help="Validate and inspect OKF bundles with Ibis and NetworkX.",
    version=_package_version("okf-parser"),
    result_action=_render_cli_result,
)


@app.command
def check(
    path: str,
    *,
    exclude: RepeatableStrings = None,
    require_spec: str | None = None,
    normative_spec: bool = False,
    relational_schema: str | None = None,
    classify: bool = False,
) -> CliResult[JsonPayload]:
    """Validate every Markdown file recursively as OKF v0.2."""
    payload = check_bundle(
        path,
        exclude or (),
        require_spec,
        normative_spec=normative_spec,
        classify=classify,
        relational_schema=relational_schema,
    )
    return CliResult(payload, 0 if payload["conformant"] else 1)


@app.command(name="import")
def import_command(  # each argument is an independent public CLI flag.
    source: str,
    path: str,
    *,
    type: str,  # the domain name for this flag is `type`.
    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
    expected_preview_token: str | None = None,
) -> CliResult[JsonPayload]:
    """Materialize every row of a DuckDB-readable source (CSV, Parquet, JSON) as a concept."""
    payload = import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=write,
        overwrite=overwrite,
        on_conflict=on_conflict,
        expected_preview_token=expected_preview_token,
    )
    failed = bool(payload["duplicate_ids"]) or bool(payload["conflicting_existing"])
    return CliResult(payload, 1 if failed else 0)


@app.command
def init(
    path: str,
    *,
    spec_template: str,
    exclude: RepeatableStrings = None,
    write: bool = False,
    infer_schema: bool = False,
) -> CliResult[JsonPayload]:
    """Scaffold a missing specification document, and optionally a starter `.schema.sql`."""
    payload = init_bundle(
        path, spec_template, exclude or (), write=write, infer_schema=infer_schema
    )
    specs = cast("dict[str, object]", payload["specs"])
    schemas = cast("dict[str, object]", payload["schemas"]) if infer_schema else None
    has_collisions = bool(specs["collisions"]) or bool(schemas and schemas["collisions"])
    return CliResult(payload, 1 if has_collisions else 0)


@app.command
def inventory(
    path: str, *, exclude: RepeatableStrings = None, digests: bool = False
) -> CliResult[JsonPayload]:
    """Count concepts by type and optionally expose deterministic content digests."""
    return CliResult(inventory_bundle(path, exclude or (), digests=digests))


@app.command
def graph(path: str, *, exclude: RepeatableStrings = None) -> CliResult[JsonPayload]:
    """Summarize the resolved concept graph with NetworkX."""
    return CliResult(graph_bundle(path, exclude or ()))


@app.command
def schema(  # each argument is an independent public CLI flag.
    path: str,
    *,
    schema_format: CliSchemaFormat = "json",
    infer_types: bool = False,
    cast: RepeatableStrings = None,
    exclude: RepeatableStrings = None,
    zod_import: ZodImport = "zod",
    spec_template: str | None = None,
    relational_schema: str | None = None,
    refs: RefsMode = "key",
) -> CliResult[JsonPayload | str]:
    """Export JSON Schema, Zod, Pydantic source, or GraphQL SDL."""
    return CliResult(
        schema_bundle(
            path,
            schema_format,
            exclude or (),
            infer_types=infer_types,
            casts=cast or (),
            zod_import=zod_import,
            spec_template=spec_template,
            relational_schema=relational_schema,
            refs=refs,
        )
    )


@app.command(name="format")
def format_command(
    path: str,
    *,
    write: bool = False,
    exclude: RepeatableStrings = None,
) -> CliResult[JsonPayload]:
    """Check mdformat style, writing only when --write is explicit."""
    patterns = exclude or ()
    payload = write_format(path, patterns) if write else check_format(path, patterns)
    return CliResult(payload, 0 if payload["succeeded"] else 1)


@app.command
def apply(  # each argument is an independent public CLI flag.
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
    """Mutate frontmatter fields via a bounded ALTER TABLE + UPDATE SQL script."""
    payload = apply_bundle(
        path,
        sql=sql,
        type_name=type,
        field_name=field,
        from_value=from_,
        to_value=to,
        write=write,
        exclude=exclude or (),
        spec_template=spec_template,
    )
    return CliResult(payload, 0 if payload["succeeded"] else 1)


def _duckdb_export_payload(
    path: str,
    database: str,
    schema: str,
    *,
    overwrite: bool,
    exclude: RepeatableStrings,
    spec_template: str | None,
) -> JsonPayload:
    """Return the shared DuckDB export payload, including known collision errors."""
    try:
        return export_duckdb(
            path,
            database,
            schema,
            overwrite=overwrite,
            exclude=exclude or (),
            spec_template=spec_template,
        )
    except BundleExportError as exc:
        return {
            "error": str(exc),
            "schema": exc.schema_name,
            "existing_tables": list(exc.tables),
        }


@app.command(name="duckdb")
def duckdb_command(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    *,
    overwrite: bool = False,
    exclude: RepeatableStrings = None,
    spec_template: str | None = None,
) -> CliResult[JsonPayload]:
    """Materialize an OKF bundle into a DuckDB database file."""
    payload = _duckdb_export_payload(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude,
        spec_template=spec_template,
    )
    return CliResult(payload, exit_code=1 if "error" in payload else 0)


@app.command
def packs() -> CliResult[JsonPayload]:
    """List opt-in OKF type packs registered by installed package metadata."""
    return CliResult({"packs": list_type_packs()})


@app.command(name="add-pack")
def add_pack(
    name: str,
    path: str = ".",
    *,
    write: bool = False,
) -> CliResult[JsonPayload]:
    """Preview or install an opt-in type pack into an ordinary OKF bundle."""
    payload = install_type_pack(name, path, write=write)
    return CliResult(payload, exit_code=1 if payload["collisions"] else 0)


def main() -> None:
    """Run the Cyclopts command-line application."""
    app()


if __name__ == "__main__":
    main()
