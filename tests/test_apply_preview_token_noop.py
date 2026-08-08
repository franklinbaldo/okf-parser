from __future__ import annotations

from pathlib import Path

from okf_parser.apply import apply_bundle


def test_intervening_change_that_makes_apply_a_noop_still_conflicts(tmp_path: Path) -> None:
    note = tmp_path / "note.md"
    note.write_text(
        """---
type: Note
status: todo
---
Body
""",
        encoding="utf-8",
    )
    sql = 'UPDATE "Note" SET status = \'done\' WHERE __okf_concept_id = \'note\''
    preview = apply_bundle(str(tmp_path), sql=sql)
    token = preview["preview_token"]
    assert isinstance(token, str)

    # Another actor reaches the same logical field value through a different
    # source state. The web commit must not report a successful no-op: the
    # reviewed candidate belonged to the earlier source snapshot.
    note.write_text(
        """---
type: Note
status: done
---
Body changed elsewhere
""",
        encoding="utf-8",
    )
    external = note.read_text(encoding="utf-8")

    result = apply_bundle(
        str(tmp_path),
        sql=sql,
        write=True,
        expected_preview_token=token,
    )

    assert result["succeeded"] is False
    assert result["written"] is False
    assert result["error"] == "apply candidate no longer matches the reviewed preview"
    assert note.read_text(encoding="utf-8") == external
