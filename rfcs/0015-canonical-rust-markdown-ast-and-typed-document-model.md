---
type: RFC
title: Canonical Rust Markdown AST and typed document model
description: Make the Rust engine the canonical Markdown parser, project its AST through a stable OKF-owned wire model, and expose typed language models without coupling public APIs to markdown-rs
status: draft
rfc_status: proposed
---

# RFC 0015: Canonical Rust Markdown AST and typed document model

## Summary

`okf-parser` shall have one authoritative interpretation of Markdown.

Markdown syntax is parsed in `okf-engine` with `markdown-rs`. Its native
`mdast::Node` tree is private implementation detail. The engine projects that
tree into a smaller, versioned, OKF-owned document model consumed by Python,
TypeScript, CLI and MCP surfaces.

Python represents cross-process document data with frozen Pydantic v2 models.
TypeScript consumes the same wire contract. Producer-authored frontmatter
continues to use RFC 0001's generated Pydantic models and remains a separate
schema family.

The architecture is:

```text
raw UTF-8 source
      │
      ├─ source_digest                exact authored snapshot
      │
      ▼
normalization + SourceMap
      │
      ├─ normalized_source_digest     AST coordinate snapshot
      │
      ▼
markdown-rs / mdast
      │
      ▼
OKF document IR
      │
      ├─ validation
      ├─ headings / links / sections
      ├─ retrieval
      ├─ formatting equivalence
      └─ structural editing
      │
      ▼
versioned JSON wire contract
      │
      ├─ frozen Pydantic models
      └─ TypeScript models/validators
```

The central boundary is:

> Rust owns Markdown syntax. OKF owns the document protocol. Language bindings
> validate that OKF-owned protocol rather than mirroring the parser crate.

The AST exists to make structure reusable. It is not a reason to serialize the
whole tree for every operation or to send AST JSON to agents.

## Motivation

### There are currently three Markdown interpretations

Today Markdown semantics are reconstructed independently in three places:

- Python through `markdown-it-py` / `mdformat` and `markdown_facts()`;
- Rust through `pulldown-cmark` event streams;
- TypeScript through its own `markdownFacts` implementation.

That creates multiple possible answers to basic structural questions: what a
heading contains, which links exist, where a section ends, how GFM tables are
represented, and whether a formatter preserved semantics.

This duplication is tolerable for headings and links. It is not a viable basis
for structural retrieval, source provenance and safe editing.

### Tree operations need a tree

The native engine is moving beyond one-pass fact extraction. It needs to support
operations such as:

- sections and subsections;
- nested lists and task items;
- GFM tables with alignment;
- reference links and definitions;
- source spans;
- structural fingerprints;
- smallest-complete-unit retrieval;
- guarded source edits.

Those are naturally tree operations. The engine should parse once and derive
all structural views from one tree.

### Agent context should use semantic units

The useful retrieval unit is usually not an arbitrary byte or character window.
For a query about installation, a complete section is preferable to either the
whole file or a syntactically incomplete chunk.

The AST therefore serves retrieval selection, while the returned agent context
normally remains ordinary Markdown. Internal structural richness must not turn
into unnecessary context tokens.

### Source spans must be correct on real files

OKF files may contain a UTF-8 BOM, CRLF, LF or solitary CR line endings. Existing
parsing normalizes some of those forms before semantic parsing, while
`source_digest` intentionally identifies the authored source rather than the
normalized representation.

A structural editing design must therefore distinguish:

1. the exact authored snapshot;
2. the normalized parser snapshot;
3. the mapping between their byte coordinates.

Conflating those three would make spans unsafe on BOM/CRLF documents.

# Decision

## 1. `okf-engine` is the authoritative Markdown parser

All normative Markdown interpretation originates in `okf-engine`.

The initial implementation uses `markdown-rs` and `to_mdast()` with an explicit
OKF-owned dialect called `okf-gfm-v1`.

Conceptually:

```rust
let mut constructs = markdown::Constructs::gfm();
constructs.frontmatter = true;
constructs.gfm_footnote_definition = true;
constructs.gfm_label_start_footnote = true;

let options = markdown::ParseOptions {
    constructs,
    ..markdown::ParseOptions::gfm()
};
```

`okf-gfm-v1` is closed and includes:

- CommonMark;
- GFM autolinks;
- GFM strikethrough;
- GFM tables;
- GFM task lists;
- GFM footnotes;
- YAML frontmatter.

It excludes MDX and any extension not explicitly listed above.

