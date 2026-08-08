---
type: Project
title: okf-parser
description: Relational inspection and validation for Open Knowledge Format bundles
---

# okf-parser

Relational inspection and validation for
[Open Knowledge Format (OKF) v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md)
bundles.

`okf-parser` reads an OKF bundle without imposing a domain taxonomy, preserves
unknown frontmatter fields, and exposes concepts and links as
[Ibis](https://ibis-project.org/) tables. This makes bundle-wide rules—identity,
lineage, cardinality, provenance, and profile-specific constraints—expressible
as deterministic relational checks.

## Why another OKF tool?

The ecosystem already has good static linters and generators, including
`okflint`, `okf-cli`, and `google-okf`. This project focuses on a different
layer:

- compile a bundle into queryable relational tables;
- project those same relations into a NetworkX graph;
- validate OKF v0.2 conformance without rejecting extensions;
- distinguish normative errors from advisory diagnostics;
- let projects add cross-concept rules as Ibis expressions;
- produce stable human-readable and JSON reports for CI and agents.

The architectural boundary between strict authored OKF and source adapters is documented in
[`docs/architecture.md`](docs/architecture.md).

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
uv run okf-parser inventory path/to/bundle
uv run okf-parser graph path/to/bundle
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

`format` is an opinionated canonicalizer, not a syntax-preserving rewriter. It
keeps [mdformat](https://mdformat.readthedocs.io/) as its base and replaces only
the numbering policy for ordered lists: markers are decided while rendering, so
they are consecutive (`1. 2. 3.`) and never zero-padded to an even width. A list
starting at `101` is written `101. 102. 103.`, appending an item does not rewrite
the lines above it, and a list whose markers would exceed CommonMark's nine-digit
limit keeps plain numbering.

Formatting will not write a file whose **protected block structure** the rewrite
would change; such a file is reported in `skipped_paths` and left on disk.
Protected means the sequence, nesting and tag of every block, block attributes
such as an ordered list's `start`, and the content of code blocks, raw HTML and
frontmatter. Inline content — link targets, emphasis, text inside a paragraph or
table cell — is deliberately outside this check, because canonical formatting
rewrites inline whitespace.

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
  - uses: franklinbaldo/okf-parser@v0.25.0
    with:
      path: knowledge
```

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
uv run okf-parser serve
uv run okf-parser-mcp
uv run fastmcp run
```

Read-only tools: `check`, `inventory`, `graph`, and `format_check`.

## DuckDB

`okf-parser` is a regular uv-managed Python app; the DuckDB integration is part
of the same package, not a native C++ subproject:

```python
import duckdb

from okf_parser.duckdb import attach_okf

connection = duckdb.connect("knowledge.duckdb")
attach_okf(connection, "knowledge/")

connection.sql("""
    SELECT concept_type, count(*)
    FROM okf.concepts
    GROUP BY concept_type
""").show()
```

The call creates `okf.concepts`, `okf.links`, `okf.reserved`, and
`okf.diagnostics` as ordinary DuckDB tables. Materializing twice into the same
schema raises `BundleExportError` rather than clobbering an earlier export; pass
`overwrite=True` (or `--overwrite` on the command line) to replace the four
tables.

## Python API

```python
from pathlib import Path

from okf_parser import load_bundle, validate_path

bundle = load_bundle(Path("knowledge"))
print(bundle.concepts.execute())
print(bundle.links.execute())
print(bundle.to_networkx())
print(bundle.validate())

report = validate_path(Path("knowledge"))
assert report.markdown_count == report.concept_count + report.reserved_count
assert report.is_conformant
```

Declared RFC 0006 types can be queried directly as Ibis relations without a CLI or database-file round trip:

```python
bundle = load_bundle(Path("knowledge"))
with bundle.compile_types("docs/types/{slug}.md") as typed:
    routines = typed["Rotina"]
    print(routines.filter(routines.custo.notnull()).execute())
```

`TypedRelations` owns an ephemeral DuckDB/Ibis backend; use it as a context manager or call `close()` explicitly. With no matching declarations, `tables` is empty rather than silently inventing an inferred physical schema.

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
- Ibis tables for concepts, reserved documents, and links;
- NetworkX graph projection for traversal, cycles, components, and impact;
- aggregated validation reports.

Profiles, lifecycle/provenance family validation, external resources, and
pluggable Ibis rules are the next milestones.
