---
type: RFC
title: Relationship taxonomy for OKF graphs
status: draft
description: Name four non-conflatable edge categories — structural, semantic, epistemic, provenance — pin which existing mechanism emits each, and require every graph export to carry a category so consumers stop inferring meaning from a plain link
---

# RFC 0022: Relationship taxonomy for OKF graphs

## Summary

An OKF bundle already produces more than one kind of edge: a Markdown `[text](target)` link, a producer-declared relation over typed frontmatter, a claim that one fact supports or contradicts another, and a record of where a fact came from. Nothing today stops these from landing in the same flattened "the graph" a consumer walks, and nothing requires a consumer to check which kind an edge is before treating it as evidence.

This RFC does not add a new relation mechanism. It names four categories that already exist in scattered form, states which existing or planned mechanism emits each one, and adds one requirement that binds them together: **every edge a graph export produces must carry an explicit category, and no export may collapse two categories into an edge list that cannot tell them apart.**

## Motivation

`Bundle.links` (`src/okf_parser/bundle.py`, `LinkRecord` in `src/okf_parser/models.py`) already carries an `origin` field, and the GraphQL `Link` type (`src/okf_parser/graphql_adapter.py`) already exposes it. In practice `origin` is hardcoded to the literal `"body"` for every link produced today — it distinguishes nothing yet, because only one producer exists. RFC 0021 anticipates a second value, `producer-sql`, for rows a bundle publishes through `okf_relations.edges`, and says explicitly that "Markdown links remain distinguishable by `origin`; relation SQL does not erase syntax provenance." That is the right instinct, applied so far to exactly one boundary: navigation syntax versus producer-declared relation.

Two more kinds of edge are already implied elsewhere in this repository without a name:

- **Epistemic claims.** `skills/codebase-to-okf/SKILL.md` marks generated code-graph edges `syntactic-unresolved` specifically to keep "navigation candidates" distinct from "dispatch claims," and its agent workflow instructs keeping "manifest declarations, syntax observations, source-tree resolution and runtime claims epistemically distinct." That is an epistemic-strength distinction, scoped narrowly to one recipe's call graph. Issue #277 asks for the general case: a fact that one concept *supports* or *contradicts* another is a claim about epistemic relationship, not a navigation edge or a producer join.
- **Provenance.** RFC 0009 already keeps "Authored and Git-derived facts" provenance-distinct and gives Git Notes "their own ... provenance" as separate records. `docs/architecture.md` states the general rule that an adapter "must preserve enough provenance to distinguish authored evidence from projection policy." Provenance answers "where did this fact/edge come from," which is a different question from "what does this edge mean," and both are different again from "does the target exist."

None of this is wrong so far — each mechanism was built for its own boundary. The risk is conflation once a consumer reaches for "the graph": a Markdown navigation link, a `Fundamentacao.regra → Regra.nome` foreign key, a `supports` claim and a Git-derived-fact provenance pointer are four different kinds of statement, and only one of docs/architecture.md's several relation mechanisms (`origin` on `LinkRecord`) currently even tries to say which kind an edge is.

## Decision

### 1. Four categories, named once

| category | answers | example | current/target mechanism |
| --- | --- | --- | --- |
| `structural` | "how do you navigate from here?" | a Markdown `[text](target.md)` link in a document body | `Bundle.links` / `LinkRecord.origin` (today: only value, hardcoded `"body"`) |
| `semantic` | "what does the producer assert this concept is related to?" | `Fundamentacao.regra → Regra.nome`, a declared FK-style reference | RFC 0007 relational constraints; RFC 0021 `okf_relations.edges` (`origin: "producer-sql"`) |
| `epistemic` | "what stance does one fact take on another?" | `supports`, `contradicts`, `syntactic-unresolved` vs. `resolved-call` | not yet implemented as a core mechanism; the `codebase-to-okf` skill's ad hoc predicate strings are the only existing precedent |
| `provenance` | "where did this fact or edge come from, and on what authority?" | a fact derived from a Git commit vs. authored directly; a relation produced by `producer-sql` vs. observed syntax | RFC 0009 (Git-derived vs. authored facts, Git Notes); RFC 0021's `origin` field on `okf_relations.edges` |

A category is a closed, small vocabulary. It is not the same thing as `origin`/`predicate`, which stays open and producer- or mechanism-defined *within* a category (RFC 0021 already treats `origin` this way for `producer-sql` vs. Markdown-link values; this RFC generalizes the same field to also carry the coarser category).