Changing parser dependencies without changing observable syntax does not change
the dialect identifier. Changing accepted syntax does.

## 2. `mdast` is private

`markdown::mdast::Node` is not an OKF API.

`okf-engine` may use native node types and may enable the crate's `serde` support
for tests or internal diagnostics, but raw serialized `mdast` must not cross a
supported language boundary.

The stable path is:

```text
mdast::Node
    ↓ explicit conversion
OkfMarkdownNode
    ↓ versioned JSON
Pydantic / TypeScript models
```

This allows `markdown-rs` to evolve without making dependency-specific node names
or fields part of the public contract.

## 3. Raw and normalized source snapshots are distinct

The engine parses a normalized snapshot, but preserves the identity of the
exact authored snapshot.

The normalization for `okf-gfm-v1` is deterministic:

1. decode as UTF-8, failing otherwise;
2. remove one leading UTF-8 BOM from the parser snapshot if present;
3. normalize CRLF to LF;
4. normalize remaining solitary CR to LF;
5. make no other textual rewrite before Markdown parsing.

Two digests identify the two coordinate spaces:

```text
source_digest
    sha256 of the exact authored UTF-8 source, preserving BOM and newline spelling

normalized_source_digest
    sha256 of the normalized UTF-8 parser snapshot defined above
```

`source_digest` keeps its existing meaning. `parsed_digest` also keeps its
existing meaning. `normalized_source_digest` is new and exists specifically to
identify the source representation to which AST spans refer.

The engine constructs an internal `SourceMap` during normalization. The map
converts every boundary in normalized UTF-8 byte space back to the corresponding
boundary in the exact authored UTF-8 byte space.

`SourceMap` is an engine implementation detail. It is not serialized in the
ordinary wire model.

This resolves the apparent tension between canonical parsing and lossless edits:

```text
raw source bytes
      │
      ├─ source_digest
      │
      ▼
normalize + SourceMap
      │
      ├─ normalized_source_digest
      ▼
AST span in normalized byte coordinates
      │
      ▼ SourceMap
raw byte range for edit
```

## 4. Frontmatter is consumed separately from the public Markdown tree

Because `okf-gfm-v1` enables frontmatter, `markdown-rs` may emit a leading
YAML/frontmatter node.

The OKF projection explicitly recognizes that leading node, records its span,
and removes it from the public Markdown body tree.

The public model represents frontmatter through:

- `frontmatter_span`;
- the validated semantic mapping on the parsed OKF document;
- the exact source snapshot when authored YAML text is needed.

There is no public `YamlNode` or `FrontmatterNode` in schema version 1.

A YAML/frontmatter node in any position other than the permitted leading
frontmatter position is not silently exposed as an unknown node. It is treated
according to Markdown syntax at that position or rejected as an internal
projection inconsistency, depending on what `markdown-rs` actually emitted.

A body line containing `---` is therefore not consumed merely because it looks
like a frontmatter delimiter. Only the parser-recognized leading frontmatter node
is removed.

## 5. OKF owns a versioned Rust document model

Representative Rust types are:

```rust
pub struct SourcePoint {
    pub line: u32,
    pub column: u32,
    pub byte_offset: u64,
}

pub struct SourceSpan {
    pub start: SourcePoint,
    pub end: SourcePoint,
}

pub struct NodeSelector {
    pub path: String,
    pub source_digest: String,
    pub normalized_source_digest: String,
    pub parsed_digest: String,
    pub node_path: Vec<u32>,
}

pub struct MarkdownDocument {
    pub schema_version: u32,
    pub dialect: String,
    pub source_digest: String,
    pub normalized_source_digest: String,
    pub parsed_digest: String,
    pub frontmatter_span: Option<SourceSpan>,
    pub body_span: SourceSpan,
    pub root: RootNode,
}
```

The concrete Rust implementation uses enums and typed fields, not generic
`serde_json::Value` payloads.

`schema_version` initially equals `1`.

`NodeSelector` is the cross-boundary selector type; there is no separate dead
`NodeRef` abstraction.

## 6. Source spans use normalized UTF-8 coordinates

`SourcePoint.byte_offset` is zero-based and refers to UTF-8 bytes in the
normalized parser snapshot identified by `normalized_source_digest`.

`line` and `column` are one-based in that same normalized snapshot.

Spans are half-open:

```text
[start, end)
```

A convenience extractor must therefore receive normalized source, not arbitrary
raw source:

