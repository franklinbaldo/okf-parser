---
type: RFC
title: Effect-aware MCP mutation tools
status: accepted
description: Expose okf-parser write capabilities through MCP with per-tool effect metadata, explicit commit-tool enablement, preview/commit separation, and no server-wide read-only fiction
---

# RFC 0008: Effect-aware MCP mutation tools

## Summary

`okf-parser` already has file-writing capabilities with materially different
effects: `format --write` rewrites Markdown style, `apply --write` mutates
frontmatter through RFC 0005's staged relational write path, `init --write`
creates missing specification files, `import --write` creates or replaces
concept documents, and `duckdb` writes a persistent database. The Python MCP
surface currently exposes none of those explicit commit operations.

That is an implementation choice, not a constraint of MCP. MCP already has
standard `ToolAnnotations` for `readOnlyHint`, `destructiveHint`,
`idempotentHint` and `openWorldHint`, and FastMCP already supports them.

This RFC therefore replaces the architectural idea that "the MCP server is
read-only" with a more precise model:

1. every tool declares its own maximum possible effect;
2. preview and commit are separate MCP tools when one service already supports
   both modes;
3. explicit commit tools are hidden unless the server is launched with
   `okf-parser serve --allow-write`;
4. annotations are descriptive hints, never authorization or a safety
   boundary;
5. MCP writers reuse the exact service functions and deterministic guards used
   by the CLI;
6. the existing `okf-parser-mcp` entry point remains **commit-disabled** by
   default for compatibility and least surprise.

"Commit-disabled" is deliberate wording. This RFC does **not** promise that
the default server is globally side-effect-free. RFC 0006 already makes
`.schema.sql` trusted DuckDB SQL executed by `schema --spec-template`; trusted
SQL can itself perform I/O or interact with external resources. The correct
boundary is therefore whether `okf-parser` exposes its own explicit commit
operations, not a blanket claim that every inspection-shaped tool is pure.

The intended agent workflow becomes first-class instead of forcing a shell
escape at the moment a change is ready to commit:

```text
check
  ↓
inventory / schema / graph
  ↓
apply_preview
  ↓
inspect changed_paths + diagnostics
  ↓
apply_write
  ↓
check
```

The useful boundary is **preview versus commit**, not **CLI versus MCP**.

This RFC changes no mutation semantics. It only defines how existing
capabilities are surfaced through MCP and how their effects are described.

## Motivation

### The current CLI/MCP boundary is accidental

The current Python MCP registration in `cli.py` exposes `check`, `inventory`,
`graph`, `schema` and `format_check`. The file-producing operations remain
CLI-only.

That boundary made sense while the project itself had almost no write surface.
It no longer describes the product. RFC 0005 deliberately added a guarded
write path with dry-run by default, candidate-tree validation, concurrent-edit
checks, atomic replacement and lossless round-trip requirements. `init` and
`import` already distinguish planning from writing. `format` already has
separate check/write service functions.

An MCP client can therefore inspect a bundle, derive a mutation and validate a
candidate, but today it must leave MCP solely to perform the final commit.
That does not add a protocol-level guarantee; it only removes a composition
point.

### MCP already has effect vocabulary

The MCP schema defines these tool annotations:

- `readOnlyHint` — whether the tool modifies its environment;
- `destructiveHint` — for a mutating tool, whether it may replace/remove
  existing state rather than only add state;
- `idempotentHint` — whether repeating the same call has no additional effect;
- `openWorldHint` — whether the tool may interact with entities outside a
  closed local domain.

As of the MCP 2025-11-25 schema, the conservative defaults are
`readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=false`, and
`openWorldHint=true`. The specification is explicit that annotations are
**hints**, not enforcement. FastMCP exposes these annotations directly on
`@mcp.tool`.

References:

- <https://modelcontextprotocol.io/specification/2025-11-25/schema>
- <https://gofastmcp.com/servers/tools#mcp-annotations>

The project should use that vocabulary instead of flattening every tool into
one server-wide risk label.

### Static annotations favor separate preview and commit tools

The CLI correctly uses `--write`: a human command line naturally expresses
"show me first, then do it" as one command with a flag.

