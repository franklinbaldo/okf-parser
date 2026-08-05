---
type: RFC
title: First-class TypeScript sibling implementation
status: accepted
description: Define semantic parity, repository layout and incremental delivery for a native TypeScript okf-parser package
---

# RFC 0002: First-class TypeScript sibling implementation

## Summary

Add a native TypeScript implementation of `okf-parser` in this repository as a
first-class sibling of the Python package.

The TypeScript package will not be a line-by-line port. Both implementations
will conform to the same observable contract through a language-neutral corpus
of inputs and expected outputs, while each runtime uses its native libraries,
type system and packaging conventions.

The proposed package is ESM-first, targets supported Node.js releases, exposes a
typed library API and CLI, and eventually covers the same public capabilities as
the Python implementation:

- frontmatter parsing and Markdown discovery;
- bundle validation and stable diagnostics;
- inventory and graph inspection;
- JSON Schema and Zod export;
- formatting with structural safety;
- DuckDB materialization;
- read-only MCP tools.

Delivery is incremental. The first implementation PR establishes the shared
conformance corpus, parser, bundle model, validation, inventory, graph, schema
export, package build and CLI. Features whose parity depends on ecosystem-
specific behavior, especially formatting and DuckDB, follow in isolated PRs.

## Motivation

`okf-parser` now serves two distinct audiences:

1. Python applications and agents that benefit from Pydantic, Ibis, NetworkX,
   DuckDB and FastMCP;
2. web, Astro and Node.js projects that currently consume generated Zod or JSON
   Schema but cannot use the parser and validator directly in their own runtime.

Making TypeScript a first-class runtime has concrete advantages:

- Astro and other content systems can validate OKF bundles without spawning
  Python;
- Node.js build pipelines can consume the bundle graph through a typed API;
- the Zod exporter can be tested in the runtime that executes the generated
  source;
- independent implementations expose accidental assumptions that a single
  codebase may hide;
- a shared compatibility corpus turns currently implicit behavior into an
  explicit, reviewable protocol.

The value is not merely distribution. A second implementation is an opportunity
to separate the OKF-facing contract from Python-specific mechanisms such as
Pydantic model generation, pandas conversion and Ibis relations.

## Decision drivers

The design prioritizes, in order:

1. semantic consistency across runtimes;
2. deterministic and inspectable output;
3. native developer experience in each ecosystem;
4. failure transparency over best-effort silent coercion;
5. incremental delivery with independently reviewable PRs;
6. low coupling between package internals;
7. reproducible releases and CI.

Source-code symmetry is not a goal. Copying Python control flow into TypeScript
would preserve incidental complexity while still failing to guarantee equal
behavior.

## Terminology

- **observable contract**: outputs, diagnostics, exit status and generated
  artifacts visible to callers for a given input and options;
- **semantic parity**: equivalence of the observable contract, allowing normal
  serialization differences that the contract explicitly declares irrelevant;
- **conformance case**: one fixture with input files, invocation options and
  normalized expected results;
- **contract graph**: the language-neutral intermediate representation used by
  schema exporters;
- **runtime adapter**: an integration whose implementation is inherently tied
  to one ecosystem, such as Ibis or a Node DuckDB client.

## Goals

- publish a native TypeScript package from the same repository;
- expose stable library functions and a CLI with familiar command names;
- preserve frontmatter mapping/list structure, authored scalar spelling and
  explicit `null` exactly as the Python parser does;
- preserve YAML merge-key semantics;
- keep requiredness and nullability independent;
- implement deterministic schema inference and strict explicit casts;
- generate JSON Schema draft 2020-12 and executable Zod source;
- share diagnostics, fixtures and compatibility expectations across runtimes;
- detect drift in CI before release;
- allow the two packages to evolve internally without importing one another.

## Non-goals

- embedding Python in Node.js or Node.js in Python;
- generating one implementation from the source code of the other;
- requiring byte-identical error prose when stable codes and structured fields
  carry the contract;
- making the normative OKF specification depend on either implementation;
- shipping every Python integration in the first TypeScript PR;
- using an LLM to infer schema or business meaning;
- treating the Python implementation as a permanent, unquestionable oracle.

## Repository layout

