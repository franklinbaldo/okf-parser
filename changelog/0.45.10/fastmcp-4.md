---
type: Release Note
title: FastMCP 4 runtime
---

# FastMCP 4 runtime

The Python MCP surface now targets FastMCP 4.0.5+ and Pydantic 2.12+.

Python-side MCP model access uses the current snake_case API directly rather
than the temporary camelCase compatibility bridge, and CI disables that bridge
while running the Python suite. Wire-format MCP field names are unchanged.
