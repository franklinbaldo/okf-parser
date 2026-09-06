"""Conformance gates for the standards-backed journalism type pack."""

from __future__ import annotations

import json
import shutil
from importlib.resources import files
from pathlib import Path
from typing import cast

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from okf_parser import concept, load_bundle, resolve_relations
from okf_parser.models import ConceptRecord
from okf_parser.parser import parse_document_text

NINJS_RESOURCE = "packs/journalism/standards/ninjs-schema_3.2.json"
GEOJSON_RESOURCE = "packs/journalism/standards/GeoJSON.json"
MAPPING_RESOURCE = "packs/journalism/ninjs-mapping.json"
BODY_MAPPING_RESOURCE = "packs/journalism/body-mapping.json"
NEWSITEM_SCHEMA_RESOURCE = "packs/journalism/specs/newsitem.schema.sql"
BODY_SCHEMA_RESOURCE = "packs/journalism/specs/body.schema.sql"
COMPLETE_EXAMPLE = "packs/journalism/examples/complete-profile.md"
MINIMAL_EXAMPLE = "packs/journalism/examples/minimal-text.md"
BODY_EXAMPLES = (
    "packs/journalism/examples/body-main.md",
    "packs/journalism/examples/body-background.md",
)


def _resource_text(path: str) -> str:
    return files("okf_parser").joinpath(*path.split("/")).read_text(encoding="utf-8")


def _resource_json(path: str) -> dict[str, object]:
    return cast("dict[str, object]", json.loads(_resource_text(path)))


def _root_properties(schema: dict[str, object]) -> dict[str, dict[str, object]]:
    definitions = cast("dict[str, object]", schema["$defs"])
    ninjs_type = cast("dict[str, object]", definitions["ninjsType"])
    return cast("dict[str, dict[str, object]]", ninjs_type["properties"])


def _body_schema(schema: dict[str, object]) -> dict[str, object]:
    bodies = _root_properties(schema)["bodies"]
    return cast("dict[str, object]", bodies["items"])


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


def _project_body(
    record: ConceptRecord,
    *,
    schema: dict[str, object],
    geojson: dict[str, object],
) -> dict[str, object]:
    body_mapping = _resource_json(BODY_MAPPING_RESOURCE)
    body_schema = _body_schema(schema)
    properties = cast("dict[str, dict[str, object]]", body_schema["properties"])
    rules = cast("dict[str, dict[str, object]]", body_mapping["properties"])
    projected: dict[str, object] = {}

    for ninjs_name, rule in rules.items():
        if rule["mode"] == "projected":
            projected[ninjs_name] = record.body.strip()
            continue
        okf_name = cast("str", rule["okf"])
        if okf_name in record.frontmatter:
            projected[ninjs_name] = _coerce(
                record.frontmatter[okf_name],
                properties[ninjs_name],
                root=schema,
                geojson=geojson,
            )
    return projected


def _copy_fixture_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "journalism"
    examples = root / "examples"
    examples.mkdir(parents=True)
    (root / "index.md").write_text("# Journalism fixture\n", encoding="utf-8")
    resources = (COMPLETE_EXAMPLE, *BODY_EXAMPLES)
    for resource_path in resources:
        target = examples / Path(resource_path).name
        target.write_text(_resource_text(resource_path), encoding="utf-8")
    return root


def _project_complete_bundle(tmp_path: Path) -> dict[str, object]:
    root_schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    bundle = load_bundle(_copy_fixture_bundle(tmp_path), engine="native")
    item = concept(bundle, "examples/complete-profile.md")
    properties = _root_properties(root_schema)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    projected: dict[str, object] = {}

    for ninjs_name, rule in rules.items():
        okf_name = cast("str", rule["okf"])
        if rule["mode"] == "relation":
            related = resolve_relations(
                bundle,
                item,
                field=okf_name,
                resource_key=cast("str", rule["resourceKey"]),
                target_type=cast("str", rule["targetType"]),
            )
            projected[ninjs_name] = [
                _project_body(record, schema=root_schema, geojson=geojson)
                for record in related
            ]
            continue
        if okf_name in item.frontmatter:
            projected[ninjs_name] = _coerce(
                item.frontmatter[okf_name],
                properties[ninjs_name],
                root=root_schema,
                geojson=geojson,
            )
    return projected


def _project_simple_example(resource_path: str) -> dict[str, object]:
    root_schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    parsed = parse_document_text(Path(Path(resource_path).name), _resource_text(resource_path))
    properties = _root_properties(root_schema)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    projected: dict[str, object] = {}

    for ninjs_name, rule in rules.items():
        okf_name = cast("str", rule["okf"])
        if rule["mode"] == "relation":
            body = parsed.body.strip()
            if okf_name not in parsed.frontmatter and body:
                projected[ninjs_name] = [
                    {
                        "contentType": cast("str", rule["fallbackContentType"]),
                        "value": body,
                    }
                ]
            continue
        if okf_name in parsed.frontmatter:
            projected[ninjs_name] = _coerce(
                parsed.frontmatter[okf_name],
                properties[ninjs_name],
                root=root_schema,
                geojson=geojson,
            )
    return projected