```python
def extract_normalized(self, normalized_source: str) -> str:
    encoded = normalized_source.encode("utf-8")
    return encoded[self.start.byte_offset : self.end.byte_offset].decode("utf-8")
```

Code that needs to mutate the authored file does not apply normalized offsets to
raw bytes directly. It asks the engine to map the normalized span through the
`SourceMap` created from the exact `source_digest` snapshot.

This rule is normative. A binding must not guess CRLF/BOM corrections itself.

## 7. The root is a container, not a recursive Markdown node

`RootNode` is not a member of the recursive `MarkdownNode` union.

The public shape is conceptually:

```python
class RootNode(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["root"]
    children: tuple["MarkdownNode", ...]


MarkdownNode = Annotated[
    ParagraphNode
    | HeadingNode
    | TextNode
    | EmphasisNode
    | StrongNode
    | DeleteNode
    | BlockquoteNode
    | ListNode
    | ListItemNode
    | LinkNode
    | LinkReferenceNode
    | ImageNode
    | ImageReferenceNode
    | CodeNode
    | InlineCodeNode
    | HtmlNode
    | BreakNode
    | ThematicBreakNode
    | TableNode
    | TableRowNode
    | TableCellNode
    | DefinitionNode
    | FootnoteDefinitionNode
    | FootnoteReferenceNode,
    Field(discriminator="kind"),
]
```

`RootNode.children` contains `MarkdownNode`; a root cannot recursively appear
inside another node.

The root has no independent `span` field. `MarkdownDocument.body_span` is the
single authoritative span for the public body root. The root's implicit
structural path is empty and it is not itself a selectable edit node.

All selectable child nodes carry `node_path` and, when available, `span`.

## 8. Pydantic models are frozen and closed

Parser-protocol models use:

```python
ConfigDict(frozen=True, extra="forbid")
```

Unexpected fields are protocol drift and must fail for a fixed schema version.

This deliberately differs from RFC 0001 generated producer models, which use
`extra="allow"` because OKF frontmatter may contain producer-defined fields.

The distinction is:

```text
producer metadata       parser protocol
-----------------       ---------------
open                    closed per version
extra="allow"           extra="forbid"
authored by producer    emitted by okf-engine
RFC 0001                RFC 0015
```

`kind` is not declared as `str` on a shared base model. Each concrete node
variant owns its `Literal[...]` discriminator.

Representative variants include:

```python
class NodeBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    node_path: tuple[int, ...]
    span: SourceSpan | None


class HeadingNode(NodeBase):
    kind: Literal["heading"]
    level: Literal[1, 2, 3, 4, 5, 6]
    children: tuple["MarkdownNode", ...]


class TableNode(NodeBase):
    kind: Literal["table"]
    align: tuple[Literal["left", "right", "center"] | None, ...]
    children: tuple["TableRowNode", ...]
```

GFM table alignment is semantic and must survive projection and structural
comparison.

A package-level closed alias, `MarkdownNodeKind`, enumerates every non-root node
kind in the current schema version. Derived models use that alias instead of
free-form `str` kinds.

## 9. `MarkdownDocument` identifies both source snapshots

The Python boundary model is conceptually:

```python
class MarkdownDocument(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1]
    dialect: Literal["okf-gfm-v1"]
    source_digest: str
    normalized_source_digest: str
    parsed_digest: str
    frontmatter_span: SourceSpan | None
    body_span: SourceSpan
    root: RootNode
```

`frontmatter_span`, `body_span` and all node spans live in normalized coordinates.
`source_digest` identifies the authored file used to create the normalization
map. `normalized_source_digest` identifies the coordinate snapshot itself.

## 10. Existing `ParsedDocument` stays lightweight

Demand-driven serialization applies to document parsing as well as bundle
loading.

The existing lightweight `ParsedDocument` remains the semantic frontmatter/body
record and does **not** acquire a mandatory full `MarkdownDocument` field merely
because the engine internally built an AST.

A caller that needs the public AST asks for it explicitly and receives a
separate envelope, for example:

```python
class DocumentInspection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: Path
    frontmatter: dict[str, YamlValue]
    body: str
    source_digest: str
    normalized_source_digest: str
    parsed_digest: str
    markdown: MarkdownDocument
```

This keeps ordinary frontmatter parsing cheap at the language boundary while
still allowing the native engine to parse once internally when an operation
needs structural facts.

The implementation may offer `parse_document(..., include_markdown=True)` or a
separate `inspect_document()` entry point. The public contract is that the full
AST is opt-in, not that a specific convenience spelling is required.

