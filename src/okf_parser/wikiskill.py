"""Optional WikiSkill ergonomics built on the canonical OKF relational bundle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

import networkx as nx

if TYPE_CHECKING:
    from collections.abc import Iterable

    from okf_parser.bundle import Bundle

_EXPERIENCE_TYPES: Final[frozenset[str]] = frozenset({"experience", "loop-run"})
_WIKI_TYPES: Final[frozenset[str]] = frozenset({"wiki-entry"})
_SKILL_TYPES: Final[frozenset[str]] = frozenset({"agent-skill"})
_PROPOSAL_TYPES: Final[frozenset[str]] = frozenset({"skill-proposal"})
_EVALUATION_TYPES: Final[frozenset[str]] = frozenset({"skill-evaluation"})


def _normalize_type(value: object) -> str:
    """Normalize one producer-defined concept type for profile matching."""
    return value.strip().casefold() if isinstance(value, str) else ""


@dataclass(frozen=True, slots=True)
class WikiSkillInventory:
    """Operational counts for a WikiSkill-shaped OKF bundle."""

    experiences: int
    wiki_entries: int
    skills: int
    proposals: int
    evaluations: int
    orphan_wiki_entries: int
    unevaluated_proposals: int

    def as_dict(self) -> dict[str, int]:
        """Return a stable JSON-friendly representation."""
        return {
            "evaluations": self.evaluations,
            "experiences": self.experiences,
            "orphan_wiki_entries": self.orphan_wiki_entries,
            "proposals": self.proposals,
            "skills": self.skills,
            "unevaluated_proposals": self.unevaluated_proposals,
            "wiki_entries": self.wiki_entries,
        }


@dataclass(frozen=True, slots=True)
class WikiSkillConcept:
    """Minimal concept identity exposed by WikiSkill helpers."""

    concept_id: str
    path: str
    concept_type: str
    title: str | None

    def as_dict(self) -> dict[str, str | None]:
        """Return a stable JSON-friendly representation."""
        return {
            "concept_id": self.concept_id,
            "concept_type": self.concept_type,
            "path": self.path,
            "title": self.title,
        }


class WikiSkillView:
    """Read-only WikiSkill queries over an existing :class:`Bundle`.

    WikiSkill remains an optional profile: no storage, parser, graph, or registry
    is introduced here. All answers derive from ``Bundle.concepts``,
    ``Bundle.links`` and ``Bundle.to_networkx()``.
    """

    def __init__(self, bundle: Bundle) -> None:
        """Bind WikiSkill ergonomics to one already-loaded OKF bundle."""
        rows = bundle.concepts.execute().to_dict(orient="records")
        self._concepts = {
            str(row["concept_id"]): WikiSkillConcept(
                concept_id=str(row["concept_id"]),
                path=str(row["path"]),
                concept_type=str(row["concept_type"]),
                title=row["title"] if isinstance(row["title"], str) else None,
            )
            for row in rows
        }
        self._types = {
            concept_id: _normalize_type(concept.concept_type)
            for concept_id, concept in self._concepts.items()
        }
        self._graph = bundle.to_networkx()

    def _ids_of_type(self, accepted: frozenset[str]) -> set[str]:
        return {
            concept_id
            for concept_id, type_name in self._types.items()
            if type_name in accepted
        }

    def _concepts_for_ids(self, concept_ids: Iterable[str]) -> tuple[WikiSkillConcept, ...]:
        return tuple(
            sorted(
                (self._concepts[concept_id] for concept_id in concept_ids),
                key=lambda concept: concept.path,
            )
        )

    def orphan_wiki_entries(self) -> tuple[WikiSkillConcept, ...]:
        """Return wiki entries with no resolved incoming concept link."""
        wiki_ids = self._ids_of_type(_WIKI_TYPES)
        orphan_ids = {
            concept_id for concept_id in wiki_ids if self._graph.in_degree(concept_id) == 0
        }
        return self._concepts_for_ids(orphan_ids)

    def unevaluated_proposals(self) -> tuple[WikiSkillConcept, ...]:
        """Return skill proposals not connected to any skill evaluation."""
        proposal_ids = self._ids_of_type(_PROPOSAL_TYPES)
        evaluation_ids = self._ids_of_type(_EVALUATION_TYPES)
        unevaluated = {
            proposal_id
            for proposal_id in proposal_ids
            if not any(
                neighbor in evaluation_ids
                for neighbor in set(self._graph.predecessors(proposal_id))
                | set(self._graph.successors(proposal_id))
            )
        }
        return self._concepts_for_ids(unevaluated)

    def lineage(self, concept: str) -> tuple[WikiSkillConcept, ...]:
        """Return all resolved ancestors and descendants for a concept id or path."""
        concept_id = self._resolve_concept_id(concept)
        related = nx.ancestors(self._graph, concept_id) | nx.descendants(self._graph, concept_id)
        related.add(concept_id)
        return self._concepts_for_ids(related)

    def inventory(self) -> WikiSkillInventory:
        """Summarize the current WikiSkill operating state."""
        return WikiSkillInventory(
            experiences=len(self._ids_of_type(_EXPERIENCE_TYPES)),
            wiki_entries=len(self._ids_of_type(_WIKI_TYPES)),
            skills=len(self._ids_of_type(_SKILL_TYPES)),
            proposals=len(self._ids_of_type(_PROPOSAL_TYPES)),
            evaluations=len(self._ids_of_type(_EVALUATION_TYPES)),
            orphan_wiki_entries=len(self.orphan_wiki_entries()),
            unevaluated_proposals=len(self.unevaluated_proposals()),
        )

    def _resolve_concept_id(self, concept: str) -> str:
        if concept in self._concepts:
            return concept
        matches = [item.concept_id for item in self._concepts.values() if item.path == concept]
        if len(matches) == 1:
            return matches[0]
        msg = f"WikiSkill concept not found or ambiguous: {concept}"
        raise KeyError(msg)


def wikiskill_view(bundle: Bundle) -> WikiSkillView:
    """Create an optional WikiSkill read view over an OKF bundle."""
    return WikiSkillView(bundle)
