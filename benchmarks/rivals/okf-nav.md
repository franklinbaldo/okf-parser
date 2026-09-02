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
---

# okf-nav

Reads bundles from an `OKF_BUNDLES_DIR` environment variable rather than from a
path argument. The first published matrix passed the bundle positionally, so the
tool reported finding none and was recorded as answering nothing -- a defect in
the harness presented as a finding about the tool.

Shown a directory of bundles through that variable, `status` reports the
fixture's nine concepts and their exact type distribution. Its search combines
full-text with TF-IDF.
