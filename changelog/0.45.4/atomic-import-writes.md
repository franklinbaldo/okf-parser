---
type: Release Note
title: atomic per-document import writes
---

- Make `import --write` stage each destination and rename it into place instead of writing directly to the live Markdown path. A crash during one document write can therefore leave a temporary file but cannot leave a truncated concept at the destination; regression coverage injects a mid-write failure and verifies every committed document remains byte-identical to a clean import.
