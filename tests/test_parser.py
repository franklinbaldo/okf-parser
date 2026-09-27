"""Unit tests for frontmatter parsing."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from okf_parser.parser import (
    DocumentParseError,
    markdown_facts,
    parse_document,
    parse_texts,
)

if TYPE_CHECKING:
    from pathlib import Path


def _headings(body: str) -> list[tuple[int, str]]:
    return list(markdown_facts(body).headings)


def test_frontmatter_value_may_contain_triple_dash(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text(
        "---\ntype: Reference\nnote: before --- after\n---\n# Body\n",
        encoding="utf-8",
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["note"] == "before --- after"
    assert parsed.body == "# Body\n"


def test_linear_splitter_preserves_crlf_body_and_delimiter_like_content(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_bytes(
        b"---\r\ntype: Reference\r\nnote: before --- after\r\n---\t \r\n# Body\r\n---\r\n"
    )

    parsed = parse_document(path)

    assert parsed.frontmatter["note"] == "before --- after"
    assert parsed.body == "# Body\n---\n"


def test_frontmatter_delimiter_must_be_an_isolated_line(tmp_path: Path) -> None:
    path = tmp_path / "concept.md"
    path.write_text("---\ntype: Reference\n--- nope\n# Body\n", encoding="utf-8")

    with pytest.raises(DocumentParseError, match="frontmatter delimited"):
        parse_document(path)


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

    assert list(markdown_facts(body).links) == [
        "ordinary.md",
        "path with spaces.md",
        "guide_(v2).md",
    ]


def test_markdown_facts_collect_links_and_headings_together() -> None:
    body = "# Title\n\n[ordinary](ordinary.md)\n\n## Detail\n"
    facts = markdown_facts(body)

    assert facts.links == ("ordinary.md",)
    assert facts.headings == ((1, "Title"), (2, "Detail"))


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

    assert parsed.frontmatter_json == '{"created":"2026-01-01","number":"0012","type":"Reference"}'


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


def test_headings_reports_empty_heading_text() -> None:
    assert _headings("#\n") == [(1, "")]
    assert _headings("# \n") == [(1, "")]


def test_headings_ignores_fenced_code_blocks() -> None:
    body = "# Title\n\n## 2026-01-01\n\n```markdown\n# fake title\n## fake date\n```\n"

    assert _headings(body) == [(1, "Title"), (2, "2026-01-01")]


def test_optional_frontmatter_is_absent_when_the_document_does_not_open_with_it() -> None:
    reserved, unclosed = parse_texts(["# Index\n", "---\nokf_version: x\n"], optional=True)

    assert not isinstance(reserved, DocumentParseError)
    assert reserved.frontmatter is None
    assert reserved.parsed_digest is None
    assert reserved.body == "# Index\n"
    assert isinstance(unclosed, DocumentParseError)
    assert "delimiters" in str(unclosed)


def test_a_batch_reports_each_failure_without_failing_the_others() -> None:
    good, bad = parse_texts(["---\ntype: A\n---\n", "no frontmatter\n"])

    assert not isinstance(good, DocumentParseError)
    assert good.frontmatter == {"type": "A"}
    assert isinstance(bad, DocumentParseError)
    assert parse_texts([]) == ()
