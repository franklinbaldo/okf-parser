---
type: RFC
title: Effect-aware MCP mutation tools
status: proposed
description: Expose okf-parser write capabilities through MCP without treating the whole server as read-only, separating preview and commit tools where effects differ, declaring standard MCP tool annotations, and keeping write-capable serving an explicit deployment choice
---

# RFC 0008: Effect-aware MCP mutation tools

## Summary

`okf-parser` already has file-writing capabilities with materially different
risk profiles: `format --write` rewrites Markdown style, `apply --write`
mutates frontmatter through RFC 0005's staged relational write path, `init
--write` creates missing specification files, `import --write` creates or
replaces concept documents, and `duckdb` writes a database file. The Python
MCP server currently exposes none of them. That is an implementation choice,
not a constraint of MCP.

This RFC replaces the server-wide "MCP is read-only" assumption with a
per-tool effect model:

1. read-only and preview tools remain read-only and say so explicitly;
2. write tools are exposed as separate tools rather than a `write: bool`
   switch on a conditionally-mutating tool;
3. every tool declares the standard MCP effect annotations that describe its
   maximum possible effect;
4. those annotations remain metadata, never the safety boundary;
5. write tools reuse the exact service functions and deterministic guards used
   by the CLI — no MCP-specific mutation implementation;
6. write-capable serving is an explicit server profile, enabled with
   `okf-parser serve --allow-write`; the existing `okf-parser-mcp` entry point
   remains read-only by default for compatibility and least surprise.

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

The important boundary is therefore **preview versus commit**, not **CLI
versus MCP**.

This RFC does not change the semantics of `apply`, `format`, `init`, `import`
or `duckdb`. It only defines how already-existing capabilities are surfaced
through MCP and how their effects are described.

## Motivation

### The current boundary is accidental

The current Python MCP registration in `cli.py` exposes `check`, `inventory`,
`graph`, `schema` and `format_check`. The module-level instructions explicitly
say formatting checks are read-only and that no exposed tool rewrites files.
That made sense while the project itself had almost no write surface.

It no longer describes the product. RFC 0005 deliberately added a robust
write path with dry-run by default, candidate-tree validation, conflict
checks, atomic replacement and lossless round-trip requirements. `init` and
`import` also already distinguish planning from writing. Keeping those
operations CLI-only means an MCP client can inspect a bundle, derive the exact
mutation it wants, preview it, and then must leave the protocol solely to
perform the final commit.

That boundary does not buy a technical guarantee. It only removes a useful
composition point.

### MCP already models tool effects

The MCP schema defines `ToolAnnotations` with four effect-relevant hints:

- `readOnlyHint` — whether the tool modifies its environment;
- `destructiveHint` — for a mutating tool, whether it may replace or remove
  existing state rather than only add state;
- `idempotentHint` — whether repeating the same call has no additional effect;
- `openWorldHint` — whether the tool may interact with entities outside a
  closed local domain.

As of the MCP 2025-11-25 schema, the conservative defaults are
`readOnlyHint=false`, `destructiveHint=true`, `idempotentHint=false`, and
`openWorldHint=true`. The specification is also explicit that annotations are
**hints**, not an enforcement boundary. FastMCP, already used by this project,
supports these annotations directly on `@mcp.tool`.

References:

- <https://modelcontextprotocol.io/specification/2025-11-25/schema>
- <https://gofastmcp.com/servers/tools#mcp-annotations>

The protocol therefore has a vocabulary for this distinction already. A
server-wide read-only rule throws that vocabulary away.

### One conditional tool gives clients worse information

The CLI correctly uses `--write` because a human command line naturally
expresses "show me first, then do it" as one command with a flag. MCP tool
annotations are static metadata attached to a tool definition. A hypothetical
MCP tool such as:

```text
apply(..., write=false)
```

would be read-only for one invocation and mutating for another, yet it could
only advertise one `readOnlyHint` value. Marking it read-only would be false
for `write=true`; marking it mutating would make every preview look riskier
than it is.

The MCP surface should therefore expose the two effects as two tools.

## Decision

### 1. MCP is effect-aware, not globally read-only

There is no project invariant that an MCP server may only inspect state.
Instead, every MCP tool belongs to one of three practical classes:

1. **read** — observes state and does not modify it;
2. **preview** — computes the exact candidate effect of an existing mutating
   operation without committing it;
3. **write** — commits an operation that may modify local state.

The CLI remains free to combine preview and write under one command plus
`--write`; this RFC does not try to make the CLI resemble MCP.

The MCP API optimizes for accurate static effect metadata, so it separates
those phases where necessary.

### 2. Preview and write are separate tools whenever one service has both modes

The following Python MCP surface is proposed:

| Capability | Preview/read tool | Commit tool |
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

The names deliberately make commit-capable tools impossible to confuse with a
preview in a tool picker or trace.

`format_check` keeps its existing name because it is already public. The
other previews use `_preview` because their corresponding CLI operations are
conceptually write operations whose default happens to be a dry-run.

There is no `write` boolean on any MCP tool in this table.

### 3. MCP tools are thin adapters over the existing service layer

No new mutation engine is introduced.

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
`write_format`; `duckdb_export` calls the existing `export_duckdb`.

This is normative: an MCP tool may adapt argument names or annotate effects,
but it must not develop a second validation, parsing, staging, overwrite or
serialization path. If a safety property belongs to the operation, it belongs
in the service function shared by CLI and MCP.

Consequences:

- RFC 0005's candidate-tree, validation, conflict and atomic-write semantics
  automatically govern `apply_write`;
- future RFC 0006 changes to typed materialization automatically reach the MCP
  tool once they reach the shared service;
- `init_write` keeps `init`'s collision behavior;
- `import_write` keeps duplicate-id and overwrite behavior;
- `format_write` keeps protected-structure refusal;
- `duckdb_export` keeps the existing collision/`overwrite` contract.

### 4. Standard MCP annotations describe the maximum effect of each tool

Annotations are static. When a tool parameter can make an operation more
aggressive — for example `overwrite=true` — its annotation describes the
**maximum effect the tool can perform**, not the least risky default call.

The initial matrix is:

| Tool | `readOnlyHint` | `destructiveHint` | `idempotentHint` | `openWorldHint` |
| --- | --- | --- | --- | --- |
| `check` | `true` | n/a | n/a | `false` |
| `inventory` | `true` | n/a | n/a | `false` |
| `graph` | `true` | n/a | n/a | `false` |
| `schema` | `true` | n/a | n/a | `false` |
| `format_check` | `true` | n/a | n/a | `false` |
| `apply_preview` | `true` | n/a | n/a | `false` |
| `init_preview` | `true` | n/a | n/a | `false` |
| `import_preview` | `true` | n/a | n/a | `true` |
| `format_write` | `false` | `true` | `true` | `false` |
| `apply_write` | `false` | `true` | `false` | `false` |
| `init_write` | `false` | `false` | `true` | `false` |
| `import_write` | `false` | `true` | `false` | `true` |
| `duckdb_export` | `false` | `true` | `false` | `false` |

`destructiveHint` is omitted/irrelevant for read-only tools per MCP semantics.

The less obvious rows are deliberate:

- **`format_write` is destructive in MCP vocabulary.** It rewrites existing
  files, even though the transformation is intended to preserve document
  meaning. `destructiveHint=false` means additive-only, not "probably safe".
- **`apply_write` is not advertised idempotent.** RFC 0005 explicitly does
  not promise that rerunning every converged SQL migration succeeds; a type
  rename can remove the table named by the original invocation. The
  conservative annotation is therefore `false`.
- **`init_write` is additive.** Its write contract scaffolds missing files and
  refuses unsafe collisions; it does not expose an overwrite mode.
- **`import_write` is destructive at the tool level** because its public input
  includes `overwrite`; a particular call with `overwrite=false` is safer,
  but the static tool can replace an existing destination.
- **`import_preview` and `import_write` are open-world conservatively.** The
  source is handed to DuckDB's replacement scan and is not normatively limited
  by this project to one local-file scheme. If the underlying DuckDB
  installation can resolve a remote source, the tool can reach it.
- **`duckdb_export` is destructive at the tool level** because `overwrite`
  can replace tables in an existing target database.

These values are part of the public MCP contract and should have tests against
`list_tools`, not merely decorator literals that can drift unnoticed.

### 5. Annotations are never the safety mechanism

The MCP specification calls these fields hints. This RFC treats them exactly
that way.

A client may use the annotations to improve UX — automatic approval for reads,
a confirmation prompt for destructive writes, retry policy for idempotent
operations — but `okf-parser` does not assume any client will honor them.

Hard guarantees remain below the MCP layer:

```text
MCP request
    ↓