The repository becomes a small multi-runtime project without moving the existing
Python package:

```text
okf-parser/
├── src/okf_parser/                 # existing Python package
├── tests/                          # existing Python tests
├── typescript/
│   ├── package.json
│   ├── tsconfig.json
│   ├── src/
│   │   ├── index.ts
│   │   ├── errors.ts
│   │   ├── frontmatter.ts
│   │   ├── discovery.ts
│   │   ├── exclusion.ts
│   │   ├── markdown.ts
│   │   ├── bundle.ts
│   │   ├── validation.ts
│   │   ├── inventory.ts
│   │   ├── graph.ts
│   │   ├── cli.ts
│   │   └── schema/
│   │       ├── contract.ts
│   │       ├── inference.ts
│   │       ├── json-schema.ts
│   │       └── zod.ts
│   └── test/
├── conformance/
│   ├── README.md
│   ├── cases/
│   └── expected/
├── rfcs/
└── changelog/
```

The root remains the release and governance boundary. The TypeScript directory
is independently buildable, but it is not a separate repository or a detached
rewrite.

## Package identity and runtime support

The proposed npm package name is `okf-parser`. It is intentionally unscoped, so the implementation does not depend on a personal or organization npm scope. The first publication must verify and reserve the registry name before release artifacts are announced.

It is:

- ESM-only;
- side-effect free except for the CLI entry point;
- built from TypeScript to ordinary JavaScript plus declaration files;
- explicit about supported Node.js versions through `engines`;
- published with the same SemVer as the Python distribution.

Keeping one version across PyPI and npm makes a conformance claim understandable:
version `X.Y.Z` names one observable protocol implemented in two runtimes. A
release workflow must reject mismatched versions.

The package uses explicit `exports` rather than exposing its internal directory
layout. Initial exports are:

```json
{
  ".": "./dist/index.js",
  "./schema": "./dist/schema/index.js",
  "./package.json": "./package.json"
}
```

The executable is published as `okf-parser-ts`. The unqualified
`okf-parser` binary remains owned by the Python distribution to avoid surprising
machines that install both ecosystems globally.

## Compatibility boundary

### Shared conformance corpus

The source of truth for cross-runtime behavior is a language-neutral fixture
corpus under `conformance/`, not direct calls from one implementation to the
other.

Each case contains:

- a miniature filesystem tree;
- invocation metadata in JSON;
- normalized expected output in JSON or text;
- optional expected diagnostics and exit code;
- a short explanation of the contract being protected.

Example:

```text
conformance/cases/schema-nullability/
├── case.json
├── one.md
└── two.md

conformance/expected/schema-nullability.json
```

Both test suites consume the same cases. The Python and TypeScript serializers
normalize only fields declared non-semantic, such as absolute temporary paths.
Everything else is compared structurally.

### Differential tests

During initial implementation, CI may also run both CLIs over the corpus and
compare normalized output. This is a migration aid, not the permanent contract.
Once a case is accepted, its checked-in expectation controls both runtimes.

This distinction matters: otherwise a bug in Python would automatically become
the required behavior in TypeScript.

### Stable diagnostics

Diagnostics are structured records:

```ts
interface Diagnostic {
  code: string;
  severity: "error" | "warning";
  path: string | null;
  message: string;
  details?: Readonly<Record<string, JsonValue>>;
}
```

Parity requires equal `code`, `severity`, logical `path` and contractually named
`details`. Human-readable messages should remain close, but punctuation and
stack formatting are not protocol fields.

## Frontmatter parsing

The TypeScript implementation uses the `yaml` package with a deliberately
restricted schema:

- failsafe structure for mappings, sequences and strings;
- the YAML null tag enabled so `null`, `Null`, `NULL` and `~` remain structural
  nulls;
- merge keys enabled;
- implicit booleans, integers, floats and timestamps left as authored strings;
- string mapping keys required;
- duplicate keys, malformed documents, unsupported structured tags and cyclic
  aliases rejected.

This is the native equivalent of the Python `_StringScalarLoader`. It avoids
post-parse stringification, which cannot recover spellings such as `0012`.

The parser returns immutable records with:

- logical path;
- frontmatter;
- deterministic `frontmatterJson`;
- Markdown body;
- derived `type`, `title` and `description` accessors.