## 11. RFC 0001 generated models remain metadata models

RFC 0001 continues to generate Pydantic models from `TypeContract` for
producer-authored frontmatter.

For example, a generated `RegistroConcept` validates authored metadata. It does
not gain `children`, `span`, `kind` or parser-specific fields.

Syntax and metadata compose explicitly:

```python
MetadataT = TypeVar("MetadataT", bound=BaseModel)

class TypedConceptDocument(BaseModel, Generic[MetadataT]):
    model_config = ConfigDict(frozen=True)

    path: Path
    concept_id: str
    frontmatter: MetadataT
    markdown: MarkdownDocument
    source_digest: str
    normalized_source_digest: str
    parsed_digest: str
```

`okf-parser schema . --format pydantic` continues to emit producer models only.
It does not duplicate the AST protocol into generated modules.

## 12. `ConceptRecord` and `Bundle` keep their current roles

`ConceptRecord` remains a compact relational projection. Full AST JSON is not
added as an Ibis/DuckDB column by default.

`Bundle` remains a non-Pydantic runtime object because it contains live Ibis
expressions rather than boundary data.

The rule stays:

```text
serializable boundary data  → Pydantic / typed wire models
live query/runtime objects   → ordinary runtime types
```

## 13. Derived headings and links carry provenance

AST-backed structural facts become richer than the current tuple-only facts.
Representative Pydantic models are:

```python
class HeadingFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    level: Literal[1, 2, 3, 4, 5, 6]
    source_text: str
    plain_text: str
    node_path: tuple[int, ...]
    span: SourceSpan


class MarkdownLinkFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    raw_target: str
    title: str | None
    node_path: tuple[int, ...]
    span: SourceSpan
```

Reference links are resolved through their definitions when producing semantic
link facts. The raw AST can preserve authored reference structure; the derived
`LinkRecord` continues to expose the useful resolved target.

## 14. Sections are document-level, root-child structures

`MarkdownSection` is intentionally defined only from `HeadingNode` values that
are direct children of the public body root.

A document-level section starts at one root-child heading and extends through
following root-child siblings until the next root-child heading whose `level` is
less than or equal to the starting heading's level, or until the end of the
body.

Headings nested inside blockquotes, list items or other containers do not start
or terminate document-level sections. They remain ordinary nodes within the
containing subtree and may be addressed directly for lower-level operations.

Representative model:

```python
class MarkdownSection(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    source_title: str
    plain_title: str
    level: Literal[1, 2, 3, 4, 5, 6]
    heading_path: tuple[int, ...]
    span: SourceSpan
    content_paths: tuple[tuple[int, ...], ...]
```

The separate source/plain titles preserve the same distinction used by
`HeadingFact`.

## 15. Retrieval returns semantic fragments, not full ASTs by default

MCP and agent-facing retrieval should normally return the smallest complete
useful Markdown unit rather than the entire syntax tree.

Representative fragment model:

```python
class DocumentFragment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    source_digest: str
    normalized_source_digest: str
    parsed_digest: str
    node_path: tuple[int, ...]
    kind: MarkdownNodeKind
    span: SourceSpan
    markdown: str
```

The engine may use AST structure to select the fragment, then render or extract
the corresponding original Markdown for the agent.

The desired flow is:

```text
query
  ↓
search/index
  ↓
AST structural selection
  ↓
smallest complete useful subtree/section
  ↓
Markdown fragment
  ↓
agent context
```

## 16. Formatting is judged by the canonical Rust parser

This RFC does not require replacing `mdformat` immediately.

During migration, `mdformat` may continue producing candidate Markdown. Semantic
preservation is eventually judged by an AST-derived Rust structural fingerprint:

```text
source → Rust structural fingerprint
       → mdformat candidate
       → Rust structural fingerprint
       → compare
```

The fingerprint includes every semantically relevant field in the OKF
projection, including GFM table alignment.

The existing Python structural signature can be removed only after the Rust
fingerprint covers the same protected semantics.

## 17. Existing digests keep their meaning

`source_digest` and `parsed_digest` are not redefined by this RFC.

`normalized_source_digest` is additive and identifies the normalized parser
snapshot used for source coordinates.

A future syntax-equivalence digest may still be introduced separately:

```text
syntax_digest = okf-syntax-v1-jcs-sha256:<digest>
```

Such a digest would exclude spans, node paths and insignificant formatting. It
must not be confused with either exact source identity or normalized coordinate
identity.