service function
    ↓
operation-specific validation / staging / collision checks
    ↓
filesystem or DuckDB commit
```

No operation becomes safer merely because its decorator says
`destructiveHint=true`, and no service guard may be removed because a host is
expected to ask for confirmation.

### 6. Write-capable serving is explicit: `serve --allow-write`

The project should support two deployment profiles from the same server
implementation:

```bash
# Existing behavior: only read/preview tools are visible.
okf-parser serve

# Read/preview plus commit tools.
okf-parser serve --allow-write
```

`--allow-write` controls **tool visibility**, not a runtime check inside each
call. In the default profile, commit tools do not appear in `tools/list` and
cannot be called. In the write-capable profile, they are registered/enabled
with the annotations in decision 4.

FastMCP 3.x already has component visibility controls, including enabling and
disabling tools by tag. An implementation may use tags such as `{"write"}`
or build the server from a factory; this RFC cares about the observable
contract, not which FastMCP mechanism implements it.

This explicit profile is necessary because annotations are hints rather than
capability enforcement. Someone starting a server for inspection should not
silently gain write authority after upgrading.

### 7. `okf-parser-mcp` remains read-only by default

The zero-argument `okf-parser-mcp` console entry point keeps its existing
behavior and starts the default profile.

A client that wants write tools can configure the normal CLI entry point with
arguments:

```text
command: okf-parser
args: [serve, --allow-write]
```

This avoids a second executable such as `okf-parser-mcp-write` and keeps the
privilege choice visible in the client configuration that launches the
server.

The profile is a deployment decision, not a property of stdio versus HTTP.
`--allow-write` works with any supported transport.

### 8. Preview tools must be faithful previews, not advisory simulations

A preview tool invokes the same service function and same planning path as its
commit counterpart with the commit disabled. It must therefore expose the
same candidate set and diagnostics the CLI dry-run exposes.

For example, `apply_preview` returns the same `changed_paths`,
`skipped_paths`, validation results and conflicts that `apply --write` would
use as its candidate input at that moment. `apply_write` is still required to
perform RFC 0005's hash recheck before commit; preview is not a lock and never
turns a later write into a blind commit.

This RFC deliberately does **not** add a preview token that authorizes a later
write. A tokenized two-phase transaction would introduce server-side state,
expiry, replay semantics and recovery questions that the current
stateless service layer does not need. The write tool recomputes and rechecks
its own candidate, exactly as the CLI does.

### 9. No MCP-specific confirmation parameter

Commit tools do not take `confirm=true`, `yes=true`, a magic phrase, or a
preview hash solely to prove that a human clicked something.

Those parameters are poor protocol design because an agent can mechanically
supply them and they duplicate host-level approval UX without creating an
authorization boundary.

The actual gates are:

- the server was launched with `--allow-write`;
- the MCP host decides whether and how to approve a mutating tool call;
- the shared service enforces the operation's deterministic invariants.

If this project later needs authentication or per-root authorization, that is
an authorization-layer feature and should be specified as such, not encoded as
a boolean tool argument.

### 10. Filesystem authority is unchanged from the server process

This RFC does not invent a workspace sandbox. A path accepted by an MCP tool is
resolved under the same operating-system permissions as the process running
`okf-parser`, exactly like the CLI.

That means `--allow-write` is a meaningful capability switch: once enabled,
the server can mutate paths that its service functions accept and that its OS
identity can write.

A future RFC may add configured roots or path capabilities if real deployment
experience needs them. They are intentionally not prerequisites here because
reads already accept caller-provided paths and because each existing writer
has its own path/collision contract. Pretending an annotations field is a path
sandbox would be worse than leaving this boundary explicit.

### 11. `duckdb_export` is included even though it has no preview pair

The persistent DuckDB command is different from `apply`, `init` and `import`:
its useful result *is* an output database file, so the current service has no
separate dry-run mode.

That is not a reason to keep it off MCP. It is exposed as one explicit commit
tool, conservatively annotated as destructive because `overwrite=true` can
replace tables.

If later work adds an export plan/dry-run to the shared service, a
`duckdb_preview` tool may be added without changing `duckdb_export`.

### 12. Python lands first; TypeScript parity is not a blocker

The native TypeScript package currently does not implement the Python write
surface (`apply`, `init`, `import`) and should not acquire duplicate
experimental implementations merely to preserve command-count symmetry.

This RFC defines the MCP semantics for any runtime that exposes a capability.
The Python FastMCP server can implement the full table in decision 2 first.
TypeScript should use the same names and annotations when and if the
corresponding write capability exists there.

Read-side parity remains desirable; write-side capability parity is not a
precondition for accepting or implementing this RFC.

## Tool signatures

The exact Python spelling may follow existing service names, but the public
MCP inputs should mirror the CLI/service parameters rather than introducing a
second vocabulary.

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
) -> dict[str, object]
```