The TypeScript type representing frontmatter is recursive and JSON-compatible:

```ts
type FrontmatterValue =
  | string
  | null
  | readonly FrontmatterValue[]
  | Readonly<Record<string, FrontmatterValue>>;
```

No `Date`, `bigint`, `Uint8Array`, `Map`, `Set` or custom class may cross the
parse boundary.

## Discovery and exclusions

Discovery must match the Python contract:

- recursive UTF-8 Markdown discovery;
- `.md` suffix matched case-insensitively;
- `index.md` and `log.md` reserved by basename;
- `.okfignore` read from the bundle root;
- repeatable runtime exclusion patterns added to the file rules;
- anchored POSIX paths relative to the bundle root;
- `*` and `?` confined to one segment;
- `**` spanning segments;
- directory matches pruning descendants;
- excluded files never read or written.

The glob compiler is implemented in the package rather than delegated to a
library whose gitignore semantics would silently broaden the existing contract.

Filesystem traversal order and all emitted lists are sorted deterministically by
logical POSIX path.

## Markdown and links

Markdown parsing uses a CommonMark-compliant token parser. Link extraction must
ignore fenced code, inline code and raw text that merely resembles Markdown.
Resolved local links retain the same logical-key rules as Python.

The implementation should expose Markdown parsing behind a narrow adapter so
formatter work can later replace or extend it without changing the bundle API.

## Bundle model

The TypeScript core uses plain immutable records and indexed maps rather than
imitating Ibis relations:

```ts
interface Bundle {
  readonly root: string;
  readonly concepts: readonly ConceptRecord[];
  readonly reserved: readonly ReservedRecord[];
  readonly links: readonly LinkRecord[];
  readonly diagnostics: readonly Diagnostic[];
}
```

Indexes by logical path, concept ID and concept type are constructed internally
for validation and graph operations. Public callers receive read-only arrays and
can choose their own dataframe or database library.

This is more idiomatic than introducing a dataframe dependency merely to resemble
the Python internals.

## Validation

Validation preserves existing OKF diagnostic codes and severity. New shared
rules must be added to the conformance corpus before either runtime implementation
lands.

A malformed document does not abort the bundle. Its parse failure becomes a
structured diagnostic and discovery continues, matching the aggregate-error
model already used by Python.

The validation API returns a report instead of throwing for content errors.
Configuration errors and programmer misuse still throw typed exceptions.

## Graph inspection

The first TypeScript graph implementation does not require a general graph
library. The currently exposed summaries can be computed deterministically from
adjacency maps:

- node and edge counts;
- resolved and unresolved links;
- in-degree and out-degree summaries;
- weakly connected components;
- cycles where part of the public contract.

A third-party graph package may be introduced only when an exposed operation
justifies it. Avoiding a dependency here keeps browser-compatible core modules
possible later, even though filesystem APIs remain Node-only.

## Contract graph for schema export

Schema inference compiles observations into an explicit intermediate
representation before producing JSON Schema or Zod:

```ts
type ContractNode =
  | ScalarContract
  | ListContract
  | ObjectContract
  | AnyContract;

interface FieldContract {
  readonly name: string;
  readonly required: boolean;
  readonly nullable: boolean;
  readonly value: ContractNode;
}
```

Requiredness and nullability are separate fields by construction. List-item
nullability is represented on the item contract, not inferred later from the
exported JSON Schema.

This IR is the architectural center of the TypeScript schema implementation and
aligns with RFC 0001's broader proposal for code generation. Exporters never
re-infer from one another's output.

## Type inference

The public policy remains:

```text
explicit cast > optional inference > string
```

Inference considers every non-null observation at a field path. One incompatible
observation widens the field to `string`; it does not create an opportunistic
union.

The TypeScript implementation improves determinism by classifying authored
lexemes directly instead of converting them into JavaScript numbers or dates:

- boolean: exact case-insensitive `true` or `false`;
- integer: complete signed decimal integer grammar;
- number: complete finite decimal/exponent grammar;
- date: syntactically and calendrically valid `YYYY-MM-DD`;
- datetime: accepted ISO 8601 forms defined by conformance fixtures;
- otherwise: string.

