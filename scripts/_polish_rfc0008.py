from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:120]!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


# Keep DuckDB's known collision contract identical across CLI and MCP instead of
# turning a structured CLI result into a generic MCP exception.
replace_once(
    "src/okf_parser/cli.py",
    '''@app.command(name="duckdb")
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
    try:
        return CliResult(
            export_duckdb(
                path,
                database,
                schema,
                overwrite=overwrite,
                exclude=exclude or (),
                spec_template=spec_template,
            )
        )
    except BundleExportError as exc:
        return CliResult(
            {
                "error": str(exc),
                "schema": exc.schema_name,
                "existing_tables": list(exc.tables),
            },
            exit_code=1,
        )
''',
    '''def _duckdb_export_payload(
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
''',
)

replace_once(
    "src/okf_parser/cli.py",
    '''    return export_duckdb(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude or (),
        spec_template=spec_template,
    )
''',
    '''    return _duckdb_export_payload(
        path,
        database,
        schema,
        overwrite=overwrite,
        exclude=exclude,
        spec_template=spec_template,
    )
''',
)

# Exercise the schema actually advertised through tools/list, not just Python
# wrapper signatures. In particular aliases must remain agent-facing and no
# dynamic write switch may leak back into paired tools.
test = Path("tests/test_mcp.py")
text = test.read_text(encoding="utf-8")
anchor = '''def test_mcp_effect_annotations_describe_maximum_possible_effect() -> None:
'''
insert = '''def test_mcp_public_schemas_keep_aliases_and_no_dynamic_write_switch() -> None:
    tools = _tools(cli.build_mcp(allow_write=True))

    apply_preview = tools["apply_preview"].inputSchema["properties"]
    apply_write = tools["apply_write"].inputSchema["properties"]
    assert "from" in apply_preview
    assert "from_" not in apply_preview
    assert "spec_template" in apply_preview
    assert "write" not in apply_preview
    assert "from" in apply_write
    assert "write" not in apply_write

    assert "write" not in tools["init_preview"].inputSchema["properties"]
    assert "write" not in tools["import_preview"].inputSchema["properties"]
    assert "spec_template" in tools["duckdb_export"].inputSchema["properties"]


'''
if anchor not in text:
    raise SystemExit("MCP schema test anchor not found")
text = text.replace(anchor, insert + anchor, 1)

append = '''


def test_duckdb_export_preserves_cli_collision_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    def collide(*args: object, **kwargs: object) -> dict[str, object]:
        raise cli.BundleExportError("okf", ("concepts", "links"))

    monkeypatch.setattr(cli, "export_duckdb", collide)

    payload = cli.mcp_duckdb_export("bundle")

    assert payload["schema"] == "okf"
    assert payload["existing_tables"] == ["concepts", "links"]
    assert "pass overwrite=True" in str(payload["error"])
'''
text += append
test.write_text(text, encoding="utf-8")

replace_once(
    "docs/cli.md",
    '''On a name collision without `--overwrite`, exits `1` with
`{"error", "schema", "existing_tables"}` in the payload instead of raising.

MCP tool: `duckdb_export` with `--allow-write`; it also accepts `spec_template`.
''',
    '''On a name collision without `--overwrite`, exits `1` with
`{"error", "schema", "existing_tables"}` in the payload instead of raising.
The MCP `duckdb_export` tool preserves that same structured collision payload.

MCP tool: `duckdb_export` with `--allow-write`; it also accepts `spec_template`.
''',
)

# Accepted RFC text should describe the concrete metadata emitted by the
# implementation, including fields that MCP treats as irrelevant for reads.
rfc = Path("rfcs/0008-effect-aware-mcp-writes.md")
rfc_text = rfc.read_text(encoding="utf-8")
for old, new in {
    "| `check` | `true` | n/a | n/a | `false` |": "| `check` | `true` | `false` | `true` | `false` |",
    "| `inventory` | `true` | n/a | n/a | `false` |": "| `inventory` | `true` | `false` | `true` | `false` |",
    "| `graph` | `true` | n/a | n/a | `false` |": "| `graph` | `true` | `false` | `true` | `false` |",
    "| `format_check` | `true` | n/a | n/a | `false` |": "| `format_check` | `true` | `false` | `true` | `false` |",
    "| `init_preview` | `true` | n/a | n/a | `false` |": "| `init_preview` | `true` | `false` | `true` | `false` |",
    "| `import_preview` | `true` | n/a | n/a | `true` |": "| `import_preview` | `true` | `false` | `true` | `true` |",
}.items():
    if old not in rfc_text:
        raise SystemExit(f"RFC annotation row not found: {old}")
    rfc_text = rfc_text.replace(old, new, 1)

rfc_text = rfc_text.replace(
    "`destructiveHint` is omitted/irrelevant for tools annotated read-only.",
    "For read-only tools, `destructiveHint=false` and `idempotentHint=true` are emitted explicitly even though MCP only gives those hints behavioral meaning for mutating tools. Explicit values keep `tools/list` deterministic and reviewable.",
    1,
)
rfc_text = rfc_text.replace(
    "The observable contract remains mechanism-independent. It mandates the observable contract:",
    "The mechanism is an implementation detail; the observable contract remains:",
    1,
)
rfc.write_text(rfc_text, encoding="utf-8")
