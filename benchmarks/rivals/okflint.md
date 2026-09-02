---
type: Rival
title: "okflint"
description: "Compliance linter for OKF documentary bases"
registry: pypi
package: okflint
executable: okflint
version_measured: "0.4.0"
surface:
  - audit
  - validate
  - validate-manifest
  - index
measured: true
---

# okflint

Operates on manifest-declared bases rather than on a bare directory: without an
`okf-base.yaml` it refuses to start. Its audit counts Markdown files rather than
concepts, which is a correct answer to a different question.
