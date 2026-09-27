"""Expose the commands the native binary has not taken over yet, through Cyclopts.

The binary (``rust-core/src/main.rs``) answers ``check``, ``inventory``,
``graph``, ``init``, ``duckdb``, ``sql``, ``apply`` and ``serve`` itself and passes every other
command line here (RFC 0024).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from importlib.metadata import version as _package_version
from typing import Annotated, Literal

from cyclopts import App, Parameter

from okf_parser.service import (
    import_bundle,
    schema_bundle,
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
    help="Validate, query and edit OKF bundles.",
    version=_package_version("okf-parser"),
    result_action=_render_cli_result,
)


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