`from_` may continue to be the Python implementation name for the public
`from` field where the MCP/Pydantic aliasing layer supports it. The important
contract is that the preview and write tool for one operation take the same
business inputs.

## Server construction

The current module owns one global `FastMCP` instance and decorates tools at
import time. Write profiles make that shape somewhat awkward because public
tool visibility now depends on how the server is launched.

An implementation should choose one of two small designs:

1. **register all tools once, tag commit tools, disable the write tag unless
   `--allow-write` is active**; or
2. **build the FastMCP instance through a small factory that conditionally
   registers commit tools**.

The first is preferred if FastMCP's visibility API preserves clean tool-list
semantics and test isolation. The second is preferable if mutable global
visibility leaks between tests or repeated in-process server construction.

The RFC intentionally does not mandate one before implementation measures the
simpler option. It does mandate these observable properties:

- default profile: commit tools absent from `tools/list`;
- write profile: commit tools present;
- annotations identical regardless of transport;
- the same service functions back CLI and MCP;
- one test process can construct both profiles without state leaking from one
  into the other.

## Error and result semantics

MCP wrappers should preserve the service payload as structured output wherever
the current read tools already do so. An operation-level failure represented
in the service payload remains an operation-level result; an invalid invocation
or raised service exception remains an MCP tool error according to the same
adapter policy used by existing tools.