## 18. Structural edits are snapshot- and coordinate-guarded

A Python selector mirrors the Rust selector:

```python
class NodeSelector(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    source_digest: str
    normalized_source_digest: str
    parsed_digest: str
    node_path: tuple[int, ...]
```

An edit operation:

1. reads the exact current source;
2. verifies `source_digest`;
3. normalizes it using the normative normalization algorithm while building a
   `SourceMap`;
4. verifies `normalized_source_digest`;
5. parses the normalized snapshot with the canonical parser;
6. optionally verifies `parsed_digest` as semantic identity;
7. resolves `node_path` in the fresh tree;
8. obtains the node's normalized `SourceSpan`;
9. maps that span through `SourceMap` to an authored-source byte range;
10. creates a candidate edit without changing unrelated BOM/newline spelling;
11. reparses and validates the candidate;
12. returns preview or commits through the existing guarded write path.

Bindings never perform ad-hoc BOM or CRLF arithmetic.

All writes remain subject to RFC 0005 and RFC 0008 safety semantics where
applicable.

# Wire protocol

## 19. Rust owns serialization; bindings validate it

A schema-version-1 document payload is conceptually:

```json
{
  "schema_version": 1,
  "dialect": "okf-gfm-v1",
  "source_digest": "sha256:raw...",
  "normalized_source_digest": "sha256:normalized...",
  "parsed_digest": "okf-parsed-v1-jcs-sha256:...",
  "frontmatter_span": {
    "start": {"line": 1, "column": 1, "byte_offset": 0},
    "end": {"line": 5, "column": 1, "byte_offset": 64}
  },
  "body_span": {
    "start": {"line": 5, "column": 1, "byte_offset": 64},
    "end": {"line": 14, "column": 1, "byte_offset": 231}
  },
  "root": {
    "kind": "root",
    "children": []
  }
}
```

`body_span` is the sole authoritative span of the body root. `root` does not
repeat it.

Python validates the payload with `MarkdownDocument.model_validate(payload)`.
TypeScript validates the same OKF-owned shape rather than reparsing Markdown.

## 20. Wire-version negotiation is only for independently selected engines

The normal packaged topology is lockstep: Python wheels, the native executable
and npm platform packages are released with the same project version. That path
does not need a second serializer merely to protect against a combination the
package manager normally cannot create.

Wire negotiation applies when a caller intentionally selects an engine outside
its packaged release, for example through an explicit native executable path,
`OKF_CORE`, development checkout or future remote/native deployment.

The policy is:

1. each binding declares the wire versions it accepts;
2. each engine declares the wire versions it can emit;
3. an external-engine invocation negotiates the highest mutually supported
   version before document data is returned;
4. no binding accepts unknown fields from a newer fixed schema by relying on
   `extra="ignore"`;
5. adding/removing fields, changing requiredness or meaning, or adding node kinds
   requires a new `schema_version`;
6. when schema version N+1 is introduced and independently selected N engines or
   bindings remain a supported deployment, the newer side retains N support for
   one minor-release compatibility window;
7. the ordinary lockstep packaged path may emit only its current schema version;
8. schema version 1 introduces no obligation to implement a nonexistent version
   0 serializer.

If the project later removes external-engine substitution entirely, the
compatibility window may be removed in the RFC that makes that deployment
change.

## 21. TypeScript consumes the same engine contract

The current TypeScript `markdownFacts` implementation is transitional.

After this RFC is implemented, TypeScript obtains normative headings, links,
sections and other structural facts from the canonical Rust engine through one
of these transports:

1. the packaged native engine protocol; or
2. a future native/WASM binding produced from the same Rust implementation.

TypeScript must not retain an independent normative Markdown parser. During
migration its existing implementation may remain only as a parity oracle.

# Performance

## 22. Parse once per document per engine operation

Within one engine operation, Markdown is parsed no more than once unless a
mutation creates a candidate document that itself must be parsed.

Bundle loading derives metadata, headings, links, validation facts and any
required section index from one tree, then drops that tree unless the operation
needs it longer.

## 23. Full AST serialization is demand-driven

The native engine may build an AST internally without serializing it across the
language boundary.

The intended projections are:

```text
bundle load
→ records + links + diagnostics

lightweight document parse
→ ParsedDocument semantics, no AST wire payload

document inspection
→ DocumentInspection + MarkdownDocument

retrieval
→ DocumentFragment[]

structural edit preview
→ selectors + candidate diff

explicit AST inspection
→ MarkdownDocument
```

