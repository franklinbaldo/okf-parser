"""JSON-ready WikiSkill application services shared by CLI and MCP."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

from okf_parser.engine import load_bundle
from okf_parser.wikiskill import wikiskill_view

if TYPE_CHECKING:
    from collections.abc import Sequence

WikiSkillAction = Literal["inventory", "pending", "lineage"]


def wikiskill_bundle(
    path: str,
    action: WikiSkillAction = "inventory",
    *,
    concept: str | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, object]:
    """Inspect a WikiSkill-shaped bundle using canonical OKF relations."""
    bundle = load_bundle(Path(path), exclude)
    view = wikiskill_view(bundle)
    if action == "inventory":
        return {"root": str(bundle.root), "inventory": view.inventory().as_dict()}
    if action == "pending":
        return {
            "root": str(bundle.root),
            "unevaluated_proposals": [
                item.as_dict() for item in view.unevaluated_proposals()
            ],
        }
    if concept is None:
        msg = "wikiskill lineage requires --concept"
        raise ValueError(msg)
    return {
        "root": str(bundle.root),
        "concept": concept,
        "lineage": [item.as_dict() for item in view.lineage(concept)],
    }