The write wrapper must not convert "failed to commit" into success merely
because the MCP transport call itself completed.

Where CLI exit codes currently encode success (`apply` uses
`payload["succeeded"]`, `import` uses duplicate ids, `duckdb` maps
`BundleExportError`), the MCP tool has no process exit code. Its structured
payload must therefore retain enough state for the client to distinguish
planned, written, skipped and failed outcomes. Existing payloads already do;
this RFC does not add a parallel status model.

## Security model

This RFC intentionally distinguishes four layers that are easy to conflate:

```text
Tool annotation     → descriptive hint for clients
Server profile      → whether commit tools exist at all
Host authorization  → whether this caller may invoke an exposed tool
Service invariants  → whether the requested mutation is valid and safe to commit
```

Only the last three can enforce anything. The first is useful because clients
can present and route tools more intelligently, but an untrusted or buggy MCP
server can lie in its annotations by definition.

For the same reason, this RFC does not claim that `--allow-write` makes an MCP
server safe to expose to arbitrary remote callers. HTTP/SSE deployment still
requires whatever network/authentication policy the operator considers
appropriate. `--allow-write` is capability selection, not authentication.

## Rejected alternatives

### Keep MCP permanently read-only

Rejected. It prevents the most useful end-to-end agent workflow precisely at
the commit boundary without adding a protocol-level security property. The
CLI and MCP already share the same process authority and service code.