A 10,000-document bundle must not send 10,000 AST JSON documents to Python or
TypeScript merely to build relational views.

## 24. AST migration has a mechanical performance gate

The `pulldown-cmark` path is removed only after an explicit, checked-in baseline
and candidate comparison.

Before the first code-changing AST migration commit, the implementation work
must extend `benchmarks/python_parser.py` so that its native bundle measurement
also records peak resident memory for the native engine process. The benchmark
report records at least:

- git commit SHA;
- OS and architecture;
- CPU identifier when available;
- Python version;
- Rust toolchain version;
- benchmark arguments;
- native `bundle load` median wall time;
- native peak resident memory.

The baseline is generated from the `main` commit that is the parent of the first
AST implementation commit, while it still uses `pulldown-cmark`:

```bash
cargo build --release --locked --bin okf-parser

uv run benchmarks/python_parser.py \
  --sizes 1000,5000,10000 \
  --body-paragraphs 4 \
  --rounds 7 \
  --read-concurrencies 32 \
  --rust-core target/release/okf-parser \
  > benchmarks/baselines/rfc-0009-pulldown-cmark.json
```

That JSON file is committed before the event-stream parser is removed. Its
`baseline_commit` field names the exact commit used to build the baseline.

The candidate uses the same command, machine class, release build profile and
benchmark arguments. A small checker, for example
`benchmarks/check_rfc0009_regression.py`, compares candidate results to the
checked-in baseline mechanically.

For every reported bundle size, and especially 10,000 documents:

- median native bundle-load wall-time regression must be no worse than **20%**;
- peak resident-memory regression must be no worse than **35%**;
- the bundle load must not serialize full ASTs across the process boundary.

If either threshold fails, the migration does not remove `pulldown-cmark` until
the AST path is optimized or a separate RFC changes the budget.

These are migration gates, not eternal project-wide SLAs.

# Conformance

## 25. Parser parity is fixture-driven

Before Python, Rust event-stream or TypeScript compatibility implementations are
removed, the shared conformance corpus covers at least:

- ATX and setext headings;
- emphasis and links inside headings;
- inline, reference, collapsed and shortcut links;
- autolinks;
- links containing fragments, queries and parentheses;
- escaped Markdown;
- images versus ordinary links;
- nested ordered/unordered/task lists;
- ordered-list starts;
- blockquotes;
- blockquote → list → nested code combinations;
- fenced and indented code;
- tabs versus spaces in indentation-sensitive constructs;
- GFM tables;
- escaped pipes inside tables;
- left/right/center/unspecified table alignment;
- raw HTML;
- thematic breaks;
- footnote definitions and references;
- Unicode;
- UTF-8 BOM;
- LF;
- CRLF;
- solitary CR;
- a document with no final newline;
- valid YAML frontmatter;
- empty frontmatter;
- malformed frontmatter delimiters;
- a body whose first post-frontmatter construct is `---`;
- `---` occurring later in the body where it is not frontmatter;
- root and nested `index.md`;
- `log.md`.

The corpus tests more than semantic acceptance. For every case with source
positions it also verifies:

1. every public span is within the normalized UTF-8 snapshot;
2. slicing normalized bytes by the span yields the expected source construct;
3. `normalized_source_digest` matches exactly that normalized snapshot;
4. `SourceMap` maps normalized span boundaries back to the correct authored
   source boundaries;
5. BOM/CRLF/CR inputs round-trip structural edits without shifting unrelated
   bytes or changing newline style;
6. only a parser-recognized leading frontmatter node is consumed.

Parser parity compares semantic projections, not dependency-specific token
streams.

## 26. Wire conformance is tested independently

For every exported non-root node kind:

1. Rust creates a canonical payload;
2. JSON serialization succeeds;
3. the matching Pydantic model accepts it;
4. the matching TypeScript validator accepts it;
5. malformed or unknown fields fail for a fixed schema version;
6. unknown node kinds fail for schema version 1;
7. `DocumentFragment.kind` rejects values outside `MarkdownNodeKind`;
8. external-engine version negotiation is tested when more than one wire version
   actually exists.

`RootNode` receives its own tests and is explicitly excluded from the recursive
`MarkdownNode` union.

# Compatibility

## 27. Existing high-level behavior is preserved during migration

The migration preserves:

