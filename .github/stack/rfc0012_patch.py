from pathlib import Path

path = Path("rfcs/0012-relational-agent-surfaces.md")
text = path.read_text(encoding="utf-8")

replacements = [
    (
        """When RFC 0010 is available, this boundary may use the native table functions.\nOtherwise it uses the portable canonical fallback. Conformance tests compare the\nbackends.\n""",
        """Milestone 1a is implementable today: define the relation-provider/service\nboundary with the portable canonical fallback and a language-neutral conformance\ncorpus that pins the four relation contracts.\n\nMilestone 1b is conditional on RFC 0010 being accepted and available: wire the\nnative table-function provider to the same boundary and run the same corpus as a\nbackend-parity test. RFC 0012 acceptance and milestone 1a do not depend on the\nnative extension having landed first.\n""",
        "decision 1",
    ),
    (
        """Default traversal for \"what may depend on this change\" follows incoming links\n(reverse edge direction) with an explicit depth bound. Consumers may request\noutgoing or both directions.\n\nThe traversal must:\n\n- maintain a visited set and terminate on cycles;\n- emit each impacted `concept_id` once at its minimum discovered depth;\n- use deterministic frontier ordering by canonical `concept_id`;\n- produce stable final ordering by `(depth, concept_id)`;\n- never depend on NetworkX insertion order, SQL incidental row order, or worker\n  completion order.\n\nDuckDB recursive CTEs, NetworkX, or another graph implementation are acceptable\nif they satisfy that contract over the same link relation.\n""",
        """Default traversal for \"what may depend on this change\" follows incoming links\n(reverse edge direction) with an explicit depth bound. Consumers may request\noutgoing or both directions.\n\nTraversal is defined over the union of link identities from both snapshots,\n`E_base ∪ E_head`, so a removed edge remains visible through `base` and an added\nedge is visible through `head`. Every traversed edge carries snapshot presence:\n`base`, `head`, or both.\n\nThe union is a discovery surface, not permission to invent a path that never\nexisted. Each frontier state carries a path-support set initialized from the\nseed's valid snapshot(s). Crossing an edge intersects that set with the edge's\nsnapshot presence. A state whose support becomes empty is discarded. Therefore\nan edge that exists only in `base` cannot be concatenated with a later edge that\nexists only in `head` and reported as one historical path.\n\nReported impact preserves snapshot provenance for the accepted path (or paths):\nwhich snapshot(s) support the reachability and, when path detail is requested,\nthe snapshot presence of each edge. A concept reachable in both snapshots may\nstill be emitted once at minimum depth while aggregating the supported snapshot\nset deterministically.\n\nThe traversal must:\n\n- maintain cycle-safe visited state keyed by at least concept identity plus\n  snapshot-support state and terminate on cycles;\n- emit each impacted `concept_id` once at its minimum discovered depth, with\n  deterministic aggregation of supported snapshots when equal-depth paths exist;\n- use deterministic frontier ordering by canonical `concept_id` and snapshot\n  support;\n- produce stable final ordering by `(depth, concept_id)`;\n- never depend on NetworkX insertion order, SQL incidental row order, or worker\n  completion order.\n\nDuckDB recursive CTEs, NetworkX, or another graph implementation are acceptable\nif they satisfy that contract over the base/head link snapshots from the same\nrelational service.\n""",
        "impact",
    ),
    (
        """1. **Shared relational read service** — canonical relation provider + native\n   RFC 0010/fallback parity tests.\n2. **`query` consumer** — resolve #151 over that service.\n3. **Structured lookup + generic relation navigation** — service first, then\n   CLI/MCP adapters.\n4. **Relational diff** — identity, shipped digests, fields, links, diagnostics.\n5. **Cycle-safe impact** — deterministic bounded reachability.\n6. **Budgeted context** — hard byte/relation/depth contracts.\n7. **Agent installation helpers** — minimal instructions over stable surfaces.\n8. **Incremental compiled-image optimization** — only after profiling proves\n   benefit.\n""",
        """1a. **Portable shared relational read service** — canonical relation-provider\n    boundary + portable fallback + language-neutral conformance corpus. This is\n    independently implementable before RFC 0010 lands.\n1b. **Native provider parity** — once RFC 0010 is accepted/available, wire its\n    table functions to the same service and run the same conformance corpus\n    against native and portable providers.\n2. **`query` consumer** — resolve #151 over that service.\n3. **Structured lookup + generic relation navigation** — service first, then\n   CLI/MCP adapters.\n4. **Relational diff** — identity, shipped digests, fields, links, diagnostics.\n5. **Cycle-safe snapshot-aware impact** — deterministic bounded reachability\n   over base/head relation snapshots without cross-snapshot synthetic paths.\n6. **Budgeted context** — hard byte/relation/depth contracts.\n7. **Agent installation helpers** — minimal instructions over stable surfaces.\n8. **Incremental compiled-image optimization** — only after profiling proves\n   benefit.\n""",
        "implementation order",
    ),
    (
        """1. **Backend parity.** A language-neutral fixture evaluated through the native\n   RFC 0010 provider and portable fallback yields canonical-equal `concepts`,\n   `links`, `reserved`, and `diagnostics` after explicit deterministic ordering.\n   If the native provider is unavailable on a CI target, its conformance corpus\n   must run on at least one supported native target and the fallback on all\n   portable targets.\n""",
        """1. **Relation contract first; backend parity when native exists.** The portable\n   provider must pass a language-neutral corpus that pins canonical `concepts`,\n   `links`, `reserved`, and `diagnostics` with explicit deterministic ordering.\n   Once RFC 0010 is accepted/available, the native provider must pass that same\n   corpus and yield canonical-equal relations. RFC 0012 acceptance does not\n   require a native provider that has not landed yet; parity becomes mandatory\n   when that backend exists.\n""",
        "invariant 1",
    ),
    (
        """6. **Impact terminates deterministically.** A cyclic link fixture terminates,\n   emits each concept once at minimum depth, and produces the same\n   `(depth, concept_id)` ordering across repeated runs/backends.\n""",
        """6. **Impact is snapshot-aware and deterministic.** Fixtures cover a removed\n   base-only edge, an added head-only edge, a cycle, and a tempting mixed path\n   whose consecutive edges never coexist in one snapshot. Traversal preserves\n   removed/added reachability, rejects the synthetic cross-snapshot path,\n   terminates, emits each concept once at minimum depth, records supported\n   snapshot provenance, and produces stable `(depth, concept_id)` ordering.\n""",
        "invariant 6",
    ),
]

for old, new, label in replacements:
    if old not in text:
        raise SystemExit(f"{label} replacement anchor not found")
    text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")
