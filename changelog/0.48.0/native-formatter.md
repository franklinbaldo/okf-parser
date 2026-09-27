---
type: Release Note
title: format runs in the binary and rewrites syntax only
---

# format runs in the binary and rewrites syntax only

Phase 5 of [RFC 0024](../../rfcs/0024-rust-native-core.md). `okf-parser
format`, the MCP `format_check`/`format_write` tools and `format_path()` run
`okf_engine::format`, a surgical formatter over pulldown-cmark: it rewrites only
the tokens that spell a document's structure, at their source offsets, and
keeps every other byte.

The canonical form:

- `-` bullets (`*` for a list right after another bullet list, so the two stay
  distinct), consecutive ordered numbering from the list's start, never
  zero-padded, one space after each marker;
- `*` and `**` emphasis, ATX headings, compact tables (`| a | b |`,
  `| --- | :-: |`), backslash hard breaks;
- single blank lines between blocks, no trailing whitespace, one final newline;
- a blank line after frontmatter, and simple frontmatter keys ordered `type`,
  `title`, `description`, then by name, as before.

Text, code, HTML, links and escapes are never touched: the rendering
formatters considered (comrak, dprint) escaped `x86_64` and `#25` or dedented
fenced code. A rewrite must parse to the same document as the original, or the
file is skipped.

**Breaking.** The report replaces `skipped_paths` with `skipped`, a list of
`{path, reason}` (`FormatReport.skipped_paths` remains as a property in
Python). `--write` now replaces every changed file or, on a write error, none.
Documents formatted by mdformat mostly stay as they are; tables with padded
columns and `_emphasis_` change once. mdformat, mdformat-gfm and
mdformat-frontmatter are no longer dependencies; `okf_parser.markdown_style`
and `okf_parser.frontmatter_order` are removed.
