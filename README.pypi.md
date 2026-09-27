# okf-parser

Relational inspection and validation for
[Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundles.

`okf-parser` reads an OKF bundle without imposing a domain taxonomy, preserves
unknown frontmatter fields, and exposes concepts and links as typed records and
DuckDB tables. This makes bundle-wide rules—identity, lineage, cardinality,
provenance, and profile-specific constraints—expressible as deterministic
relational checks.

It ships the way `ruff` and `uv` do: a native Rust binary does the work, and the
Python package is a thin shell over it
([RFC 0024](https://github.com/franklinbaldo/okf-parser/blob/main/rfcs/0024-rust-native-core.md)).

## Why another OKF tool?

The ecosystem already has good static linters and generators, including
`okflint`, `okf-cli`, and `google-okf`. This project focuses on a different
layer:

- compile a bundle into queryable relational tables;
- project those same relations into a graph, with an optional NetworkX bridge;
- validate OKF v0.2 conformance without rejecting extensions, against a
  [corpus pinned to the upstream specification](https://github.com/franklinbaldo/okf-parser/blob/main/conformance/README.md);
- distinguish normative errors from advisory diagnostics;
- let projects add cross-concept rules as SQL over the same relations;
- produce stable human-readable and JSON reports for CI and agents.

The architectural boundary between strict authored OKF and source adapters is documented in
[`docs/architecture.md`](https://github.com/franklinbaldo/okf-parser/blob/main/docs/architecture.md).

The parser and validation model are inspired by
[`franklinbaldo/sisprev`](https://github.com/franklinbaldo/sisprev): parse
documents independently from semantic validation, aggregate violations instead
of failing at the first bad concept, preserve authored bodies, and test
filesystem identity explicitly. No Sisprev-specific legal types are copied into
the core.

[`mrorigo/rust-okf`](https://github.com/mrorigo/rust-okf) inspired the stable
logical key, conservative metadata preservation, BOM/CRLF handling, and clean
separation between bundle parsing and downstream query surfaces. Its BM25,
vector index, storage format, and HTTP server are intentionally outside this
project's scope.

## Quick start

```bash
uv sync
uv run okf-parser check path/to/bundle
uv run okf-parser check path/to/bundle --relational-schema okf.schema.sql
uv run okf-parser inventory path/to/bundle
uv run okf-parser graph path/to/bundle
uv run okf-parser search path/to/bundle "retry budget" --context 1
uv run okf-parser sql path/to/bundle "SELECT concept_type, count(*) FROM concepts GROUP BY 1"
uv run okf-parser format path/to/bundle
uv run okf-parser format path/to/bundle --write
uv run okf-parser duckdb path/to/bundle knowledge.duckdb
uv run okf-parser duckdb path/to/bundle knowledge.duckdb --overwrite
uv run okf-parser serve
uv run okf-parser serve --allow-write
```

The command exits with status `1` only when normative errors exist. Broken
cross-links are warnings because OKF v0.2 explicitly says they do not make a
bundle non-conformant.

The MCP server is commit-disabled by default. `serve --allow-write` exposes explicit
commit tools for formatting, relational apply, spec scaffolding, import, and DuckDB
export; preview tools remain available without the flag. Effect annotations describe
maximum tool effects but are not authorization or sandboxing.

`format` normalizes syntax and nothing else. It parses each document and
rewrites only the tokens that spell its structure: list markers (`-`, and
consecutive `1. 2. 3.` numbering from the list's start, never zero-padded), `*`
and `**` emphasis, ATX headings, compact tables (`| a | b |`, `| --- | :-: |`),
backslash hard breaks, single blank lines, no trailing whitespace, one final
newline, and a blank line after frontmatter whose simple keys are ordered
`type`, `title`, `description`, then by name. Every other byte — text, code,
HTML, links, escapes — is kept as written, so `x86_64`, `#25` and `[[wiki]]`
stay exactly as they are.

The rewrite must parse to the same document as the original. When it would
not, the file is reported in `skipped` with the reason and left on disk, and
`--write` replaces every changed file or, on a write error, none.

## Excluding paths

A repository that keeps OKF knowledge next to code, a README and vendored
dependencies has no root that validates cleanly. Checking the repository root
reports `OKF001` for every unrelated Markdown file; checking each bundle
separately makes every link *between* bundles unresolvable, because the target
sits outside the checked root. Excluding subpaths is what lets one root cover
the whole tree, which is the only arrangement under which cross-bundle link
validation runs at all.

Put the patterns in an `.okfignore` beside the bundle, so the exclusions are
versioned with the content and CI needs no extra flags:

```gitignore
# vendored dependencies
vendor
# the knowledge inside them is not noise
!vendor/knowledge
# project Markdown at the root, not knowledge
/*.md
```

Or pass them for a single run — the option repeats, and adds to the file rather
than replacing it:

```bash
uv run okf-parser check . --exclude vendor --exclude '/*.md'
```

Every command that reads a bundle accepts `--exclude`: `check`, `inventory`,
`graph`, `format` and `duckdb`. Excluded files are never read and never
written, so `format --write` on a repository root leaves vendored documents
alone.

### Pattern semantics

`.okfignore` uses **`.gitignore` pattern semantics**, matched against
POSIX-style paths relative to the bundle root:

| pattern        | matches                                      | does not match          |
| -------------- | -------------------------------------------- | ----------------------- |
| `vendor`       | `vendor/a.md`, `libs/vendor/a.md`            | `equipe/a.md`           |
| `/vendor`      | `vendor/a.md`                                | `libs/vendor/a.md`      |
| `vendor/`      | `vendor/a.md` (directory only)               | a *file* named `vendor` |
| `*.md`         | `README.md`, `items/tarefa.md`               | `items/tarefa.markdown` |
| `/*.md`        | `README.md`                                  | `items/tarefa.md`       |
| `docs/**/x.md` | `docs/x.md`, `docs/a/b/x.md`                 | `other/x.md`            |
| `!vendor/kb`   | re-includes what an earlier pattern excluded |                         |

- a pattern without a separator matches its name **at any depth**; a separator
  anchors it at the bundle root;
- `*` and `?` stay inside one segment, `[abc]` and `[!abc]` classes work, and
  `**` spans segments;
- a trailing `/` matches directories only;
- `!` re-includes, and **the last pattern that matches a path decides**;
- `#` starts a comment, blank lines declare nothing, unescaped trailing spaces
  are dropped, and `\#` or `\!` escape a literal first character.

One deviation from `.gitignore` is deliberate. Git cannot re-include a path
whose parent directory is excluded, because it prunes the walk and never looks
back, so `vendor` plus `!vendor/knowledge` does nothing there. Here it works:
discovery descends whenever a negation exists. A negation that silently does
nothing is exactly the surprise this feature exists to prevent. With no
negation in the rules the walk still prunes, so excluding a vendored dependency
of several hundred documents never walks it.

The same rules are shared with the TypeScript package through
`conformance/exclusion.json`.

### Migrating from the pre-0.14 semantics

Before 0.14.0 every pattern was anchored at the bundle root and `!` was a
literal character. Two rewrites cover it:

| before   | after     | why                                               |
| -------- | --------- | ------------------------------------------------- |
| `*.md`   | `/*.md`   | unanchored patterns now match at any depth        |
| `vendor` | `/vendor` | keep it root-only; leave as-is to match any depth |

A pattern that begins with a literal `!` now needs `\!`.

## Requiring a specification per type

OKF v0.2 only requires `type` to be non-empty, so a producer can invent a type,
emit concepts of it and keep a green `check` while that type's frontmatter
schema changes underneath its consumers.

The optional rule below closes that gap without inventing taxonomy: it derives a
document path from each type in use and reports the types whose document is
absent.

```bash
uv run okf-parser check ./bundle --require-spec ".okf/specs/{slug}.md"
uv run okf-parser check ./bundle --require-spec ".okf/specs/{slug}.md" --normative-spec
```

The template must contain `{slug}`. The slug is lowercase, with accents and
cedillas removed, whitespace and `/` turned into hyphens, and every remaining
non-alphanumeric character dropped:

| `type`            | derived path                    |
| ----------------- | ------------------------------- |
| `Spec`            | `.okf/specs/spec.md`            |
| `Revisão Ciência` | `.okf/specs/revisao-ciencia.md` |
| `Peça Forense`    | `.okf/specs/peca-forense.md`    |

The path is **derived**, not declared. A `spec:` frontmatter field would be a
second fact free to disagree with the first, and putting the path in `type`
itself would tie identity to layout, so renaming a directory would invalidate
every concept of that type.

Missing documents are reported as `OKF010` warnings, because a bundle mid-
adoption legitimately has legacy types without a document and that is not an
OKF v0.2 defect. `--normative-spec` promotes them to errors for a bundle that
has completed the adoption. The rule is off unless `--require-spec` is given.

## GitHub Actions

Add the repository as a CI check:

```yaml
steps:
  - uses: actions/checkout@v7
  - uses: franklinbaldo/okf-parser@v0.48.0
    with:
      path: knowledge
```

### Rust end-to-end engine

Python always loads bundles through the native binary installed with the package.
The TypeScript package keeps a portable implementation. To test a locally built
engine, build it and pass its path explicitly:

```bash
cargo build --release --manifest-path rust-core/Cargo.toml
```

Python accepts `load_bundle(root, rust_core=Path(".../okf-parser"))`; TypeScript accepts
`loadBundle(root, { rustCore: ".../okf-core" })`. The native process owns discovery,
bounded parallel reads, YAML/frontmatter, Markdown facts, validation, link resolution,
and content digests. The packaged `okf-parser` executable is the single Python command
and declares the whole command line: `check`, `inventory`, `graph`, `search`, `sql`,
`apply`, `format`, `duckdb`, `init`, `serve` and the private engine operations run natively, and only
the commands still written in Python (`import`'s document building, `schema` and the type
packs) are handed to the Python CLI. Omitting the option keeps the portable language-native fallback.

The composite action installs a pinned uv version and executes the same
`validate_path()` function used by the Python API, CLI, and MCP server.

Pin an exact version. There is no moving `@v1` ref: this repository's tags are
package versions, because `publish.yml` refuses to publish a release whose tag
does not equal the version in `pyproject.toml`. A `v1` tag would either break
that check or drift away from the version it claims to be. A major-version ref
becomes worth introducing when the package itself reaches `1.0.0`.

Releases are published to PyPI from GitHub Releases through OIDC Trusted
Publishing. No long-lived PyPI token is stored in the repository.

Every pull request must increase the SemVer version in `pyproject.toml` and add
exactly one matching `changelog/<version>.md` entry. CI compares both against
the target branch before allowing merge.

## MCP

```bash
uv run okf-parser serve                      # stdio
uv run okf-parser serve --transport http     # Streamable HTTP on 127.0.0.1:8000/mcp
uv run okf-parser serve --transport http --host 0.0.0.0 --allowed-host mcp.example.com
uv run okf-parser serve --allow-write        # also register the commit tools
```

The server is part of the native `okf-parser` binary, built on
[`rmcp`](https://crates.io/crates/rmcp); no Python MCP framework is installed.
Every tool but `schema` and `import_*` is answered natively; those two run the
same Python service functions as the CLI (see
[RFC 0024](https://github.com/franklinbaldo/okf-parser/blob/main/rfcs/0024-rust-native-core.md)).

Read-only tools: `check`, `inventory`, `graph`, `search`, `sql`, `format_check`,
`apply_preview`, `init_preview` and `import_preview`.

## Graph

```python
graph = bundle.graph()
graph.summary()       # nodes, edges, weak/strong components, directed_acyclic
graph.nodes, graph.edges
graph.to_networkx()   # needs `pip install 'okf-parser[networkx]'`
```

NetworkX is optional. `Bundle.to_networkx()` still works but is deprecated in
favor of `bundle.graph().to_networkx()`.

## DuckDB

The binary carries its own DuckDB, so exporting a bundle needs nothing else
installed:

```bash
uv run okf-parser duckdb knowledge/ knowledge.duckdb
```

or, from Python:

```python
from okf_parser.bundle import load_bundle
from okf_parser.service import export_duckdb

export_duckdb("knowledge/", "knowledge.duckdb")  # opens in any DuckDB client

bundle = load_bundle("knowledge/")  # or query the snapshot directly
bundle.sql("SELECT concept_type, count(*) FROM concepts GROUP BY 1").to_dicts()
```

The export creates `okf.concepts`, `okf.links`, `okf.reserved`, and
`okf.diagnostics` as ordinary DuckDB tables, in one transaction. Exporting
twice into the same schema raises `BundleExportError` rather than clobbering an
earlier export; pass `overwrite=True` (or `--overwrite` on the command line) to
replace the tables.

## Python API

```python
from pathlib import Path

from okf_parser import load_bundle, validate_path

bundle = load_bundle(Path("knowledge"))
for concept in bundle.concepts:          # tuple[ConceptRecord, ...]
    print(concept.concept_id, concept.concept_type, concept.title)
print(len(bundle.links))                 # tuple[LinkRecord, ...]
print(bundle.graph().summary())
print(bundle.validate())

report = validate_path(Path("knowledge"))
assert report.markdown_count == report.concept_count + report.reserved_count
assert report.is_conformant
```

A loaded bundle answers SQL directly, no database file involved. The tables are
the ones `okf-parser duckdb` exports, and declared RFC 0006 types become typed
tables with `spec_template`:

```python
bundle = load_bundle(Path("knowledge"))
print(bundle.sql("SELECT concept_type, count(*) FROM concepts GROUP BY 1").to_dicts())

routines = bundle.sql(
    "SELECT __okf_path, custo FROM Rotina WHERE custo IS NOT NULL",
    spec_template="docs/types/{slug}.md",
)
for path, cost in routines:      # cost is a Decimal
    print(path, cost)
```

The query runs in the binary over this snapshot, is one read-only statement, and
cannot read files or reach the network. Values come back typed by their DuckDB
column (`Decimal`, `date`, `datetime`, lists, dicts). The same query runs from the
command line as `okf-parser sql knowledge "SELECT ..."` and over MCP as `sql`.

`load_bundle`, `validate_path` and `format_path` read the bundle's `.okfignore`
on their own, and take an `exclude` sequence for patterns supplied per call:

```python
report = validate_path(Path("."), exclude=["vendor", "*.md"])
```

`validate_path` also takes the optional type-specification rule:

```python
report = validate_path(Path("knowledge"), require_spec=".okf/specs/{slug}.md")
```

## Current scope

- UTF-8 Markdown discovery, matching `.md` case-insensitively, with
  `.gitignore`-compatible exclusions from `.okfignore` or `--exclude`;
- reserved `index.md` and `log.md` handling;
- strict YAML-frontmatter parsing for concept documents, validated with Pydantic
  at the parse boundary so one malformed document cannot abort a run;
- required non-empty `type`, optionally requiring a specification document per
  type in use;
- stable concept IDs derived from paths;
- Markdown-link extraction and resolution;
- typed records for concepts, reserved documents, and links, and DuckDB tables
  through `okf-parser duckdb`;
- a native graph summary, with an optional NetworkX projection for traversal,
  cycles, components, and impact;
- aggregated validation reports.

Profiles, lifecycle/provenance family validation, external resources, and
SQL rules run by the binary are the next milestones.
