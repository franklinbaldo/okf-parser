"""Unit tests for frontmatter parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.parser import (
    DocumentParseError,
    has_markdown_suffix,
    iter_headings,
    iter_markdown_links,
    looks_like_frontmatter_link,
    parse_document,
    resolve_local_target,
)

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


def test_ordinary_yaml_scalars_preserve_their_authored_spelling(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\n"
        "type: Reference\n"
        "number: 0012\n"
        "active: false\n"
        "created: 2026-01-01\n"
        "nothing: null\n"
        "---\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter == {
        "type": "Reference",
        "number": "0012",
        "active": "false",
        "created": "2026-01-01",
        "nothing": None,
    }


def test_yaml_merge_keys_keep_structure_and_string_scalars(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\n"
        "type: Reference\n"
        "defaults: &defaults\n"
        "  active: true\n"
        "item:\n"
        "  <<: *defaults\n"
        "  title: Example\n"
        "---\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["item"] == {
        "active": "true",
        "title": "Example",
    }


def test_plain_scalar_mapping_keys_are_strings(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\ntype: Reference\n2026-01-01: released\n---\n", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.frontmatter["2026-01-01"] == "released"


def test_cyclic_yaml_anchor_is_a_parse_error_not_a_recursion_error(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\ntype: Reference\nself: &a\n  child: *a\n---\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="cyclic"):
        parse_document(path)


def test_empty_frontmatter_block_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\n---\n# Body\n", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.frontmatter == {}
    assert parsed.body == "# Body\n"


def test_blank_frontmatter_block_is_still_valid(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\n\n---\n# Body\n", encoding="utf-8")

    parsed = parse_document(path)

    assert parsed.frontmatter == {}
    assert parsed.body == "# Body\n"


def test_frontmatter_json_preserves_scalar_strings(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\ntype: Reference\ncreated: 2026-01-01\nnumber: 0012\n---\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter_json == (
        '{"created": "2026-01-01", "number": "0012", "type": "Reference"}'
    )


def test_malformed_url_target_does_not_raise(tmp_path: Path) -> None:
    assert has_markdown_suffix("http://[oops/x.md") is False
    assert resolve_local_target(tmp_path, tmp_path / "a.md", "http://[oops/x.md") is None


def test_frontmatter_link_inference_ignores_prose() -> None:
    assert looks_like_frontmatter_link("/b.md") is True
    assert looks_like_frontmatter_link("Supersedes the old draft in notes.md") is False


def test_markdown_suffix_is_case_insensitive() -> None:
    assert has_markdown_suffix("b.MD") is True


def test_binary_frontmatter_value_is_rejected_not_coerced(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\ntype: Reference\nblob: !!binary aGk=\n---\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="unsupported YAML value"):
        parse_document(path)


def test_set_frontmatter_value_is_rejected_not_coerced(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\ntype: Reference\ntags: !!set {b: null, a: null}\n---\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="unsupported YAML value"):
        parse_document(path)


def test_iter_headings_reports_empty_heading_text() -> None:
    assert iter_headings("#\n") == [(1, "")]
    assert iter_headings("# \n") == [(1, "")]


def test_iter_headings_ignores_fenced_code_blocks() -> None:
    body = "# Title\n\n## 2026-01-01\n\n```markdown\n# fake title\n## fake date\n```\n"

    assert iter_headings(body) == [(1, "Title"), (2, "2026-01-01")]
