"""Subject-first OKF commit messages, parsed and formatted by the native engine.

``okf_engine::git_commit`` owns the grammar; ``okf-parser commit-msg PATH``
is the hook. This module is the Python API over the same code.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

from pydantic import BaseModel, ConfigDict

from okf_parser.models import YamlValue
from okf_parser.rust_core import native_result

if TYPE_CHECKING:
    from collections.abc import Mapping


class GitCommitMessageError(ValueError):
    """Report a structural error in a subject-first OKF commit message."""

    def __init__(self, code: str, message: str, *, line: int | None = None) -> None:
        """Store a stable error code and optional source line."""
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
        return cast("str", self.effective_frontmatter["type"])


class _Parse(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    require_envelope: bool


class _Authored(BaseModel):
    model_config = ConfigDict(frozen=True)

    subject: str
    authored_metadata: dict[str, YamlValue]
    body: str
    has_envelope: bool


class _ParseRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    parse: _Parse


class _FormatRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    format: _Authored


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    authored_metadata: dict[str, YamlValue]
    effective_frontmatter: dict[str, YamlValue]
    body: str
    has_envelope: bool
    source_digest: str
    parsed_digest: str


class _Invalid(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    line: int | None


class _Answer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    message: _Message | None = None
    invalid: _Invalid | None = None
    text: str | None = None


def _call(request: _ParseRequest | _FormatRequest) -> _Answer:
    return _Answer.model_validate(native_result("__git-commit", request))


def validate_git_commit_message(
    source: str,
    *,
    require_envelope: bool = False,
) -> GitCommitMessage:
    """Validate one prospective commit message and return its canonical projection."""
    answer = _call(_ParseRequest(parse=_Parse(source=source, require_envelope=require_envelope)))
    if answer.invalid is not None:
        raise GitCommitMessageError(
            answer.invalid.code, answer.invalid.message, line=answer.invalid.line
        )
    if answer.message is None:
        msg = "okf-parser __git-commit answered a parse without a message"
        code = "GIT_MESSAGE_PROTOCOL"
        raise GitCommitMessageError(code, msg)
    message = answer.message
    return GitCommitMessage(
        subject=message.subject,
        authored_metadata=MappingProxyType(message.authored_metadata),
        effective_frontmatter=MappingProxyType(message.effective_frontmatter),
        body=message.body,
        has_envelope=message.has_envelope,
        source_digest=message.source_digest,
        parsed_digest=message.parsed_digest,
    )


def parse_git_commit_message(source: str) -> GitCommitMessage:
    """Parse a valid UTF-8 commit message into its effective OKF projection."""
    return validate_git_commit_message(source)


def format_git_commit_message(message: GitCommitMessage) -> str:
    """Format one parsed commit message idempotently without rewriting Git history."""
    answer = _call(
        _FormatRequest(
            format=_Authored(
                subject=message.subject,
                authored_metadata=dict(message.authored_metadata),
                body=message.body,
                has_envelope=message.has_envelope,
            )
        )
    )
    if answer.text is None:
        msg = "okf-parser __git-commit answered a format without text"
        code = "GIT_MESSAGE_PROTOCOL"
        raise GitCommitMessageError(code, msg)
    return answer.text
