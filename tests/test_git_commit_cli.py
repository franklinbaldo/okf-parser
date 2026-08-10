from __future__ import annotations

from pathlib import Path

from okf_parser.git_commit_cli import main


def test_commit_msg_validator_accepts_plain_message_by_default(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("Plain subject\n\nBody", encoding="utf-8")
    assert main([str(message)]) == 0


def test_commit_msg_validator_can_require_envelope(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("Plain subject\n\nBody", encoding="utf-8")
    assert main([str(message), "--require-envelope"]) == 1


def test_commit_msg_validator_reports_structural_error(tmp_path: Path) -> None:
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("Subject\n\n--- okf\ntype: Change\n", encoding="utf-8")
    assert main([str(message)]) == 1
