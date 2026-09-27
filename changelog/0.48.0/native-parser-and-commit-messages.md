---
type: Release Note
title: the parser and commit messages run in the binary; Pydantic is the only dependency
---

# the parser and commit messages run in the binary; Pydantic is the only dependency

Phase 6d of [RFC 0024](../../rfcs/0024-rust-native-core.md), the last one.
`parse_document()`, `parse_document_text()` and `ingest_documents()` parse
with the engine's strict `parse_text` over a batched `__parse` request:
every YAML scalar keeps its authored spelling as a string, and a tag JSON
cannot carry (`!!binary`, `!!set`) is still an error. Ingestion reads files
as before and parses 256 documents per native call.

Commit messages are parsed, validated and formatted by
`okf_engine::git_commit`; the Python API is unchanged and the shared
vectors pass in both. The hook is now `okf-parser commit-msg PATH
[--require-envelope]`, replacing `python -m okf_parser.git_commit_cli`.

markdown-it-py and PyYAML leave the runtime dependencies, so a plain
install is okf-parser and Pydantic. Link destinations from
`markdown_facts()` keep their authored spelling (`path with spaces.md`)
instead of markdown-it's percent-encoding, as the bundle loader already
did. Removed Python internals: `iter_markdown_links`, `iter_headings`,
`has_markdown_suffix`, `looks_like_frontmatter_link`, `split_link_target`
and `split_optional_frontmatter` from `okf_parser.parser`, and
`source_digest`, `parsed_digest` and `normalize_newlines` from
`okf_parser.digests`.