Lexical classification avoids precision loss for large integers and timezone-
dependent `Date` parsing. The Python implementation may later adopt the same
language-neutral classifier in a separate parity PR; this RFC does not silently
change it.

Explicit casts are strict. Invalid syntax, conflicting declarations,
incompatible values, structured/scalar mismatches and unused paths are errors.
Dotted paths address nested fields and scalar list items exactly as in Python.

## JSON Schema export

JSON Schema output targets draft 2020-12 and is generated directly from the
contract graph.

The exporter guarantees:

- deterministic object and definition ordering;
- requiredness independent from nullable `anyOf` entries;
- nested structures extracted into stable `$defs` where reuse or recursion
  requires them;
- Unicode-preserving, collision-checked model names;
- explicit `format: date` and `format: date-time`;
- no accidental closure of producer extension fields unless a future explicit
  policy requests it.

The report envelope remains compatible with Python:

```ts
interface SchemaReport {
  readonly root: string;
  readonly totalTypes: number;
  readonly inferredTypes: boolean;
  readonly casts: readonly string[];
  readonly schemas: Readonly<Record<string, JsonSchema>>;
}
```

Serialized CLI keys follow the existing snake-case contract even when the
library API uses idiomatic camelCase.

## Zod export

Zod source is generated from the contract graph, not by parsing emitted JSON
Schema. This removes a lossy conversion layer and makes both exporters peers.

Generated source:

- imports from `zod` by default;
- supports an Astro import mode when explicitly requested;
- quotes object keys;
- preserves nullable versus optional ordering;
- uses `z.iso.date()` and `z.iso.datetime()` for current Zod releases;
- detects top-level and nested identifier collisions before rendering;
- is formatted deterministically.

A package-level option controls the import target rather than hard-coding
`astro:content`, making the TypeScript package useful outside Astro while
retaining an exact Astro mode.

## Public API

The initial API is function-oriented and accepts `URL` or filesystem paths:

```ts
export function parseDocument(path: string | URL): Promise<ParsedDocument>;
export function loadBundle(root: string | URL, options?: LoadOptions): Promise<Bundle>;
export function validateBundle(root: string | URL, options?: LoadOptions): Promise<ValidationReport>;
export function inventoryBundle(root: string | URL, options?: LoadOptions): Promise<InventoryReport>;
export function graphBundle(root: string | URL, options?: LoadOptions): Promise<GraphReport>;
export function exportJsonSchema(root: string | URL, options?: SchemaOptions): Promise<SchemaReport>;
export function exportZod(root: string | URL, options?: ZodOptions): Promise<string>;
```

All option and result types are exported. Internal AST and cache types are not.
Abort signals are accepted by filesystem-wide operations so callers can cancel
long traversals.

## CLI

The TypeScript CLI initially exposes:

```text
okf-parser-ts check PATH
okf-parser-ts inventory PATH
okf-parser-ts graph PATH
okf-parser-ts schema PATH --format json|zod
```

Common options retain familiar spelling:

```text
--exclude PATTERN
--infer-types
--cast FIELD=TYPE
```

Machine-readable commands write stable JSON to stdout and diagnostics to stderr.
Exit status is part of the conformance corpus:

- `0`: command completed and no normative error applies;
- `1`: valid invocation whose report contains normative errors;
- `2`: invalid options, configuration or unrecoverable runtime failure.

This separates content non-conformance from CLI misuse more clearly than a single
failure status.

## Formatting

Formatting is intentionally not in the first implementation PR.

The existing Python formatter has a load-bearing contract involving mdformat,
ordered-list marker policy and a protected-block structural signature. A quick
Prettier wrapper would produce a nominally similar command with materially
different safety guarantees.

Formatter parity requires its own design and fixtures covering:

- ordered-list starts and CommonMark digit limits;
- protected block attributes;
- code, raw HTML and frontmatter content;
- GFM tables;
- write refusal when structure changes;
- exact `changed_paths` and `skipped_paths` behavior.

The TypeScript implementation may use a different renderer, but it must satisfy
those fixtures before the command is exposed.

## DuckDB

DuckDB support is an adapter layered on the immutable bundle model. It is not a
dependency of the parser core.

