from __future__ import annotations

import json
from pathlib import Path

import pytest

from okf_parser.git_commit import (
    GitCommitMessageError,
    format_git_commit_message,
    parse_git_commit_message,
    validate_git_commit_message,
)


def _vectors() -> dict[str, list[dict[str, object]]]:
    path = Path(__file__).parents[1] / "conformance" / "git-commit-messages.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_shared_valid_vectors() -> None:
    for item in _vectors()["valid"]:
        parsed = parse_git_commit_message(str(item["source"]))
        assert parsed.subject == item["subject"]
        assert dict(parsed.authored_metadata) == item["authored_metadata"]
        assert dict(parsed.effective_frontmatter) == item["effective_frontmatter"]
        assert parsed.body == item["body"]
        assert parsed.has_envelope is item["has_envelope"]
        assert parsed.source_digest == item["source_digest"]
        assert parsed.parsed_digest == item["parsed_digest"]

        formatted = format_git_commit_message(parsed)
        assert formatted == item["formatted"]
        reparsed = parse_git_commit_message(formatted)
        assert format_git_commit_message(reparsed) == formatted
        assert dict(reparsed.effective_frontmatter) == dict(parsed.effective_frontmatter)
        assert reparsed.body == parsed.body


def test_shared_invalid_vectors() -> None:
    for item in _vectors()["invalid"]:
        with pytest.raises(GitCommitMessageError) as caught:
            parse_git_commit_message(str(item["source"]))
        assert caught.value.code == item["code"]


def test_policy_can_require_envelope_without_changing_legacy_parsing() -> None:
    source = "Legacy commit\n\nStill readable."
    assert parse_git_commit_message(source).has_envelope is False
    with pytest.raises(GitCommitMessageError, match="requires an OKF commit envelope") as caught:
        validate_git_commit_message(source, require_envelope=True)
    assert caught.value.code == "GIT_MESSAGE_ENVELOPE_REQUIRED"


def test_effective_title_and_default_type_participate_in_parsed_digest() -> None:
    first = parse_git_commit_message("First subject\n\nSame body")
    second = parse_git_commit_message("Second subject\n\nSame body")
    typed = parse_git_commit_message("First subject\n\n--- okf\ntype: Change\n---\n\nSame body")

    assert first.parsed_digest != second.parsed_digest
    assert first.parsed_digest != typed.parsed_digest
