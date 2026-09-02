---
type: Rival
title: "kbforge-okfquery"
description: "SQL over an OKF v0.2 bundle, via DuckDB"
registry: pypi
package: kbforge-okfquery
executable: okfquery
version_measured: "0.1.0"
surface:
  - query
  - shell
  - schema
  - check
measured: true
---

# kbforge-okfquery

Loads a bundle into an in-memory DuckDB connection and hands the connection
back. Its `query` verb accepts arbitrary SQL, which is why it reaches the graph
questions this project treated as its own: recursive CTEs answer cycles, impact
and unresolved links correctly.

Discovery is hard-coded to `bundle/concepts/**/*.md`, so documents kept anywhere
else are invisible to it. Every disagreement it records in the capability matrix
traces to that one decision rather than to a missing capability.