The adapter should use the maintained Node DuckDB API, materialize the same four
tables and preserve overwrite/error behavior. It ships as an optional export or
optional dependency so browser-oriented users do not install native database
artifacts.

Schema and row parity are tested against shared fixtures rather than database
file bytes.

## MCP

MCP support follows the stable TypeScript SDK and exposes the same read-only tool
set. MCP handlers call the public library API; they do not contain independent
business logic.

Tool input aliases and output shapes are conformance-tested against Python. No
write-capable formatter tool is exposed unless the existing read-only policy is
changed explicitly.

## Dependency policy

Dependencies are chosen for a narrow, auditable responsibility:

- `yaml` for YAML parsing with a restricted schema;
- a CommonMark parser for tokenized link extraction;
- a CLI parser with first-class TypeScript declarations;
- no runtime validation framework merely to reproduce Pydantic internals;
- no dataframe or graph dependency until an exposed operation requires it.

Development uses TypeScript strict mode, Vitest, a formatter/linter and package
build smoke tests. Dependencies are pinned by a committed lockfile.

All runtime dependencies must be ESM-compatible, actively maintained and
compatible with the supported Node.js range.

## Error model

Programmatic errors extend a common `OkfParserError` and carry stable machine
fields:

```ts
class OkfParserError extends Error {
  readonly code: string;
  readonly path?: string;
  readonly details?: Readonly<Record<string, JsonValue>>;
}
```

Expected subclasses include parse, exclusion-pattern, cast, name-collision and
export errors.

Content diagnostics remain values in reports. Exceptions are reserved for
situations where the requested operation cannot produce a meaningful report.

## Determinism

Every cross-runtime artifact must define its ordering:

- paths: POSIX lexical order;
- concept types: Unicode code-point order after exact value comparison;
- object fields: lexical authored-key order for reports unless a format requires
  another canonical order;
- diagnostics: logical path, code, then stable detail key;
- generated definitions: deterministic dependency order;
- JSON: UTF-8, no ASCII escaping requirement, stable key ordering at CLI
  serialization boundaries.

Absolute roots are normalized in conformance tests. Concept IDs must remain
checkout-location independent.

## Performance and caching

Correctness comes before optimization, but the architecture avoids obvious
quadratic work:

- each file is read once per bundle load;
- parsed Markdown tokens serve both headings and links;
- indexes are constructed in one pass;
- schema observations are grouped by concept type and field path once;
- exporters consume the contract graph without reparsing documents.

A future cache must key on logical input content and options, not only mtime, and
must never alter diagnostics or ordering.

## Security

The parser treats bundles as untrusted input:

- no executable YAML tags;
- aliases and nesting subject to explicit limits;
- no network resolution during validation;
- local link resolution cannot escape the requested bundle root;
- filesystem writes absent from the initial package;
- generated Zod is data-derived source with escaped identifiers and literals;
- MCP remains read-only.

Resource limits and symlink policy must be explicit conformance cases before the
first implementation is declared complete.

## CI

The root workflow gains an independent `typescript-quality` job:

1. install the pinned Node.js and package-manager versions;
2. install from the committed lockfile;
3. check formatting and lint;
4. run TypeScript with strict settings and no emit;
5. run unit and conformance tests;
6. build ESM and declaration outputs;
7. install the produced tarball into a clean temporary project;
8. execute CLI smoke tests;
9. compare normalized cross-runtime conformance results.

Python CI remains independent so one runtime cannot hide failures in the other.
The release workflow requires both jobs and verifies matching package versions.

## Release and versioning

The Python and npm packages share one SemVer.

A change is:

- patch when it fixes parity without changing the accepted contract;
- minor when it adds a backward-compatible command, option or output field;
- major when it changes existing observable semantics incompatibly.

A runtime may temporarily lag only on capabilities explicitly marked
`not_implemented` in a versioned capability manifest. Published documentation
must not claim full parity while such entries remain.

## Capability manifest

Each package exports a manifest such as:

```json
{
  "protocol_version": "0.6.0",
  "capabilities": {
    "check": "stable",
    "inventory": "stable",
    "graph": "stable",
    "schema_json": "stable",
    "schema_zod": "stable",
    "format": "not_implemented",
    "duckdb": "not_implemented",
    "mcp": "not_implemented"
  }
}
```

