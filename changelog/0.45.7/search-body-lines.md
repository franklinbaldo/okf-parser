---
type: Release Note
title: Share search body-line semantics
---

- Factor the existing Markdown body `splitlines()` behavior into shared runtime support for RFC 0016 search work.
- Freeze edge cases such as empty bodies, terminal newlines, CRLF/CR and Unicode line separators in tests.
- Keep relational `apply` body-line materialization on the same observable semantics.
