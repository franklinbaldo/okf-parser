---
type: Release Note
title: canonical ordered-frontmatter fast path
---

- Add a semantics-preserving fast path for the canonical simple frontmatter order (`type`, `title`, `description`, then lexical remaining keys), while falling back to the existing YAML parser for complex or out-of-order YAML. Align Python and TypeScript canonical writers and preserve same-host benchmark evidence showing roughly 18–21% lower end-to-end load latency on the measured simple-frontmatter corpus.