MCP annotations are static metadata on a tool definition. A hypothetical tool:

```text
apply(..., write=false)
```

would be read-only for one call and mutating for another, yet could advertise
only one `readOnlyHint`. Marking it read-only would be false for `write=true`;
marking it mutating would make every preview look riskier than it is.

MCP should therefore expose preview and commit as distinct tools where the
underlying service already has those two modes.

### RFC 0006 already disproves a simplistic global read-only claim

RFC 0006 intentionally treats `.schema.sql` as trusted DuckDB SQL. The file is
executed whole on a dedicated connection; the project checks the resulting
catalog rather than restricting SQL to an inert DDL grammar.

That is a valid design choice, but it matters for MCP effect metadata. A public
`schema` tool whose inputs include `spec_template` may execute author-supplied
DuckDB SQL. Such SQL can read external resources and may perform environment
side effects permitted by DuckDB and the process identity.

So `schema` cannot honestly receive `readOnlyHint=true` merely because its
*product-level purpose* is schema inspection. Tool annotations describe what a
tool may do, not what its name sounds like.

This RFC uses that fact rather than hiding it: annotations are conservative,
and `--allow-write` gates explicit project-owned commit tools rather than
pretending it creates a universal read-only sandbox.

## Decision

### 1. MCP is effect-aware, not globally read-only

There is no project invariant that an MCP server may only inspect state.
Instead, tools fall into practical classes:

1. **inspection** — primarily observes or derives state;
2. **preview** — computes the candidate effect of a mutating operation without
   committing that operation;
3. **commit** — intentionally creates, replaces or mutates persistent state.

Those classes are useful product concepts, but MCP annotations remain grounded
in actual possible effects. An inspection tool that executes trusted user SQL
may still need non-read-only annotations.

The CLI remains free to combine preview and commit under one command plus
`--write`; this RFC does not try to make the CLI resemble MCP.

### 2. The default profile is commit-disabled, not promised read-only

The server has two profiles:

```bash
# Inspection/preview profile: no explicit commit tools.
okf-parser serve

# Inspection/preview plus explicit commit tools.
okf-parser serve --allow-write
```

`--allow-write` controls **tool visibility**. In the default profile, the
commit tools defined by this RFC do not appear in `tools/list` and cannot be
called. In the write-capable profile, they are present.

This is a capability boundary for `okf-parser`'s own commit operations. It is
not a sandbox around arbitrary trusted code executed by another capability,
such as RFC 0006 declaration SQL.

That distinction is preferable to calling the default server "read-only" when
one existing tool can already execute trusted SQL.

### 3. Preview and commit are separate tools where the service has both modes

The proposed Python MCP surface is:

| Capability | Inspection/preview tool | Commit tool |
| --- | --- | --- |
| validation | `check` | — |
| inventory | `inventory` | — |
| graph | `graph` | — |
| schema export | `schema` | — |
| formatting | `format_check` | `format_write` |
| relational frontmatter mutation | `apply_preview` | `apply_write` |
| type-spec scaffolding | `init_preview` | `init_write` |
| tabular import | `import_preview` | `import_write` |
| persistent DuckDB export | — | `duckdb_export` |

`format_check` keeps its existing name because it is already public. The other
previews use `_preview` because their corresponding CLI operations are
conceptually write operations whose default happens to be a dry-run.

There is no MCP `write: bool` switch on any paired tool.

### 4. MCP tools are thin adapters over the existing service layer

No MCP-specific mutation engine is introduced.

Conceptually:

```python
@mcp.tool(...)
def apply_preview(...):
    return apply_bundle(..., write=False)

@mcp.tool(...)
def apply_write(...):
    return apply_bundle(..., write=True)
```

The same rule applies to `init` and `import`; `format_write` calls the existing
`write_format`; `duckdb_export` calls `export_duckdb`.

This is normative: an MCP wrapper may adapt argument names, visibility and
annotations, but it must not develop a second validation, parsing, staging,
overwrite or serialization path. Safety properties belong in the service
function shared by CLI and MCP.

Consequences:

