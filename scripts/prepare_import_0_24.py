from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, count: int = 1) -> None:
    text = read(path)
    if old not in text:
        raise SystemExit(f"expected block not found in {path}: {old[:100]!r}")
    write(path, text.replace(old, new, count))


# Keep one tiny runtime alias local to the public CLI/FastMCP schema.
replace(
    "src/okf_parser/cli.py",
    'type CliSchemaFormat = Annotated[SchemaFormat, Parameter(name="format")]\n',
    'type CliSchemaFormat = Annotated[SchemaFormat, Parameter(name="format")]\n'
    'type ImportConflictPolicy = Literal["skip", "verify-identical"]\n',
)
replace(
    "src/okf_parser/cli.py",
    '''    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
) -> CliResult[JsonPayload]:
    """Materialize every row of a DuckDB-readable source (CSV, Parquet, JSON) as a concept."""
    payload = import_bundle(
        source, path, type, id_column=id_column, write=write, overwrite=overwrite
    )
    return CliResult(payload, 1 if payload["duplicate_ids"] else 0)
''',
    '''    id_column: str | None = None,
    write: bool = False,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
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
    )
    failed = bool(payload["duplicate_ids"]) or bool(payload["conflicting_existing"])
    return CliResult(payload, 1 if failed else 0)
''',
)
replace(
    "src/okf_parser/cli.py",
    '''    *,
    id_column: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Plan a tabular import without creating or replacing concept files."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=False,
        overwrite=overwrite,
    )
''',
    '''    *,
    id_column: str | None = None,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
) -> dict[str, object]:
    """Plan a tabular import without creating or replacing concept files."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=False,
        overwrite=overwrite,
        on_conflict=on_conflict,
    )
''',
)
replace(
    "src/okf_parser/cli.py",
    '''    *,
    id_column: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]:
    """Commit a tabular import using the existing import service."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=True,
        overwrite=overwrite,
    )
''',
    '''    *,
    id_column: str | None = None,
    overwrite: bool = False,
    on_conflict: ImportConflictPolicy = "skip",
) -> dict[str, object]:
    """Commit a tabular import using the existing import service."""
    return import_bundle(
        source,
        path,
        type,
        id_column=id_column,
        write=True,
        overwrite=overwrite,
        on_conflict=on_conflict,
    )
''',
)

# Import behavior regressions: replay, normalization, fail-closed divergence and atomicity.
BUNDLE_TESTS = '''

def test_verify_identical_classifies_an_idempotent_reapplication(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    import_bundle(str(csv), str(bundle), "Pessoa", id_column="id", write=True)

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        write=True,
        on_conflict="verify-identical",
    )

    assert result["written"] is True
    assert result["created"] == []
    assert result["matched_existing"] == ["pessoa/r1.md", "pessoa/r2.md"]
    assert result["conflicting_existing"] == []


def test_verify_identical_uses_parsed_value_not_yaml_spelling(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\\nidade: 30\\nnome: Ana\\nid: r1\\ntype: Pessoa\\n---\\n",
        encoding="utf-8",
    )

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result["matched_existing"] == ["pessoa/r1.md"]
    assert result["conflicting_existing"] == []


def test_verify_identical_rejects_a_divergent_identity_atomically(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(
        "---\\ntype: Pessoa\\nid: r1\\nnome: Outra\\nidade: '30'\\n---\\n",
        encoding="utf-8",
    )

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        write=True,
        on_conflict="verify-identical",
    )

    assert result["written"] is False
    assert result["created"] == []
    assert result["would_create"] == ["pessoa/r2.md"]
    assert result["conflicting_existing"] == ["pessoa/r1.md"]
    assert not (bundle / "pessoa" / "r2.md").exists()


@pytest.mark.parametrize(
    "existing_text",
    [
        "---\\ntype: Pessoa\\nid: r1\\nnome: Ana\\nidade: '30'\\n---\\nBody inesperado\\n",
        "---\\ntype: Pessoa\\nid: r1\\nnome: Ana\\nidade: '30'\\nextra: null\\n---\\n",
        "não é um conceito OKF\\n",
    ],
)
def test_verify_identical_fails_closed_for_unexpected_documents(
    tmp_path: Path, existing_text: str
) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)
    bundle = tmp_path / "bundle"
    existing = bundle / "pessoa" / "r1.md"
    existing.parent.mkdir(parents=True)
    existing.write_text(existing_text, encoding="utf-8")

    result = import_bundle(
        str(csv),
        str(bundle),
        "Pessoa",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result["conflicting_existing"] == ["pessoa/r1.md"]


def test_verify_identical_cannot_be_combined_with_overwrite(tmp_path: Path) -> None:
    csv = tmp_path / "source.csv"
    _write_csv(csv)

    with pytest.raises(BundleImportError, match="mutually exclusive"):
        import_bundle(
            str(csv),
            str(tmp_path / "bundle"),
            "Pessoa",
            id_column="id",
            overwrite=True,
            on_conflict="verify-identical",
        )
'''
replace(
    "tests/test_bundle_import.py",
    "\n\ndef test_overwrite_replaces_an_existing_destination(tmp_path: Path) -> None:\n",
    BUNDLE_TESTS + "\n\ndef test_overwrite_replaces_an_existing_destination(tmp_path: Path) -> None:\n",
)

