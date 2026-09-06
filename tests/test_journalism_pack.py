"""Conformance gates for the standards-backed journalism type pack."""

from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from okf_parser.parser import parse_document_text

NINJS_RESOURCE = "packs/journalism/standards/ninjs-schema_3.2.json"
GEOJSON_RESOURCE = "packs/journalism/standards/GeoJSON.json"
MAPPING_RESOURCE = "packs/journalism/ninjs-mapping.json"
SCHEMA_RESOURCE = "packs/journalism/specs/newsitem.schema.sql"
COMPLETE_EXAMPLE = "packs/journalism/examples/complete-profile.md"
MINIMAL_EXAMPLE = "packs/journalism/examples/minimal-text.md"


def _resource_text(path: str) -> str:
    return files("okf_parser").joinpath(*path.split("/")).read_text(encoding="utf-8")


def _resource_json(path: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(_resource_text(path)))


def _root_properties(schema: dict[str, object]) -> dict[str, dict[str, object]]:
    definitions = cast("dict[str, object]", schema["$defs"])
    ninjs_type = cast("dict[str, object]", definitions["ninjsType"])
    return cast("dict[str, dict[str, object]]", ninjs_type["properties"])


def _merge_schema(target: dict[str, object], local: dict[str, object]) -> dict[str, object]:
    """Merge a resolved `$ref` with sibling constraints for lexical coercion only."""
    merged = dict(target)
    for key, value in local.items():
        if key == "$ref":
            continue
        if key == "properties" and isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = {**cast("dict[str, object]", merged[key]), **value}
        elif key == "required" and isinstance(merged.get(key), list) and isinstance(value, list):
            merged[key] = list(dict.fromkeys([*cast("list[object]", merged[key]), *value]))
        else:
            merged[key] = value
    return merged


def _resolve_schema(
    node: dict[str, object],
    *,
    root: dict[str, object],
    geojson: dict[str, object],
) -> dict[str, object]:
    reference = node.get("$ref")
    if not isinstance(reference, str):
        return node
    if reference.startswith("#/$defs/"):
        name = reference.removeprefix("#/$defs/")
        definitions = cast("dict[str, object]", root["$defs"])
        target = cast("dict[str, object]", definitions[name])
        return _merge_schema(target, node)
    if reference.rstrip("#") == "https://geojson.org/schema/GeoJSON.json":
        return _merge_schema(geojson, node)
    msg = f"unresolved schema reference in vendored ninjs: {reference}"
    raise AssertionError(msg)


def _coerce_geojson(value: object) -> object:
    """Restore JSON numeric scalars after OKF's lexical-scalar parser boundary."""
    if isinstance(value, list):
        return [_coerce_geojson(item) for item in value]
    if isinstance(value, dict):
        return {key: _coerce_geojson(item) for key, item in value.items()}
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _coerce(  # noqa: PLR0911
    value: object,
    schema: dict[str, object],
    *,
    root: dict[str, object],
    geojson: dict[str, object],
) -> object:
    """Project OKF's spelling-preserving scalars into the JSON types required by ninjs."""
    reference = schema.get("$ref")
    if (
        isinstance(reference, str)
        and reference.rstrip("#") == "https://geojson.org/schema/GeoJSON.json"
    ):
        return _coerce_geojson(value)
    resolved = _resolve_schema(schema, root=root, geojson=geojson)
    expected = resolved.get("type")

    if expected == "array":
        assert isinstance(value, list)
        item_schema = cast("dict[str, object]", resolved.get("items", {}))
        return [_coerce(item, item_schema, root=root, geojson=geojson) for item in value]
    if expected == "object":
        assert isinstance(value, dict)
        properties = cast("dict[str, dict[str, object]]", resolved.get("properties", {}))
        return {
            key: _coerce(item, properties.get(key, {}), root=root, geojson=geojson)
            for key, item in value.items()
        }
    if expected == "integer" and isinstance(value, str):
        return int(value)
    if expected == "number" and isinstance(value, str):
        return float(value)
    if expected == "boolean" and isinstance(value, str):
        lowered = value.lower()
        assert lowered in {"true", "false"}
        return lowered == "true"
    return value


