---
type: Release Note
title: Git commits as OKF proposal
---

- Accept RFC 0009 defining Git commit messages as an explicit OKF source-adapter surface: subject-first `--- okf` envelopes preserve ordinary Git subjects, plain commits remain representable as `type: Commit`, Git object/provenance identity stays adapter-owned, and `parsed_digest` covers the effective semantic OKF projection rather than Git transport metadata.
