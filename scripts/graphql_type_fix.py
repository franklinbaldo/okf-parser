"""One-shot GraphQL type-narrowing patch for PR #165."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected exactly one anchor")
    target.write_text(text.replace(old, new), encoding="utf-8")


replace_once(
    "src/okf_parser/graphql_adapter.py",
    '''    elif isinstance(value, (date, datetime, UUID)):\n        result = value.isoformat() if hasattr(value, "isoformat") else str(value)\n''',
    '''    elif isinstance(value, datetime):\n        result = value.isoformat()\n    elif isinstance(value, date):\n        result = value.isoformat()\n    elif isinstance(value, UUID):\n        result = str(value)\n''',
)

replace_once(
    "tests/test_graphql_adapter.py",
    "from typing import TYPE_CHECKING\n",
    "from typing import TYPE_CHECKING, cast\n",
)
replace_once(
    "tests/test_graphql_adapter.py",
    '''    concepts = result.data["concepts"]\n    assert isinstance(concepts, list)\n    assert len(concepts) == 1\n    concept = concepts[0]\n    assert isinstance(concept, dict)\n''',
    '''    concepts = cast(list[dict[str, object]], result.data["concepts"])\n    assert len(concepts) == 1\n    concept = concepts[0]\n''',
)
replace_once(
    "tests/test_graphql_adapter.py",
    '''    assert concept["tags"] == ["um", "dois"]\n    assert sorted(link["exists"] for link in concept["links"]) == [False, True]\n    assert {diagnostic["code"] for diagnostic in concept["diagnostics"]} == {"OKF101"}\n\n    concept_id = concept["id"]\n''',
    '''    assert concept["tags"] == ["um", "dois"]\n    links = cast(list[dict[str, object]], concept["links"])\n    diagnostics = cast(list[dict[str, object]], concept["diagnostics"])\n    assert sorted(cast(bool, link["exists"]) for link in links) == [False, True]\n    assert {cast(str, diagnostic["code"]) for diagnostic in diagnostics} == {"OKF101"}\n\n    concept_id = cast(str, concept["id"])\n''',
)
