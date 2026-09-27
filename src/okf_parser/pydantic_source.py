"""Render Pydantic v2 source for contracts the binary compiled.

``okf-parser schema --format pydantic`` pipes ``{"contracts": [...]}`` here:
Pydantic's naming rules are Python's own (``keyword``, ``str.isidentifier``,
the attributes of ``BaseModel``), so the source is rendered where they live.
"""

from __future__ import annotations

import json
import sys

from okf_parser.pydantic_projection import render_pydantic_source
from okf_parser.schema_contract import SchemaExportError, contracts_from_json


def main() -> int:
    """Read contracts from stdin and write their Pydantic source to stdout."""
    try:
        source = render_pydantic_source(contracts_from_json(json.load(sys.stdin)))
    except SchemaExportError as error:
        sys.stderr.write(f"{error}\n")
        return 1
    sys.stdout.write(source)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
