---
type: Release Note
title: Make release smoke tests independent of runtime dependencies
---

- Install the Python DuckDB module explicitly before checking a database exported by the public PyPI wheel. The CLI bundles libduckdb and does not require that Python module at runtime.
- Allow bounded registry latency with a 30-second connection timeout, a 120-second HTTP-read timeout, and three retries.
