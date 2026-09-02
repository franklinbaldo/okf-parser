---
type: Documentation
title: Parser benchmarks
description: Comparable Python and TypeScript parser benchmark protocol
---

# Parser benchmarks

These scripts produce comparable JSON reports for the Python and TypeScript
runtimes. They generate deterministic temporary OKF bundles and remove them
after each run.

The default matrix uses 100, 1,000 and 5,000 concepts, five measured rounds and
one unmeasured warm-up round:

```bash
uv run benchmarks/python_parser.py
npm --prefix typescript run build
node benchmarks/typescript-parser.mjs
```

Use the same parameters in both runtimes when changing the corpus:

```bash
uv run benchmarks/python_parser.py \
  --sizes 100,1000 --body-paragraphs 8 --rounds 7 \
  --read-concurrencies 1,4,8,16,32,64

node benchmarks/typescript-parser.mjs \
  --sizes=100,1000 --body-paragraphs=8 --rounds=7 \
  --read-concurrencies=1,4,8,16,32,64
```

Each result reports nanoseconds per document for frontmatter boundary detection,
full in-memory document parsing, Markdown link extraction and Markdown heading
extraction. It also measures the many-small-files workload directly: recursive
Markdown discovery, sequential reads, concurrent reads, and full filesystem
bundle loading. Bundle loading also reports total milliseconds.

The concurrent-read probe uses the same requested, bounded worker count in
both runtimes: a reusable thread pool in Python and a fixed asynchronous worker
pool in TypeScript. The default is 32. Pass a comma-separated matrix to compare
multiple pressures without ever allocating one promise per corpus path. It is
diagnostic: the result shows whether a bounded batch reader is worth
implementing before considering a Rust core.

## Capability matrix

The scripts above compare okf-parser's own runtimes against each other. They say
nothing about the other tools in the ecosystem, and a latency comparison against
them would measure the wrong thing: a linter reads a document and checks a rule,
while okf-parser compiles a bundle into relational tables and a link graph.

`capability_matrix.py` compares on the axis that separates the tools instead. It
puts the same eight questions about one fixture to `okf-parser` and to six
published ecosystem tools -- `kbforge-okfquery`, `okflint`, `okf-retrieve`,
`okf-nav`, `okf-schema` and `google-okf` -- and records whether each answered
correctly, disagreed, or exposes no subcommand that could answer:

```bash
uv run --script benchmarks/capability_matrix.py
uv run --script benchmarks/capability_matrix.py --rival-bin path/to/venv/bin
```

Every expected answer is a property of the fixture documents, so okf-parser can
fail this benchmark too; it exits non-zero when it does. Rivals are installed
from PyPI and given whatever configuration they require, because a tool that
cannot start is not evidence about capability.

Results are diagnostic evidence, not a CI performance gate. Record the machine,
operating system, filesystem and cold/warm-cache conditions whenever publishing
numbers. Compare repeated runs on the same machine before drawing a Rust
migration conclusion.