- RFC 0005's candidate-tree, validation, conflict and atomic-write semantics
  automatically govern `apply_write`;
- future RFC 0006 changes to typed materialization automatically reach MCP when
  they reach the shared service;
- `init_write` keeps `init`'s collision behavior;
- `import_write` keeps duplicate-id and overwrite behavior;
- `format_write` keeps protected-structure refusal;
- `duckdb_export` keeps the existing collision/`overwrite` contract.

### 5. Annotations describe the maximum effect of the public tool

Annotations are static. If one legal argument combination makes a tool more
powerful, the annotation describes that maximum possible effect rather than the
safest default invocation.

The initial matrix is:

| Tool | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
| --- | --- | --- | --- | --- |
| `check` | `true` | `false` | `true` | `false` |
| `inventory` | `true` | `false` | `true` | `false` |
| `graph` | `true` | `false` | `true` | `false` |
| `schema` | `false` | `true` | `false` | `true` |
| `format_check` | `true` | `false` | `true` | `false` |
| `apply_preview` | `false` | `true` | `false` | `true` |
| `init_preview` | `true` | `false` | `true` | `false` |
| `import_preview` | `true` | `false` | `true` | `true` |
| `format_write` | `false` | `true` | `true` | `false` |
| `apply_write` | `false` | `true` | `false` | `true` |
| `init_write` | `false` | `false` | `true` | `false` |
| `import_write` | `false` | `true` | `false` | `true` |
| `duckdb_export` | `false` | `true` | `false` | `true` |

For read-only tools, `destructiveHint=false` and `idempotentHint=true` are emitted explicitly even though MCP only gives those hints behavioral meaning for mutating tools. Explicit values keep `tools/list` deterministic and reviewable.

The less obvious rows are deliberate:

- **`schema` is conservatively non-read-only/open-world.** Its public MCP
  signature already accepts `spec_template`; RFC 0006 declaration discovery
  can execute trusted DuckDB SQL. The annotation describes what the tool can
  do in its most capable legal invocation, not the common no-template call.
- **`format_write` is destructive in MCP vocabulary.** It rewrites existing
  files even though the transformation is intended to preserve meaning.
  `destructiveHint=false` means additive-only, not "low risk".
- **`apply_write` is not advertised idempotent.** RFC 0005 explicitly does not
  promise that rerunning every converged SQL migration succeeds; a type rename
  can remove the table named by the original invocation.
- **`init_write` is additive.** Its contract scaffolds missing files and
  refuses unsafe collisions; it does not expose overwrite.
- **`import_write` is destructive at the tool level** because `overwrite` is
  part of the public input. A particular call with `overwrite=false` is safer,
  but the static tool can replace an existing destination.
- **`import_preview` and `import_write` are open-world conservatively.** The
  source is handed to DuckDB replacement scan and is not normatively limited
  by this project to a local-file scheme.
- **`duckdb_export` is destructive at the tool level** because `overwrite` can
  replace tables in an existing target database.

These annotations are part of the public MCP contract and should be tested via
`tools/list`, not left as unreviewed decorator literals.

### 6. Existing `schema` keeps its signature; the conservative annotation is the price

A cleaner abstract API could split schema export into a guaranteed-pure tool
without `spec_template` and a second tool that executes declarations. This RFC
does not do that because `schema` is already public and its MCP signature
already includes `spec_template`.

Removing that argument solely to recover `readOnlyHint=true` would be a
behavioral regression for annotation aesthetics.

The initial rule is therefore:

- keep `schema` compatible;
- annotate it for its maximum effect;
- consider a future pure alias such as `schema_observed` only if client UX
  demonstrates that the conservative annotation is materially costly.

This also establishes a useful project rule: **do not weaken actual capability
merely to make an annotation prettier**.

### 7. Effect annotations must be revisited when capability changes

A tool's annotation is not permanent metadata detached from implementation.
If another RFC adds a capability that expands what a tool can do, its
annotations must be reviewed in the same PR.

