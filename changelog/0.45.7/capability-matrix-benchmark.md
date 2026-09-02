---
type: Release Note
title: benchmark what each OKF tool can answer, not how fast it answers
---

- Add `benchmarks/capability_matrix.py`, which puts eight questions about one shared fixture to `okf-parser` and to six published ecosystem tools -- `kbforge-okfquery`, `okflint`, `okf-retrieve`, `okf-nav`, `okf-schema` and `google-okf` -- and records, per question, whether each answered correctly, disagreed, or exposes no subcommand that could answer at all.
- Build the fixture so that every expected answer is a property of the documents it writes. The benchmark therefore fails when `okf-parser` is wrong, not only when a rival is, and it exits non-zero in that case. It did fail on the first run: specification documents are concepts of type `Spec` and belong to the bundle they describe, which the first oracle did not account for.
- Record that `kbforge-okfquery` answers cycles, impact analysis and unresolved-link counting correctly through recursive SQL. Relational access to a bundle is not exclusive to `okf-parser`, and any claim that it is does not survive the measurement. Its four disagreements all trace to one cause: it hard-codes discovery to `bundle/concepts/**/*.md` and therefore reads six of the fixture's nine concepts.
- Record that `google-okf` and `okf-retrieve` fail a bundle whose only defect is an unresolved link, which OKF v0.2 says does not make a bundle non-conformant. A bundle that passes one gate fails another, so conformance is not yet a portable claim.
- Give every rival the configuration it requires, including the `okf-base.yaml` manifest `okflint` asks for and both link models -- frontmatter `links:` and Markdown body links -- because the tools disagree about where a link lives. A rival that cannot start is not evidence about capability.
- Deliberately do not measure latency against these tools. A linter that reads a document and checks a rule is faster than something that compiles a bundle into relational tables and a link graph; a timing comparison would measure the wrong thing.