### 2. Category is mandatory on every graph export

Any surface that emits an edge-shaped record — `Bundle.links`, GraphQL `Link`, a future `okf_relations.edges` row, a future epistemic-claim surface, a future NetworkX projection — must include a `category` value drawn from the table above, in addition to whatever finer `origin`/`predicate` detail that surface already carries. An export with edges from more than one category must not merge them into a single list that erases which category each row belongs to; a combined view is only acceptable when `category` survives as a queryable column/field on every row.

This is additive to `LinkRecord.origin` and the `okf_relations.edges.origin` column from RFC 0021, not a replacement: `origin`/`predicate` keeps naming the producing mechanism (`"body"`, `"producer-sql"`, a future `"git-commit"`), while `category` names which of the four buckets that mechanism belongs to. `LinkRecord`'s current sole producer is `structural`; nothing here requires renaming its existing field before a second producer exists.

### 3. Structural links never carry semantic, epistemic, or provenance meaning by construction

A Markdown link is evidence of navigation, nothing more. Its target existing, its anchor text, or its position in the document must never be read by core tooling as an assertion that the source concept is semantically related to, supports, contradicts, or derives from the target. Semantic, epistemic, and provenance edges must be authored or derived through their own mechanism (frontmatter, `okf_relations.edges`, a future epistemic surface, Git-derived provenance) and must never be synthesized by pattern-matching a link's shape or destination. `docs/architecture.md`'s existing rule — "classification alone does not grant normative relation semantics" — already states this for the discovery/classification boundary; this RFC extends the same rule to the graph-consumption boundary.

### 4. Epistemic relations get a name, not yet an implementation

This RFC does not ship an epistemic-relation mechanism. It reserves the category name and the constraint that when one is built (predicates such as `supports`/`contradicts`, or the `codebase-to-okf` skill's `syntactic-unresolved`/`resolved-call` distinction generalized into core), it must emit `category: epistemic` and must not be represented as a plain Markdown link or folded into `okf_relations.edges` without that field. Design of the epistemic mechanism itself — how a claim is authored, what predicate vocabulary is core vs. producer-defined, whether claims are frontmatter or their own concept type — is out of scope here and left to a follow-up RFC.

### 5. Provenance is a property of an edge (and a fact), not a fifth kind of edge that duplicates the other three

`provenance` in the table above describes *where a structural, semantic, or epistemic edge came from* (authored vs. derived, which adapter or recipe produced it), not a competing edge type consumers walk instead of the other three. RFC 0009's Git-derived-fact provenance and RFC 0021's `producer-sql` origin value are both provenance information attached to edges/facts that are already structural or semantic; nothing here asks for a separate provenance graph parallel to the real one. A future surface may expose provenance as its own field (e.g., `derived_from`) without inventing a fifth top-level category.

## Non-goals

- Does not change `LinkRecord`'s schema, `okf_relations.edges`'s schema, or GraphQL SDL in this RFC. Adding the `category` field to each concrete surface is implementation work tracked against issue #277, sequenced after this taxonomy is agreed.
- Does not design the epistemic-relation authoring mechanism (predicate vocabulary, storage, validation). That is a follow-up RFC.
- Does not require every producer relation to be a graph edge; RFC 0021 §6 already establishes that only rows intentionally projected into `okf_relations.edges` become generic graph edges, and that stays true per category.
- Does not retroactively reinterpret existing `origin` values; `"body"` and `"producer-sql"` keep their current meaning and simply gain an associated `category`.

## Consequences

A consumer reading any edge-shaped record from `okf-parser` can rely on `category` being present and can branch on it before treating the edge as navigation, a producer assertion, a stance, or a derivation record. `docs/architecture.md` gains one anchor point — this RFC — instead of the rule being implicit across RFC 0007, RFC 0009 and RFC 0021 independently.

## Open questions

- Where `category` lives physically on each surface (a new column vs. deriving it from existing `origin`/table namespace) is left to the implementation issue for each surface.
- Whether `category` should be a core enum enforced by the parser or a convention documented for producers, given RFC 0021's general preference for trusted producer SQL over parser-enforced restriction.
- Whether provenance eventually needs its own field separate from `origin`/`category`, once a second provenance-bearing mechanism beyond Git-derived facts exists.

Parent tracking: #277.
