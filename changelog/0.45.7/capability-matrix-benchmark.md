---
type: Release Note
title: benchmark what each OKF tool can answer, not how fast it answers
---

- Add `benchmarks/capability_matrix.py`, which puts eight questions about one shared fixture to `okf-parser`, `okflint`, `okf-cli` and `google-okf` and records, per question, whether each tool answered correctly, disagreed, or exposes no subcommand that could answer at all. Each rival's advertised command surface is recorded next to its verdicts so an unsupported verdict is auditable rather than asserted.
- Build the fixture so that every expected answer is a property of the documents it writes. The benchmark therefore fails when `okf-parser` is wrong, not only when a rival is, and it exits non-zero in that case.
- Ship the `okf-base.yaml` manifest `okflint` requires. A rival that cannot start is not evidence about capability.
- Deliberately do not measure latency against these tools. A linter that reads a document and checks a rule is faster than something that compiles a bundle into relational tables and a link graph; that is a consequence of doing more, and a timing comparison would measure the wrong thing.
