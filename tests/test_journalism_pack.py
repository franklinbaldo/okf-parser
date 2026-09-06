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
COMPLETE_EXAMPLE = "packs/journalism/examples/complete-profile.md"


def _resource_json(path: str) -> dict[str, object]:
    text = files("okf_parser").joinpath(*path.split("/")).read_text(encoding="utf-8")
    return cast("dict[str, object]", json.loads(text))


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


def _project_complete_example() -> dict[str, object]:
    root = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    source = files("okf_parser").joinpath(*COMPLETE_EXAMPLE.split("/")).read_text(encoding="utf-8")
    parsed = parse_document_text(Path("complete-profile.md"), source)
    properties = _root_properties(root)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])

    projected: dict[str, object] = {}
    for ninjs_name, rule in rules.items():
        mode = rule["mode"]
        if mode == "projected":
            body = parsed.body.strip()
            if body:
                projected[ninjs_name] = [
                    {"contentType": cast("str", rule["contentType"]), "value": body}
                ]
            continue
        okf_name = cast("str", rule["okf"])
        if okf_name in parsed.frontmatter:
            projected[ninjs_name] = _coerce(
                parsed.frontmatter[okf_name],
                properties[ninjs_name],
                root=root,
                geojson=geojson,
            )
    return projected


def test_ninjs_mapping_covers_every_official_3_2_root_property() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    properties = _root_properties(schema)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])

    assert schema["title"] == "IPTC ninjs - News in JSON - version 3.2"
    assert set(rules) == set(properties)
    assert len(rules) == 41
    assert {name for name, rule in rules.items() if rule["mode"] == "renamed"} == {"type"}
    assert rules["type"]["okf"] == "ninjs_type"
    assert {name for name, rule in rules.items() if rule["mode"] == "projected"} == {"bodies"}
    assert rules["bodies"]["okf"] == "$body"

    for name, property_schema in properties.items():
        if property_schema.get("type") in {"array", "object"} and name != "bodies":
            assert rules[name].get("nested") == "verbatim"


def test_complete_fixture_authors_every_non_projected_ninjs_property() -> None:
    mapping = _resource_json(MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    source = files("okf_parser").joinpath(*COMPLETE_EXAMPLE.split("/")).read_text(encoding="utf-8")
    parsed = parse_document_text(Path("complete-profile.md"), source)

    expected_okf = {
        cast("str", rule["okf"]) for rule in rules.values() if rule["mode"] != "projected"
    }
    assert expected_okf <= set(parsed.frontmatter)
    assert set(parsed.frontmatter) == expected_okf | {"type"}
    assert parsed.concept_type == "NewsItem"
    assert parsed.body.strip()


def test_complete_fixture_projects_to_valid_official_ninjs_3_2() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    registry = Registry().with_resource(
        cast("str", geojson["$id"]), Resource.from_contents(geojson)
    )
    validator = Draft202012Validator(
        schema,
        registry=registry,
        format_checker=FormatChecker(),
    )

    errors = sorted(
        validator.iter_errors(_project_complete_example()),
        key=lambda item: list(item.path),
    )
    assert errors == [], "\n".join(
        f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in errors
    )


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
