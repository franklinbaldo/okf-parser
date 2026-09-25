---
type: Release Note
title: Name structural, semantic, epistemic and provenance relationships
---

# Relationship taxonomy

Add RFC 0022, naming four edge categories OKF graphs already mix without a
shared vocabulary: `structural` Markdown navigation links, `semantic`
producer-declared relations (RFC 0007/0021), `epistemic` claims such as
supports/contradicts, and `provenance` records of where a fact or edge came
from (RFC 0009). It pins which existing or planned mechanism emits each
category and requires every graph export to carry one, so a consumer never
has to infer semantics from a plain link.

`docs/architecture.md` gains a short anchor paragraph, and `LinkRecord.origin`
is documented as always category `structural`.
