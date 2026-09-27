---
type: Release Note
title: import runs in the binary
---

# import runs in the binary

Phase 6a of [RFC 0024](../../rfcs/0024-rust-native-core.md). `okf-parser
import`, the MCP `import_preview`/`import_write` tools and `import_bundle()`
run `okf_db::import`: the source is read by DuckDB, each row is rendered as
`<type slug>/<id slug>.md` by the engine's canonical renderer, and
`verify-identical` compares parsed digests. Writes are still staged and
renamed one document at a time.

The `preview_token` is now bound to DuckDB's text of every value, so a token
printed by 0.47 or earlier does not validate a 0.48 write: preview again.
`--expected-preview-token` is available on the command line too.
