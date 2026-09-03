---
type: Release Note
title: exercise the published npm CLI through a supported flag
---

- Fix the one-shot npm bootstrap smoke test to invoke `okf-parser-ts --help`, which is part of the published CLI contract, instead of the unsupported `--version` flag. The bootstrap remains resumable: already-published 0.45.7 packages are accepted only when their registry integrity matches the reviewed release artifacts before the public install/import smoke and release-tag creation continue.