replace(
    "tests/test_cli.py",
    "from okf_parser.cli import app, format_command\n",
    "from okf_parser.cli import app, format_command, import_command\n",
)
CLI_TEST = '''

def test_import_exits_nonzero_for_a_divergent_existing_identity(tmp_path: Path) -> None:
    source = tmp_path / "source.csv"
    source.write_text("id,name\\nr1,Expected\\n", encoding="utf-8")
    bundle = tmp_path / "bundle"
    destination = bundle / "example" / "r1.md"
    destination.parent.mkdir(parents=True)
    destination.write_text(
        "---\\ntype: Example\\nid: r1\\nname: Different\\n---\\n", encoding="utf-8"
    )

    result = import_command(
        str(source),
        str(bundle),
        type="Example",
        id_column="id",
        on_conflict="verify-identical",
    )

    assert result.exit_code == 1
    assert result.payload["conflicting_existing"] == ["example/r1.md"]
'''
replace(
    "tests/test_cli.py",
    '\n\n@pytest.mark.parametrize("schema_format", ["zod", "pydantic"])\n',
    CLI_TEST + '\n\n@pytest.mark.parametrize("schema_format", ["zod", "pydantic"])\n',
)

replace(
    "tests/test_mcp.py",
    '''    assert import_preview == import_write
    assert "write" not in import_preview["properties"]

    assert "classify" in tools["check"].inputSchema["properties"]
''',
    '''    assert import_preview == import_write
    assert "write" not in import_preview["properties"]
    assert import_preview["properties"]["on_conflict"]["enum"] == [
        "skip",
        "verify-identical",
    ]

    assert "classify" in tools["check"].inputSchema["properties"]
''',
)
replace(
    "tests/test_mcp.py",
    '''    preview = cli.mcp_import_preview("source.csv", "bundle", "Pessoa")
    written = cli.mcp_import_write("source.csv", "bundle", "Pessoa")
''',
    '''    preview = cli.mcp_import_preview(
        "source.csv", "bundle", "Pessoa", on_conflict="verify-identical"
    )
    written = cli.mcp_import_write(
        "source.csv", "bundle", "Pessoa", on_conflict="verify-identical"
    )
''',
)

# Public docs: policy is one flag on the existing import surface.
replace(
    "docs/cli.md",
    "uv run okf-parser import SOURCE path/to/bundle --type TYPE [--id-column COLUMN] [--write] [--overwrite]",
    "uv run okf-parser import SOURCE path/to/bundle --type TYPE [--id-column COLUMN] [--write] [--overwrite] [--on-conflict skip|verify-identical]",
)
replace(
    "docs/cli.md",
    '''- `--overwrite` — permit replacement of an existing destination; without it,
  collisions are reported rather than silently replaced.

Exits `1` when the import plan contains duplicate ids. Other invalid inputs are
reported as command errors.

MCP tools: `import_preview`; `import_write` with `--allow-write`.
''',
    '''- `--overwrite` — permit replacement of an existing destination; without it,
  collisions use the selected conflict policy rather than being replaced.
- `--on-conflict skip|verify-identical` — default `skip` preserves the existing
  behavior. `verify-identical` parses the destination and candidate through the
  official parser and compares `parsed_digest`; matching parsed values are
  idempotent `matched_existing` rows, while any divergence is
  `conflicting_existing` and aborts the batch before writes. It cannot be
  combined with `--overwrite`.

Exits `1` when the import plan contains duplicate ids or conflicting existing
identities. Other invalid inputs are reported as command errors.

MCP tools: `import_preview`; `import_write` with `--allow-write`. Both expose the
same optional `on_conflict` policy through FastMCP.
''',
)

# Release metadata. All package versions stay synchronized by repository policy.
replace("README.md", "franklinbaldo/okf-parser@v0.23.0", "franklinbaldo/okf-parser@v0.24.0")
replace("pyproject.toml", 'version = "0.23.0"', 'version = "0.24.0"')
replace("typescript/package.json", '"version": "0.23.0"', '"version": "0.24.0"')
replace("typescript-duckdb/package.json", '"version": "0.23.0"', '"version": "0.24.0"')
replace("typescript-duckdb/package.json", '"okf-parser": "^0.23.0"', '"okf-parser": "^0.24.0"')
replace(
    "typescript/src/version.ts",
    'export const PROTOCOL_VERSION = "0.23.0";',
    'export const PROTOCOL_VERSION = "0.24.0";',
)
write(
    "changelog/0.24.0.md",
    '''---
type: Release
title: okf-parser 0.24.0
description: make tabular imports replay-safe with explicit verify-identical conflicts
---

# okf-parser 0.24.0

- `import --on-conflict verify-identical` distinguishes idempotent replay from reuse of the same import id with different content (#63).
- Equality reuses the 0.23.0 `parsed_digest` primitive: both existing and candidate documents pass through the official parser, so YAML key order and quoting do not create false conflicts.
- Extra/null frontmatter, unexpected body, malformed documents or any other parsed-value divergence fail closed as `conflicting_existing`.
- Any divergent existing destination aborts the entire batch before writes; matching existing rows are reported separately as `matched_existing`.
- `--overwrite` and `verify-identical` are mutually exclusive, while the default `skip` policy preserves prior behavior.
- Preview/write, CLI and the existing FastMCP import tools share the same conflict-policy contract; no new MCP tool is added.
''',
)