This prevents package presence from being mistaken for complete parity and gives
CI a concrete target as later PRs close the gaps.

## Implementation plan

### PR 1: conformance foundation and core package

- add the TypeScript package skeleton and locked toolchain;
- add the capability manifest;
- introduce shared frontmatter, discovery, exclusion and diagnostic fixtures;
- implement parsing, discovery, bundle loading and validation;
- implement inventory and graph reports;
- build the typed ESM package and CLI;
- add the independent TypeScript CI job.

### PR 2: contract graph and schema exporters

- add shared schema conformance cases;
- implement aggregate observation and contract graph construction;
- implement exact lexical inference and strict casts;
- generate JSON Schema draft 2020-12;
- generate generic and Astro-mode Zod source;
- add differential tests against accepted Python behavior;
- where behavior intentionally improves, update the corpus first and adapt
  Python in a separate commit or PR.

### PR 3: MCP adapter

- add TypeScript MCP server and read-only tools;
- validate tool schemas and aliases;
- add transport and clean-install smoke tests.

### PR 4: DuckDB adapter

- materialize concepts, links, reserved documents and diagnostics;
- preserve overwrite protection;
- keep the native dependency optional.

### PR 5: formatter parity

- establish shared structural-signature fixtures;
- implement canonical ordered-list rendering;
- expose check and explicit write modes only after safety parity passes.

The sequence may split further when a reviewable unit becomes too large. It must
not collapse into one cross-runtime mega-PR.

## Acceptance criteria

The TypeScript initiative is complete when:

- the npm package installs into a clean Node.js project;
- public APIs have generated declaration files and no implicit `any`;
- all stable capabilities in the manifest pass the shared corpus;
- the Python package also consumes the shared corpus;
- malformed files aggregate into diagnostics rather than aborting discovery;
- frontmatter scalar spelling, nulls and merge keys match the accepted contract;
- exclusion behavior and logical paths match across operating systems;
- schema requiredness, nullability, lists, nested objects, casts and Unicode
  collisions are covered;
- JSON Schema and Zod outputs validate representative accepted and rejected
  documents;
- CLI output and exit statuses are conformance-tested;
- package builds are deterministic enough for clean-install smoke tests;
- release automation rejects version drift between PyPI and npm;
- no capability is advertised as stable while its corpus is skipped.

## Alternatives considered

### Generate TypeScript from Python

Rejected. Generated wrappers would distribute Python semantics but would not
provide a native Node.js parser, filesystem model or CLI.

### Rewrite the repository as TypeScript

Rejected. The Python integrations are valuable and mature; replacement creates
migration risk without improving the cross-runtime contract.

### Share one implementation through WebAssembly

Rejected for now. YAML, Markdown, filesystem and integration behavior would still
need host adapters, while debugging and packaging become more complex. A future
language-neutral core is possible only after the conformance protocol is stable.

### Use JSON Schema as the only intermediate representation

Rejected. JSON Schema is an export target and cannot cleanly preserve every
inference decision, source observation and exporter-specific naming concern.
The explicit contract graph remains the richer internal form.

### Publish from a separate repository

Rejected. It would make shared fixtures, synchronized releases and atomic
contract changes harder, and would encourage drift.

## Open questions

1. Should the generic Zod import be the default, with Astro selected by
   `--zod-import astro`, or should compatibility keep Astro as the CLI default
   for the first release?
2. Which currently supported Node.js major should define the minimum runtime at
   implementation time?
3. Should browser-compatible parsing from in-memory strings be a first-release
   subpath export, or follow after the Node filesystem API stabilizes?
4. Should exact lexical inference be adopted by Python before the TypeScript
   schema exporter is marked stable, or may the corpus initially preserve the
   current pandas-backed edge behavior?
5. Should optional DuckDB and MCP adapters live in the same npm package through
   subpath exports, or become separate scoped packages after usage is measured?

## Decision

Proposed.

Implementation begins only after this RFC is accepted. Acceptance authorizes the
incremental sequence above, not a single all-at-once port. Any material change to
the compatibility boundary, package identity, shared-version policy or
conformance ownership requires an RFC amendment before code lands.