def _validation_errors(document: dict[str, object]) -> list[str]:
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
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    return [
        f"{'.'.join(str(part) for part in error.path) or '<root>'}: {error.message}"
        for error in errors
    ]


def _sql_columns(resource_path: str) -> set[str]:
    return {
        line.strip().split('"')[1]
        for line in _resource_text(resource_path).splitlines()
        if line.strip().startswith('"')
    }


def test_ninjs_mapping_covers_every_official_3_2_root_property() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    geojson = _resource_json(GEOJSON_RESOURCE)
    mapping = _resource_json(MAPPING_RESOURCE)
    properties = _root_properties(schema)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])

    assert schema["title"] == "IPTC ninjs - News in JSON - version 3.2"
    assert set(rules) == set(properties)
    assert len(rules) == 41
    assert {rule["mode"] for rule in rules.values()} == {"direct", "renamed", "relation"}
    assert {name for name, rule in rules.items() if rule["mode"] == "renamed"} == {"type"}
    assert rules["type"]["okf"] == "ninjs_type"
    assert {name for name, rule in rules.items() if rule["mode"] == "relation"} == {"bodies"}
    assert rules["bodies"]["okf"] == "bodies"
    assert rules["bodies"]["targetType"] == "Body"
    assert rules["bodies"]["fallback"] == "$body"

    for name, property_schema in properties.items():
        resolved = _resolve_schema(property_schema, root=schema, geojson=geojson)
        if resolved.get("type") in {"array", "object"} and name != "bodies":
            assert rules[name].get("nested") == "shape-preserving"


def test_body_mapping_covers_every_official_body_property() -> None:
    schema = _resource_json(NINJS_RESOURCE)
    body_mapping = _resource_json(BODY_MAPPING_RESOURCE)
    body_schema = _body_schema(schema)
    properties = cast("dict[str, dict[str, object]]", body_schema["properties"])
    rules = cast("dict[str, dict[str, object]]", body_mapping["properties"])

    assert set(rules) == set(properties)
    assert set(rules) == {"role", "contentType", "charCount", "wordCount", "value"}
    assert rules["value"] == {"mode": "projected", "okf": "$body"}


def test_complete_fixture_authors_every_mapped_ninjs_property() -> None:
    mapping = _resource_json(MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    parsed = parse_document_text(Path("complete-profile.md"), _resource_text(COMPLETE_EXAMPLE))

    expected_okf = {cast("str", rule["okf"]) for rule in rules.values()}
    assert len(expected_okf) == 41
    assert expected_okf <= set(parsed.frontmatter)
    assert set(parsed.frontmatter) == expected_okf | {"type"}
    assert parsed.concept_type == "NewsItem"
    assert not parsed.body.strip()


def test_generated_newsitem_schema_has_every_mapped_root_property() -> None:
    mapping = _resource_json(MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", mapping["properties"])
    expected = {cast("str", rule["okf"]) for rule in rules.values()}

    assert len(expected) == 41
    assert _sql_columns(NEWSITEM_SCHEMA_RESOURCE) == expected


def test_generated_body_schema_has_every_authored_body_metadata_field() -> None:
    body_mapping = _resource_json(BODY_MAPPING_RESOURCE)
    rules = cast("dict[str, dict[str, object]]", body_mapping["properties"])
    expected = {
        cast("str", rule["okf"])
        for rule in rules.values()
        if rule["mode"] != "projected"
    }

    assert _sql_columns(BODY_SCHEMA_RESOURCE) == expected


def test_newsitem_resolves_multiple_body_concepts(tmp_path: Path) -> None:
    bundle = load_bundle(_copy_fixture_bundle(tmp_path), engine="native")
    resolved = resolve_relations(
        bundle,
        "examples/complete-profile.md",
        field="bodies",
        target_type="Body",
    )

    assert [item.concept_id for item in resolved] == [
        "examples/body-main",
        "examples/body-background",
    ]
    assert all(item.body.strip() for item in resolved)


def test_complete_fixture_projects_linked_bodies_to_valid_ninjs_3_2(tmp_path: Path) -> None:
    projected = _project_complete_bundle(tmp_path)
    bodies = cast("list[dict[str, object]]", projected["bodies"])

    assert [body["role"] for body in bodies] == ["main", "background"]
    assert all(isinstance(body["value"], str) and body["value"] for body in bodies)
    assert _validation_errors(projected) == []


def test_markdown_body_is_a_valid_single_body_fallback() -> None:
    projected = _project_simple_example(MINIMAL_EXAMPLE)
    body = cast("list[dict[str, object]]", projected["bodies"])

    assert body == [
        {
            "contentType": "text/markdown",
            "value": "Este é um exemplo mínimo de conteúdo textual para o pack `journalism`.",
        }
    ]
    assert _validation_errors(projected) == []


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