- current `source_digest` semantics;
- current `parsed_digest` semantics;
- frontmatter scalar spelling;
- concept IDs;
- link resolution;
- reserved-document validation;
- heading-based log validation;
- relational schemas unless separately changed;
- RFC 0001 Pydantic generation;
- current public CLI behavior unless separately extended.

`normalized_source_digest` and the AST wire model are additive new concepts.

Parser replacement is not permission to silently change OKF semantics. Any
intentional semantic difference requires a documented decision and fixtures.

# Cross-PR acceptance gates

The implementation of this RFC must converge with adjacent work rather than
reintroducing costs or ownership that those changes deliberately removed. The
following open pull requests are coordination points, not runtime dependencies.

## Ordered-frontmatter fast path (#182)

If the ordered-frontmatter optimization from #182 lands, the canonical AST
implementation must preserve its transparent fast path for the deliberately
simple authored subset (`type`, `title`, `description`, then remaining simple
ASCII keys in ordinal lexical order). The AST/frontmatter integration must not
force every eligible document back through the generic YAML path merely because
Markdown now has a canonical tree. Complex or out-of-order YAML continues to
fall back without changing semantics.

The same-host baseline recorded by #182 -- roughly 18--21% lower end-to-end
load latency for 1k--50k simple-frontmatter documents -- is part of the
performance evidence considered by the migration gate in this RFC.

## No-copy/newline fast path (#183)

If #183 lands, normalization plus `SourceMap` must preserve its allocation
behavior: UTF-8/LF-only source without BOM must be representable by an identity
map and a borrowed parser snapshot. A normalized copy is required only when the
source actually needs BOM removal or CR/CRLF translation. Building source
provenance is not permission to reintroduce an unconditional whole-document
allocation.

The same-host #183 measurements (about 3.5--8% lower cold-load latency across
the measured source-size matrix) are part of the regression baseline.

## Structural retrieval (#189)

The search/retrieval work in #189 should consume the OKF-owned structural IR
defined here for passage selection. Its agent-facing result remains compact
Markdown evidence (for example a complete section, list or table fragment), not
serialized AST JSON. Retrieval profiles may index derived structural units, but
those indexes never become another Markdown authority.

## Git/provenance adapter (#104)

The Git adapter in #104 remains authoritative for Git objects, commit envelopes
and Git provenance. This RFC owns Markdown-body syntax and source coordinates
only. A Git-backed document may use this parser for its Markdown body, but the
Markdown IR must not absorb commit-message grammar, refs, parentage or other
Git-specific semantics.

# Migration plan

## Phase 0 — Freeze benchmarks and offset fixtures

Before changing the native parser:

1. extend the benchmark harness with peak RSS and metadata required by §24;
2. commit `benchmarks/baselines/rfc-0009-pulldown-cmark.json` from the exact
   pre-migration `main` commit;
3. add BOM/LF/CRLF/CR and span/source-map fixtures from §25.

## Phase 1 — Add normalization, `SourceMap` and canonical Rust parsing

Add a focused Rust Markdown module that owns:

- normative normalization;
- `SourceMap` construction;
- `normalized_source_digest`;
- `markdown-rs` dialect configuration;
- `to_mdast()`;
- source positions;
- frontmatter-node consumption;
- text extraction;
- headings, links and reference resolution;
- GFM table alignment.

`pulldown-cmark`, Python `markdown_facts()` and TypeScript `markdownFacts` remain
as parity oracles.

## Phase 2 — Introduce OKF-owned Rust node and wire types

Add schema version 1, `RootNode`, the non-root `MarkdownNode` enum,
`MarkdownNodeKind`, `NodeSelector`, source types and explicit `mdast` conversion.

No version-0 serializer is required.

Unsupported native node variants do not silently become generic `unknown` wire
nodes.

## Phase 3 — Introduce Pydantic and TypeScript document models

Add frozen closed models for:

- `SourcePoint` and `SourceSpan`;
- `RootNode` and all non-root node variants;
- `MarkdownNodeKind`;
- `MarkdownDocument`;
- `NodeSelector`;
- heading/link facts;
- `MarkdownSection`;
- `DocumentFragment`;
- optional `DocumentInspection` / typed composition helpers.

Generated RFC 0001 producer models remain unchanged in purpose.

## Phase 4 — Validate the native boundary

Add an internal engine operation such as `__engine-parse` for explicit AST
inspection and binding tests.

The operation validates/negotiates wire versions only when the caller selects an
independent engine. Packaged lockstep invocations use the current schema.

## Phase 5 — Replace Rust event-stream facts

