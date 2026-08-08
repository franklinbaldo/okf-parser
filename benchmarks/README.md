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
uv run python benchmarks/python-parser.py
npm --prefix typescript run build
node benchmarks/typescript-parser.mjs
```

Use the same parameters in both runtimes when changing the corpus:

```bash
uv run python benchmarks/python-parser.py \
  --sizes 100,1000 --body-paragraphs 8 --rounds 7

node benchmarks/typescript-parser.mjs \
  --sizes=100,1000 --body-paragraphs=8 --rounds=7
```

Each result reports nanoseconds per document for frontmatter boundary detection,
full in-memory document parsing, Markdown link extraction, Markdown heading
extraction and full filesystem bundle loading. Bundle loading also reports total
milliseconds.

Results are diagnostic evidence, not a CI performance gate. Record the machine,
operating system, filesystem and cold/warm-cache conditions whenever publishing
numbers. Compare repeated runs on the same machine before drawing a Rust
migration conclusion.
