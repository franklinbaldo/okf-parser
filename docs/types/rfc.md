---
type: Spec
title: RFC
description: A proposal for a change large enough that agreeing on it before building is cheaper than rebuilding
---

# RFC

An `RFC` concept proposes a change big enough that agreeing on it first is
cheaper than building it twice. Concepts of this type live in `rfcs/`, numbered
in the order they were opened.

## Frontmatter

- `type` — always `RFC`.
- `title` — the proposal in a noun phrase.
- `status` — `draft`, `proposed`, or `accepted`.
- `description` — one sentence a reader can use to decide whether to read on.

## Numbering

The number is assigned when the RFC is opened and never reused, so a number is
enough to cite one unambiguously. Because a number is claimed by an open pull
request rather than by a merged file, two proposals can claim the same number
while both are in flight; resolving that before merge is part of review.

## Status is not review state

`accepted` means the decision holds, not that the implementation exists. An RFC
whose decisions have shipped stays `accepted`; the code is evidence, not a state
transition.
