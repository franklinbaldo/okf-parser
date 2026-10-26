---
type: Release Note
title: apply keeps the body's original bytes
---

- `apply` now copies the Markdown body byte for byte when only the frontmatter changes. Before, a body containing a bare `\r` (for example a `\rm` that lost its backslash in a LaTeX formula) came back with each `\r` turned into a newline, which silently rewrote content the edit never touched.
- The line-ending style written for the frontmatter now follows the opening delimiter line. A `\r\n` that appears only in the body no longer converts an LF frontmatter to CRLF.
- A body replaced through an edit is still written in the document's line-ending style, as before.