def _project_example(resource_path: str) -> dict[str, object]:
    root = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    parsed = parse_document_text(Path(resource_path).name, _resource_text(resource_path))
    properties = _root_properties(root)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])

    projected: dict[str, object] = {}
    for ninjs_name, rule in rules.items():
        okf_name = cast("str", rule["okf"])
        if rule["mode"] == "hybrid" and okf_name not in parsed.frontmatter:
            body = parsed.body.strip()
            if body:
                projected[ninjs_name] = [
                    {"contentType": cast("str", rule["contentType"]), "value": body}
                ]
            continue
        if okf_name in parsed.frontmatter:
            projected[ninjs_name] = _coerce(
                parsed.frontmatter[okf_name],
                properties[ninjs_name],
                root=root,
                geojson=geojson,
            )
    return projected


def _validator() -> Draft202012Validator:
    schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    registry = Registry().with_resource(
        cast("str", geojson["$id"]), Resource.from_contents(geojson)
    )
    return Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def test_ninjs_mapping_covers_every_official_3_2_root_property() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    properties = _root_properties(schema)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])

    assert schema["title"] == "IPTC ninjs - News in JSON - version 3.2"
    assert set(rules) == set(properties)
    assert len(rules) == 41
    assert {rule["mode"] for rule in rules.values()} == {"direct", "renamed", "hybrid"}
    assert {name for name, rule in rules.items() if rule["mode"] == "renamed"} == {"type"}
    assert rules["type"]["okf"] == "ninjs_type"
    assert {name for name, rule in rules.items() if rule["mode"] == "hybrid"} == {"bodies"}
    assert rules["bodies"]["okf"] == "bodies"
    assert rules["bodies"]["fallback"] == "$body"

    for name, property_schema in properties.items():
        resolved = _resolve_schema(property_schema, root=schema, geojson=geojson)
        if resolved.get("type") in {"array", "object"}:
            assert rules[name].get("nested") == "shape-preserving"


def test_complete_fixture_authors_every_mapped_ninjs_property() -> None:
    mapping = _resource_json(MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    parsed = parse_document_text(Path("complete-profile.md"), _resource_text(COMPLETE_EXAMPLE))

    expected_okf = {cast("str", rule["okf"]) for rule in rules.values()}
    assert len(expected_okf) == 41
    assert expected_okf <= set(parsed.frontmatter)
    assert set(parsed.frontmatter) == expected_okf | {"type"}
    assert parsed.concept_type == "NewsItem"
    assert parsed.body.strip()


def test_generated_schema_has_one_column_for_every_mapped_ninjs_root_property() -> None:
    mapping = _resource_json(MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    expected = {cast("str", rule["okf"]) for rule in rules.values()}
    sql = _resource_text(SCHEMA_RESOURCE)
    columns = {
        line.strip().split('"')[1]
        for line in sql.splitlines()
        if line.strip().startswith('"')
    }

    assert len(expected) == 41
    assert columns == expected


def test_complete_fixture_projects_to_valid_official_ninjs_3_2() -> None:
    errors = sorted(
        _validator().iter_errors(_project_example(COMPLETE_EXAMPLE)),
        key=lambda item: list(item.path),
    )
    assert errors == [], "\n".join(
        f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in errors
    )


def test_markdown_body_is_a_valid_bodies_fallback() -> None:
    projected = _project_example(MINIMAL_EXAMPLE)
    body = cast("list[dict[str, object]]", projected["bodies"])

    assert body == [
        {
            "contentType": "text/markdown",
            "value": "Este é um exemplo mínimo de conteúdo textual para o pack `journalism`.",
        }
    ]
    assert list(_validator().iter_errors(projected)) == []


def test_vendored_ninjs_external_refs_are_closed_by_pack_resources() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    refs: set[str] = set()

    def collect(value: object) -> None:
        if isinstance(value, dict):
            reference = value.get("$ref")
            if isinstance(reference, str):
                refs.add(reference)
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(schema)
    internal = {ref for ref in refs if ref.startswith("#/$defs/")}
    external = {ref.rstrip("#") for ref in refs if not ref.startswith("#/")}
    definitions = cast("dict[str, object]", schema["$defs"])

    assert {ref.removeprefix("#/$defs/") for ref in internal} <= set(definitions)
    assert external == {cast("str", geojson["$id"])}
