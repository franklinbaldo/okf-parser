---
type: Release Note
title: Release refs can trigger the guarded dispatcher
---

- Allow `release/vX.Y.Z` branch pushes to enter the existing guarded release dispatcher when the branch SHA is exactly the current `main`; version validation, immutable tag creation/reuse, and delegation to `publish.yml` remain unchanged. This gives automation that can create Git refs a fail-closed path to initiate the official release without adding a second publisher.
