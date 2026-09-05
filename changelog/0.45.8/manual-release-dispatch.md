---
type: Release Note
title: Manual releases dispatch the immutable tag pipeline
---

- Fix the manual release entrypoint: validate an explicit semantic version against `main`, create or safely reuse the matching immutable `vX.Y.Z` tag at the current `main` SHA, and explicitly dispatch the existing release workflow on that tag. This keeps manual publication behind the same build, manifest, PyPI, public smoke-test, and GitHub Release gates as tag-driven releases.
