---
type: Release Note
title: Native import preserves structured source values
---

# Native import preserves structured source values

The Rust-native importer no longer flattens DuckDB `LIST` and `STRUCT` values
to strings before rendering frontmatter. Collection shape is preserved
recursively, while number and boolean leaves keep the parser's existing OKF
scalar contract by using their textual spelling.

Writes remain UTF-8/LF through the native staged byte writer.
