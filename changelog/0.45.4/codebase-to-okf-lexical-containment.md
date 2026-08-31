---
type: Release Note
title: expose lexical containment in codebase OKF
---

- Add immediate bidirectional lexical-containment metadata to projected classes, functions, and methods, including definition-line and generated-path identity so same-name redefinitions remain distinct.
- Add exact `--parent` lookup so agents can retrieve the immediate children of a class or callable without reopening source.
- Keep containment explicitly syntax-level: it does not claim runtime ownership, inheritance, reachability, descriptor binding, or call dispatch.