RFC 0006 now reaches both `apply` and `duckdb`. Therefore `apply_preview`
accepts `spec_template` and can execute trusted declaration SQL even though it does
not commit the candidate frontmatter changes. Its annotation is consequently
non-read-only, destructive, non-idempotent and open-world at the static tool level.
`apply_write` and `duckdb_export` are likewise open-world because they accept the
same declaration capability. This is the concrete example of why annotation tests
belong next to capability tests: a later capability expansion changed the honest
metadata without changing the preview/commit product distinction.

### 8. Annotations are never the safety mechanism

The MCP specification calls these fields hints. This RFC treats them exactly
that way.

A client may use them for UX or policy — automatic approval for reads, a
warning for destructive calls, retry decisions for idempotent operations — but
`okf-parser` assumes no client will enforce them correctly.

Hard guarantees remain below the annotation layer:

```text
MCP request
    ↓
server profile / host authorization
    ↓
shared service function
    ↓
operation-specific validation / staging / collision checks
    ↓
filesystem or DuckDB commit
```

No operation becomes safe because its decorator says `destructiveHint=true`,
and no service guard may be removed because a host is expected to ask for
confirmation.

### 9. `serve --allow-write` gates explicit commit tools

The commit tools introduced by this RFC are disabled by default and enabled
only when the server is launched with `--allow-write`.

FastMCP 3.x already has component visibility controls, including enabling and
disabling tools by tag. An implementation may tag commit tools or construct the
server conditionally; this RFC cares about observable behavior:

- default profile: explicit commit tools absent from `tools/list`;
- `--allow-write`: explicit commit tools present;
- annotations identical regardless of transport;
- one test process can construct both profiles without visibility state
  leaking between them.

The flag is necessary because annotations are hints rather than capability
enforcement. Upgrading a server used for inspection should not silently expose
new first-class file mutation tools.

### 10. `okf-parser-mcp` remains commit-disabled by default

The zero-argument `okf-parser-mcp` console entry point keeps the default
profile. It does not expose the commit tools added by this RFC.

A client that wants them can launch:

```text
command: okf-parser
args: [serve, --allow-write]
```

This avoids a second executable such as `okf-parser-mcp-write` and keeps the
capability choice visible in the client configuration that launches the
server.

The profile is independent of transport: `--allow-write` works with stdio,
HTTP or SSE.

The default profile may gain new **preview** tools because those are additive
inspection capabilities. Compatibility means no explicit commit authority is
silently added; it does not mean `tools/list` must remain forever frozen at
five names.

### 11. Preview tools are faithful previews, not advisory simulations

A preview tool invokes the same service function and planning path as its
commit counterpart with commit disabled. It exposes the same candidate set and
diagnostics the CLI dry-run exposes.

For example, `apply_preview` returns the same `changed_paths`, `skipped_paths`,
validation results and conflicts that `apply --write` would use as candidate
input at that moment. `apply_write` still performs RFC 0005's hash recheck at
commit time; preview is not a lock.

This RFC deliberately does not add a preview token authorizing a later write.
A tokenized two-phase transaction would introduce server state, expiry, replay
and recovery semantics that the current service layer does not need. The write
tool recomputes and rechecks its own candidate exactly as the CLI does.

### 12. No MCP-specific confirmation parameter

Commit tools do not take `confirm=true`, `yes=true`, a magic phrase or a
preview hash merely to prove that somebody approved something.

An agent can mechanically supply such a value, so it does not create an
authorization boundary. The real gates are:

- commit tools were enabled at server startup;
- the MCP host decides whether the caller may invoke them;
- the shared service validates whether the requested mutation is valid to
  commit.

If the project later needs authentication, per-root authorization or a policy
engine, that should be specified at the authorization layer rather than hidden
inside a boolean tool argument.

### 13. Filesystem authority is unchanged from the process

This RFC does not invent a workspace sandbox. Paths are resolved under the same
operating-system permissions as the running `okf-parser` process, just as with
the CLI.

That means `--allow-write` is a meaningful capability switch for explicit
writers, but not authentication and not filesystem isolation.

A future RFC may add configured roots or path capabilities if deployment
experience requires them. They are not prerequisites here because existing
read/inspection tools already accept caller-provided paths and each writer
already has its own collision/write contract.

