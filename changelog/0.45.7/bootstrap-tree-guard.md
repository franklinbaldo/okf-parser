---
type: Release Note
title: fix the one-shot npm bootstrap tree guard
---

- Make the v0.45.7 npm bootstrap compare Git tree object IDs with `git rev-parse ...^{tree}` instead of `git show`, which prints tree contents and caused the provenance guard to fail before any registry mutation.
