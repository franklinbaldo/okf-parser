---
type: Release Note
title: Derived runtime index and log views
---

Add `okf-parser materialize` to generate deterministic disposable `index.md`
and `log.md` projections from authored OKF state. The log uses only explicit
frontmatter dates, never Git history or filesystem mtimes, and write mode keeps
the generated views out of version control by default.
