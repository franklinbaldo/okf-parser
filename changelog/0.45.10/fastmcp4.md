---
type: Release Note
title: FastMCP 4 and current Cyclopts compatibility
---

- support the stable FastMCP 4 line (`>=4.0.5,<5`) while preserving the existing MCP tool schemas, effect annotations, read/write profile isolation, and client contract;
- migrate direct MCP model access to FastMCP 4 / MCP SDK snake-case fields instead of relying on the deprecated camelCase compatibility bridge;
- raise the Cyclopts floor to the current 4.25 line; dedicated compatibility CI installs FastMCP 4.0.5 and Cyclopts 4.25.2 in a clean environment and exercises `tests/test_mcp.py`.