### 14. `duckdb_export` is included without a preview pair

Persistent DuckDB export differs from `apply`, `init` and `import`: its useful
result is the output database file, and the current shared service has no
separate dry-run mode.

That is not a reason to keep it off MCP. It is exposed as one explicit commit
tool, conservatively annotated destructive because `overwrite=true` can
replace tables.

If the shared service later gains a real export plan, `duckdb_preview` may be
added without changing `duckdb_export`.

### 15. Python lands first; TypeScript parity is not a blocker

The native TypeScript package does not currently implement the Python write
surface (`apply`, `init`, `import`). It should not acquire duplicate
experimental implementations merely to preserve command-count symmetry.

This RFC defines semantics for any runtime that exposes a capability. The
Python FastMCP server can implement the full surface first. TypeScript should
use the same names and annotations when and if equivalent capabilities exist
there.

Read-side parity remains desirable; simultaneous write-side capability parity
is not required.

## Tool signatures

Public MCP inputs should mirror existing CLI/service concepts rather than
inventing a second vocabulary.

Illustrative signatures:

```python
apply_preview(
    path: str,
    sql: str | None = None,
    type: str | None = None,
    field: str | None = None,
    from_: str | None = None,
    to: str | None = None,
    exclude: list[str] | None = None,
    spec_template: str | None = None,
) -> dict[str, object]

apply_write(...same arguments...) -> dict[str, object]

init_preview(
    path: str,
    spec_template: str,
    exclude: list[str] | None = None,
    infer_schema: bool = False,
) -> dict[str, object]

init_write(...same arguments...) -> dict[str, object]

import_preview(
    source: str,
    path: str,
    type: str,
    id_column: str | None = None,
    overwrite: bool = False,
) -> dict[str, object]

import_write(...same arguments...) -> dict[str, object]

format_write(
    path: str,
    exclude: list[str] | None = None,
) -> dict[str, object]

duckdb_export(
    path: str,
    database: str = "okf.duckdb",
    schema: str = "okf",
    overwrite: bool = False,
    exclude: list[str] | None = None,
    spec_template: str | None = None,
) -> dict[str, object]
```

The preview and write forms of one operation take the same business inputs.
Only commit authority differs.

## Server construction

The current Python module owns one global `FastMCP` instance and decorates
tools at import time. Profiles make that shape somewhat awkward because public
tool visibility depends on server startup.

An implementation should choose the smaller of:

1. register all tools once, tag explicit commit tools, and disable that tag
   unless `--allow-write` is active; or
2. construct the FastMCP instance through a factory that conditionally
   registers commit tools.

The implementation uses the second: `build_mcp(allow_write=...)` constructs an
isolated FastMCP server and conditionally registers commit tools. This avoids a
process-global visibility switch entirely, so constructing a write-capable profile
cannot leak authority into the default profile.

The mechanism is an implementation detail; the observable contract remains:

- both profiles can be constructed independently in one test process;
- default profile has no explicit commit tools;
- write profile has them;
- tool schemas and annotations are stable across transports;
- CLI and MCP call the same service functions.

## Error and result semantics

MCP wrappers preserve the service payload as structured output wherever the
current tools already do so. An operation-level failure represented in a
service payload remains an operation-level result; invalid invocation or a
raised service exception follows the existing MCP adapter policy.

The wrapper must never translate "failed to commit" into success merely
because the MCP transport call itself completed.

CLI exit codes have no MCP equivalent. Existing structured payloads already
carry the necessary state (`succeeded`, `written`, `changed_paths`,
`duplicate_ids`, collision information, etc.), so this RFC does not invent a
parallel status model.

## Security model

Four layers are intentionally distinct:

```text
Tool annotation     → descriptive hint for clients
Server profile      → whether explicit commit tools exist
Host authorization  → whether this caller may invoke an exposed tool
Service invariants  → whether the requested operation is valid to perform
```

Only the last three can enforce anything. The first helps clients present and
route tools but is untrusted metadata by protocol design.

There is also a fifth category that must not be confused with first-class
commit tools: **trusted executable inputs**. RFC 0006 `.schema.sql` belongs
there. `--allow-write` does not sandbox such code; the relevant trust decision
is whether the operator permits that trusted declaration to be present and
executed under the server process identity.

