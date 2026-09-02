---
type: Rival
title: "okf-nav"
description: "OKF Knowledge Navigator: search, audit, fix, and export bundles"
registry: pypi
package: okf-nav
executable: okf-nav
version_measured: "0.1.0"
surface:
  - search
  - show
  - status
  - topics
  - health
  - audit
  - export
  - index
  - update
  - stale
  - context
homepage: https://github.com/lennney/okf-nav
measured: true
agentic_enabled: true
agentic_version: "0.1.0"
agentic_executable: okf-nav
agentic_instruction: "You must use okf-nav materially to solve the task. Configure OKF_BUNDLES_DIR or any other documented discovery requirement correctly before judging capability."
---

# okf-nav

Reads bundles from an `OKF_BUNDLES_DIR` environment variable rather than from a
path argument, and reported no bundles found when the capability matrix passed
one positionally. Its search combines full-text with TF-IDF.
