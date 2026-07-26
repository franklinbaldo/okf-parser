"""Unit tests for frontmatter parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.parser import DocumentParseError, iter_markdown_links, parse_document

if TYPE_CHECKING:
    from pathlib import Path


def test_frontmatter_value_may_contain_triple_dash(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\ntype: Reference\nnote: before --- after\n---\n# Body\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["note"] == "before --- after"
    assert parsed.body == "# Body\n"


def test_frontmatter_must_be_mapping(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\n- item\n---\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="mapping"):
        parse_document(path)


def test_utf8_bom_is_accepted(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("\ufeff---\ntype: Reference\n---\nBody\n", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.frontmatter["type"] == "Reference"


def test_link_extraction_uses_commonmark_tokens() -> None:
    body = """\
[ordinary](ordinary.md)

`[inline](inline.md)`

```markdown
[fenced](fenced.md)
```

[angle](<path with spaces.md>)
[balanced](guide_(v2).md)
"""

    assert iter_markdown_links(body) == [
        "ordinary.md",
        "path%20with%20spaces.md",
        "guide_(v2).md",
    ]
