"""Subject-first OKF commit-message parsing and formatting."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from okf_parser.digests import canonical_json, normalize_newlines, parsed_digest, source_digest
from okf_parser.models import YamlValue
from okf_parser.parser import DocumentParseError, parse_document_text

_RESERVED_KEYS = frozenset(
    {
        "title",
        "source_kind",
        "repository_identity",
        "object_format",
        "object_type",
        "oid",
        "commit_oid",
        "tree_oid",
        "parent_oids",
        "parents",
        "author",
        "committer",
        "refs",
        "source_digest",
        "parsed_digest",
        "diagnostics",
        "provenance",
    }
)


class GitCommitMessageError(ValueError):
    """Report a structural error in a subject-first OKF commit message."""

    def __init__(self, code: str, message: str, *, line: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.line = line


@dataclass(frozen=True, slots=True)
class GitCommitMessage:
    """Canonical projection of one valid UTF-8 Git commit message."""

    subject: str
    authored_metadata: Mapping[str, YamlValue]
    effective_frontmatter: Mapping[str, YamlValue]
    body: str
    has_envelope: bool
    source_digest: str
    parsed_digest: str

    @property
    def concept_type(self) -> str:
        """Return the effective semantic OKF type."""
        value = self.effective_frontmatter["type"]
        assert isinstance(value, str)
        return value


def _parse_metadata(block: str) -> dict[str, YamlValue]:
    """Reuse the strict filesystem YAML authority without inventing Git YAML semantics."""
    source = f"---\n{block}\n---\n"
    try:
        parsed = parse_document_text(Path("<git-commit-metadata>"), source)
    except DocumentParseError as exc:
        raise GitCommitMessageError("GIT_MESSAGE_YAML", str(exc), line=3) from exc
    return dict(parsed.frontmatter)


def _validate_metadata(metadata: dict[str, YamlValue]) -> str:
    reserved = sorted(set(metadata) & _RESERVED_KEYS)
    if reserved:
        joined = ", ".join(reserved)
        raise GitCommitMessageError(
            "GIT_MESSAGE_RESERVED_KEY",
            f"commit metadata uses adapter-owned key(s): {joined}",
            line=3,
        )

    if "type" not in metadata:
        return "Commit"
    raw_type = metadata["type"]
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise GitCommitMessageError(
            "GIT_MESSAGE_TYPE",
            "commit metadata type must be a non-empty string",
            line=3,
        )
    return raw_type.strip()


def _find_closing_delimiter(envelope_source: str) -> tuple[str, str, int]:
    """Return metadata block, post-close text and the closing line number."""
    lines = envelope_source.splitlines(keepends=True)
    if not lines or lines[0].removesuffix("\n") != "--- okf":
        raise GitCommitMessageError(
            "GIT_MESSAGE_ENVELOPE",
            "structured commit metadata must start with exact '--- okf'",
            line=3,
        )

    offset = len(lines[0])
    for index, line in enumerate(lines[1:], start=1):
        logical = line.removesuffix("\n")
        if logical == "---":
            metadata = envelope_source[len(lines[0]) : offset]
            after = envelope_source[offset + len(line) :]
            return metadata.removesuffix("\n"), after, index + 3
        offset += len(line)

    raise GitCommitMessageError(
        "GIT_MESSAGE_UNTERMINATED_ENVELOPE",
        "structured commit metadata has no closing '---' delimiter",
        line=3,
    )


def parse_git_commit_message(source: str) -> GitCommitMessage:
    """Parse a valid UTF-8 commit message into its effective OKF projection."""
    exact_digest = source_digest(source)
    normalized = normalize_newlines(source).removeprefix("\ufeff")
    subject, separator, remainder = normalized.partition("\n")
    if not subject.strip():
        raise GitCommitMessageError(
            "GIT_MESSAGE_EMPTY_SUBJECT",
            "commit subject must be non-empty",
            line=1,
        )

    has_blank_separator = separator == "\n" and remainder.startswith("\n")
    candidate = remainder[1:] if has_blank_separator else remainder
    has_envelope = candidate == "--- okf" or candidate.startswith("--- okf\n")

    metadata: dict[str, YamlValue]
    body: str
    if has_envelope:
        block, after, closing_line = _find_closing_delimiter(candidate)
        if after and not after.startswith("\n"):
            raise GitCommitMessageError(
                "GIT_MESSAGE_BODY_SEPARATOR",
                "structured commit metadata must be followed by a blank line before the body",
                line=closing_line + 1,
            )
        body = after[1:] if after.startswith("\n") else ""
        if any(line == "--- okf" for line in body.splitlines()):
            raise GitCommitMessageError(
                "GIT_MESSAGE_DUPLICATE_ENVELOPE",
                "commit message contains more than one OKF envelope",
            )
        metadata = _parse_metadata(block)
    else:
        metadata = {}
        body = candidate if has_blank_separator else remainder

    effective_type = _validate_metadata(metadata)
    effective: dict[str, YamlValue] = dict(metadata)
    effective["type"] = effective_type
    effective["title"] = subject

    return GitCommitMessage(
        subject=subject,
        authored_metadata=MappingProxyType(dict(metadata)),
        effective_frontmatter=MappingProxyType(effective),
        body=body,
        has_envelope=has_envelope,
        source_digest=exact_digest,
        parsed_digest=parsed_digest(effective, body),
    )


def validate_git_commit_message(
    source: str,
    *,
    require_envelope: bool = False,
) -> GitCommitMessage:
    """Validate one prospective commit message and return its canonical projection."""
    parsed = parse_git_commit_message(source)
    if require_envelope and not parsed.has_envelope:
        raise GitCommitMessageError(
            "GIT_MESSAGE_ENVELOPE_REQUIRED",
            "repository policy requires an OKF commit envelope",
            line=3,
        )
    return parsed


def _format_metadata(metadata: Mapping[str, YamlValue]) -> str:
    """Emit a deterministic JSON-flow subset that is also valid failsafe YAML."""
    return "\n".join(
        f"{canonical_json(key)}: {canonical_json(metadata[key])}"
        for key in sorted(metadata, key=lambda value: value.encode("utf-16-be"))
    )


def format_git_commit_message(message: GitCommitMessage) -> str:
    """Format one parsed commit message idempotently without rewriting Git history."""
    if not message.has_envelope:
        return message.subject if not message.body else f"{message.subject}\n\n{message.body}"

    metadata = _format_metadata(message.authored_metadata)
    block = "--- okf\n"
    if metadata:
        block += f"{metadata}\n"
    block += "---"
    return f"{message.subject}\n\n{block}" if not message.body else f"{message.subject}\n\n{block}\n\n{message.body}"
