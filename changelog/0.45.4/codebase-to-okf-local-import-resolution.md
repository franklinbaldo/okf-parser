---
type: Release Note
title: Conservative local import resolution for codebase OKF
description: The codebase skill can now add separate source-tree import-resolution claims without rewriting syntax observations or claiming runtime dispatch
---

# Conservative local import resolution for codebase OKF

Adds a PEP 723 `resolve_codebase_okf.py` enrichment recipe that maps import targets only when they match a unique projected `CodeModule` in the generated source tree.

The resolver preserves `CodeImport` as immutable syntax evidence and emits separate `CodeImportResolution` concepts with explicit `source-tree-resolved` or `source-tree-partial` status, a versioned resolution method, normative type specification, deterministic regeneration and dependency-oriented query support.