For the same reason, this RFC does not claim `--allow-write` makes HTTP/SSE
safe for arbitrary remote callers. Network/authentication policy remains a
deployment concern.

## Rejected alternatives

### Keep MCP permanently limited to inspection tools

Rejected. It prevents the most useful end-to-end agent workflow at the commit
boundary without adding a protocol-level guarantee.

### Call the default profile globally read-only

Rejected as inaccurate. Existing `schema --spec-template` can execute trusted
RFC 0006 DuckDB SQL. The default profile is instead **commit-disabled** with
respect to the explicit writers defined here.

### Expose one MCP tool with `write: bool`

Rejected. Static effect annotations would necessarily misdescribe some calls.
The CLI keeps `--write`; MCP uses separate preview/commit tools.

### Trust annotations as the write gate

Rejected. MCP explicitly defines annotations as hints, not authorization.

### Add `confirm=true` to every writer

Rejected. It is trivially supplied by an agent and duplicates host approval
without creating a real boundary.

### Require preview before write with a server-issued token

Rejected for v1. It would make the server stateful and create token expiry,
replay and recovery problems. Existing services already recompute and recheck
at commit time.

### Expose explicit commit tools unconditionally after upgrade

Rejected. Existing installations should not silently acquire first-class write
tools merely because annotations exist.

### Remove `spec_template` from MCP `schema` to recover `readOnlyHint=true`

Rejected. Capability should not be weakened to make metadata prettier. Keep the
public input and annotate its maximum effect honestly.

### Omit `duckdb_export` because it lacks dry-run

Rejected. Preview/write pairing is required where the service has two modes,
not as a universal prerequisite for any mutation.

### Block Python on TypeScript parity

Rejected. Shared semantics are required when a capability exists; simultaneous
implementation is not.

## Compatibility

The default entry point remains **commit-disabled**: upgrading does not expose
`format_write`, `apply_write`, `init_write`, `import_write` or
`duckdb_export` unless the operator opts in.

The default profile may gain `apply_preview`, `init_preview` and
`import_preview`; those are additive tool names and do not intentionally commit
the corresponding operation.

Existing tool names keep their meanings. `schema` keeps its current
`spec_template` input; the only proposed change to it is conservative effect
annotation.

Documentation and server instructions should stop saying MCP is "read-only by
design" and instead describe the concrete tools/profile actually exposed.

## Implementation plan

A minimal implementation is independent of the remaining RFC 0006 type work
because it adapts service functions that already exist:

1. add reviewed effect annotations to the five existing MCP tools, including
   the conservative `schema` annotation required by RFC 0006 trusted SQL;
2. introduce profile-aware server construction and `serve --allow-write`;
3. add `apply_preview`/`apply_write`, `init_preview`/`init_write`,
   `import_preview`/`import_write`, `format_write`, and `duckdb_export` as thin
   adapters;
4. test `tools/list` in both profiles, including annotations;
5. test preview/write wrappers against direct service calls for payload
   equivalence;
6. update CLI/MCP documentation and server instructions to say
   "commit-disabled by default", not "read-only by design".

If RFC 0006 later adds declaration execution to a tool currently annotated
read-only, that integrating PR must update the annotation or preserve a truly
read-only path.

## Acceptance criteria

This RFC can move from `proposed` to `accepted` when the project agrees that:

- MCP is effect-aware rather than globally read-only;
- the default server is commit-disabled, not falsely described as a universal
  read-only sandbox;
- explicit commit tools require `serve --allow-write`;
- preview and commit are separate MCP tools where one service supports both;
- annotations describe each public tool's maximum possible effect;
- existing `schema` is annotated conservatively because RFC 0006 executes
  trusted declaration SQL;
- annotations are hints, not authorization;
- MCP writers reuse shared service functions rather than duplicating mutation
  logic;
- `okf-parser-mcp` remains commit-disabled by default;
- Python implementation does not wait for TypeScript write parity.

Implementation completion is separate from acceptance, as with the other RFCs.
