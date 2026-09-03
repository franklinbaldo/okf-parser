---
type: Release Note
title: verify bootstrap provenance for synthetic pull-request commits
---

- Resolve the release dry-run's synthetic pull-request merge commit through the GitHub commit API instead of assuming that commit remains reachable from a normal clone. Pin the immutable Actions artifact by ID, workflow run, name, and digest before the temporary npm bootstrap can publish anything.