Move headings, links and reserved-document structural validation to the AST.

Remove `pulldown-cmark` only after both:

- the shared conformance corpus passes;
- the §24 performance checker passes against the committed baseline.

## Phase 6 — Remove Python semantic Markdown parsing

Replace Python `markdown_facts()` and other normative parsing with engine-derived
facts. `markdown-it-py` may remain only where `mdformat` requires it internally.

## Phase 7 — Remove TypeScript semantic Markdown parsing

Replace TypeScript `markdownFacts` with the canonical engine contract or a
native/WASM binding to the same Rust implementation.

## Phase 8 — Move formatting equivalence to Rust

Replace the Python `protected_block_signature()` authority with the AST-derived
Rust structural fingerprint, including table alignment.

`mdformat` may continue proposing formatted text; Rust decides whether semantics
were preserved.

## Phase 9 — Build AST-native retrieval and editing

Retrieval and mutation may then consume sections, subtrees, selectors and spans
through the OKF-owned abstraction rather than direct `markdown-rs` types.

# Non-goals

This RFC does not:

1. expose raw `markdown-rs` types through Python or TypeScript;
2. make AST JSON a default DuckDB/Ibis column;
3. replace RFC 0001 generated frontmatter models;
4. make `Bundle` a Pydantic model;
5. require agents to consume AST JSON;
6. define a visual Markdown editor;
7. introduce MDX or unlisted Markdown extensions;
8. replace `mdformat` in the first implementation;
9. redefine `source_digest` or `parsed_digest`;
10. make node paths persistent across document snapshots;
11. move producer schemas into the syntax tree;
12. require dual wire serializers on the ordinary lockstep packaged path.

# Invariants

After this RFC is implemented:

1. **One authoritative parser.** Normative Markdown interpretation originates in
   `okf-engine` for native, Python and TypeScript consumers.
2. **Dependency ASTs stay private.** `mdast::Node` never becomes the OKF wire
   contract.
3. **Raw and normalized source identities are distinct.** `source_digest`
   identifies authored source; `normalized_source_digest` identifies AST
   coordinates.
4. **Offsets are mapped, never guessed.** Structural edits convert normalized
   spans through the engine-owned `SourceMap` before touching authored bytes.
5. **Frontmatter is projected separately.** Only the parser-recognized leading
   frontmatter node is removed from the public body tree.
6. **Root is not recursive.** `RootNode` is outside `MarkdownNode`, and
   `body_span` is the sole body-root span.
7. **Protocol models are closed.** Pydantic and TypeScript reject unknown fields
   and node kinds for a fixed schema version.
8. **Producer models stay open.** RFC 0001 continues to model producer
   frontmatter independently from Markdown syntax.
9. **Table alignment is semantic.** GFM alignment survives projection and
   structural comparison.
10. **Sections are document-level.** Only root-child headings create section
    boundaries.
11. **Relational views stay compact.** Ordinary bundle loading does not carry
    full AST JSON.
12. **AST serialization is opt-in.** Lightweight document and bundle operations
    do not pay wire cost for full trees.
13. **Selectors are exact-source bound.** Structural edits carry raw and
    normalized snapshot identity plus semantic identity and node path.
14. **Retrieval prefers semantic completeness.** Structural boundaries are used
    to return useful complete Markdown fragments.
15. **Agent cost matters.** Internal AST richness is not automatically exposed
    as context tokens.
16. **Wire compatibility matches deployment reality.** Negotiation protects
    independently selected engines, not impossible lockstep package mixes.
17. **The AST migration is performance-gated.** `pulldown-cmark` is not removed
    until the committed baseline gate passes.
18. **Offset correctness is conformance-tested.** BOM/newline normalization,
    frontmatter consumption and span/source-map round trips are part of CI.

# Consequences

The architecture deliberately maintains an explicit projection:

```text
markdown-rs mdast
        ↓
OKF Rust document model
        ↓
versioned JSON
        ↓
Pydantic / TypeScript document model
```

That conversion code is the price of owning a stable protocol instead of
outsourcing it to a parser dependency.

The additional `normalized_source_digest` and internal `SourceMap` also make the
source model more explicit than today. That extra machinery is necessary because
lossless authored bytes and normalized parser coordinates are genuinely
different representations.

The payoff is that Markdown stops being an opaque body string repeatedly
reparsed by each feature. It becomes a canonical structural source from which
validation, retrieval, provenance, formatting guards and guarded edits can be
derived without exposing parser internals or inflating ordinary agent context.