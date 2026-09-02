---
type: Rival
title: "okf-cli"
description: "Open Knowledge Format tooling"
registry: pypi
package: okf-cli
executable: okf
surface:
  - bundle
  - list
  - read
  - validate
version_measured: "0.6.1"
measured: true
---

# okf-cli

It installs the `okf` command, and so do [[okf-retrieve]] and
[[okf-generator]]. The first published matrix installed them into one
environment and could therefore only ever measure whichever landed last; this
one gives every rival an environment of its own, which is what made this tool
measurable at all.