### Expose one tool with `write: bool`

Rejected for MCP. It makes static `readOnlyHint` necessarily wrong for half of
the tool's calls. The CLI keeps `--write`; the two surfaces need not encode
effects identically.

### Trust annotations as the write gate

Rejected. MCP explicitly defines annotations as hints. They are metadata for
client UX and policy, not authorization.

### Add `confirm=true` to every write tool

Rejected. It is trivially supplied by an agent, does not prove user intent,
and duplicates confirmation policy that belongs to the host.

### Require preview before every write with a server-issued token

Rejected for v1. It would make the server stateful and require token expiry,
replay and recovery semantics. Existing write services already re-evaluate
and recheck the candidate at commit time.

### Expose commit tools unconditionally after upgrade

Rejected. Existing installations currently get a read-only MCP surface. A
write-capable profile should be consciously enabled because annotations do not
enforce permissions.

### Omit `duckdb_export` because it lacks dry-run

Rejected. Preview/write pairing is required when the service has two effect
modes, not as a universal prerequisite for all mutations. `duckdb` is already
a direct artifact-producing command with explicit overwrite semantics.

### Block this RFC on TypeScript parity

Rejected. It would make every Python write capability wait for a second
implementation that does not otherwise exist. Shared observable semantics are
required when a capability exists; simultaneous implementation is not.

## Compatibility

The default MCP profile remains backward-compatible: the same five tools are
visible and no write authority appears merely because the package was
upgraded.

The only compatibility-sensitive change is conceptual: documentation and
server instructions stop promising that MCP is read-only **by design** and
instead describe read-only as the default profile.

Enabling `--allow-write` is additive. Existing tool names keep their current
meaning. No existing read tool becomes mutating.

## Implementation plan

A minimal implementation can land independently of RFC 0006's remaining type
work because it only adapts service functions that already exist:

1. annotate the existing read tools explicitly;
2. introduce a server-construction/profile mechanism and `serve --allow-write`;
3. add `apply_preview`/`apply_write`, `init_preview`/`init_write`,
   `import_preview`/`import_write`, `format_write`, and `duckdb_export` as thin
   adapters;
4. test tool visibility and annotations through MCP `tools/list` in both
   profiles;
5. test each preview/write pair against its direct service function for payload
   equivalence;
6. update `docs/cli.md` and server instructions to describe the two profiles.

No change to RFC 0005 or RFC 0006 is required. Typed columns, when RFC 0006
reaches `apply` and `duckdb`, flow through these wrappers automatically.

## Acceptance criteria

This RFC can move from `proposed` to `accepted` when the project agrees on the
following observable contract:

- MCP is not globally read-only;
- commit-capable tools are opt-in at server startup;
- preview and write are separate MCP tools where one service supports both;
- every exposed tool has reviewed standard MCP effect annotations;
- annotations are treated as hints, not authorization;
- write tools reuse existing service functions rather than duplicating write
  logic;
- the default `okf-parser-mcp` entry point remains read-only;
- Python implementation does not wait for TypeScript write parity.

Implementation completion is separate from acceptance, as with the other RFCs.
