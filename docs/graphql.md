---
type: Documentation
title: Embedded GraphQL read adapter
description: Deterministic SDL and host-owned read-only GraphQL execution over OKF TypeContract and Ibis relations
---

# Embedded GraphQL read adapter

GraphQL is an optional **read adapter** over the same OKF semantics used by the
parser, TypeContract, RFC 0006 typed relations and DuckDB/Ibis surfaces. It is not
another schema implementation and it does not own Markdown parsing or writes.

## Deterministic SDL

SDL generation has no GraphQL runtime dependency:

```bash
okf-parser schema path/to/bundle --format graphql > schema.graphql
```

The generated schema contains the generic `Concept` interface, producer-defined
concept object types, forward/reverse links, diagnostics and deterministic scalar
policies. Authored names that are not valid GraphQL identifiers receive stable
aliases with `@okfType` / `@okfField` provenance directives. Distinct authored
paths that would collapse to one GraphQL name fail explicitly instead of silently
sharing an alias.

`--infer-types`, `--cast` and `--spec-template` retain the same meaning they have
for the other schema exporters. In particular, `--spec-template` may execute
trusted sibling `.schema.sql` declarations; it is operator configuration, not a
request-controlled switch.

## Embedded executable schema

Install the optional runtime extra when execution is needed:

```bash
python -m pip install 'okf-parser[graphql]'
```

Then embed the adapter in the host process:

```python
from okf_parser import GraphQLReadAdapter

adapter = GraphQLReadAdapter(
    "path/to/bundle",
    spec_template="types/{slug}.md",
)

result = adapter.execute(
    """
    query($type: String!) {
      concepts(type: $type, first: 50, offset: 0) {
        id
        path
        title
        links { targetId exists }
        reverseLinks { sourceId }
      }
    }
    """,
    {"type": "Note"},
)

if result.errors:
    raise RuntimeError(result.errors)
print(result.data)
```

`build_graphql_schema(...)` exposes the underlying `GraphQLSchema` when a host
wants to provide its own execution or transport layer. okf-parser deliberately
does **not** start a GraphQL HTTP server.

## Query contract

The first executable slice exposes:

```graphql
type Query {
  concept(id: ID!): Concept
  concepts(type: String, first: Int = 50, offset: Int = 0): [Concept!]!
}
```

`concepts` is ordered by canonical `concept_id`; `first` is bounded to `1..1000`
and `offset` must be non-negative. This keeps pagination deterministic and
bounded. General typed field filtering is intentionally a later #56 milestone;
the adapter does not interpolate arbitrary GraphQL input into SQL.

The adapter reads generic concept identity/source data from canonical Ibis
relations. When `spec_template` is supplied, declared RFC 0006 values are read
through the public `Bundle.compile_types()` API rather than private DuckDB tables.

## Scalar policy

GraphQL must not flatten physical types merely because JSON can carry a similar
shape:

- safe 32-bit integer declarations use GraphQL `Int`;
- wider integer declarations use `BigInt` and serialize as decimal strings;
- `DECIMAL` uses `Decimal` and serializes as an exact decimal string;
- `DATE` uses `Date` and serializes as `YYYY-MM-DD` even if the execution backend
  represents it internally at midnight;
- timestamps use `DateTime`;
- UUID declarations use `UUID`;
- lists preserve their item scalar contract;
- objects, unknown values and structured frontmatter use `JSON`.

These names are explicit custom scalars in the emitted SDL. Hosts may attach
stricter scalar implementations when integrating the returned `GraphQLSchema`.

## Astro / server-rendered applications

A browser does not need to know that GraphQL exists. A typical composition is:

```text
Browser
  -> Astro SSR / Actions
  -> application gateway
  -> GraphQLReadAdapter
  -> OKF Bundle / TypeContract / Ibis
```

The host can create a fresh adapter for each live read when filesystem changes
must be visible immediately, or manage snapshot lifetime explicitly when a stable
read view is desired. Do not cache one adapter indefinitely and then claim live
filesystem semantics.

## Node tooling

Node consumers can use the deterministic SDL without acquiring Python server
semantics. For example, after generating `schema.graphql`, ordinary GraphQL
libraries can parse or validate the schema for code generation:

```javascript
import { readFileSync } from "node:fs";
import { buildSchema, printSchema } from "graphql";

const schema = buildSchema(readFileSync("schema.graphql", "utf8"));
console.log(printSchema(schema));
```

That is schema/tooling consumption only. Canonical OKF read resolvers remain the
Python adapter in this slice; a Node host must not independently reparse Markdown
or recreate TypeContract semantics.

## Read-only boundary

There is no GraphQL `Mutation` type. Filesystem-changing operations continue
through the existing preview/commit service APIs (`apply`, editor writes, import,
init and formatting) with their existing effect and authorization contracts.
Adding generic GraphQL mutations would bypass those invariants and is explicitly
outside this adapter.
