"""Install opt-in reusable OKF type packs registered by package metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from pathlib import Path, PurePosixPath
from typing import cast

PACK_ENTRY_POINT_GROUP = "okf_parser.packs"
NINJS_SCHEMA = "https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json"


@dataclass(frozen=True, slots=True)
class PackFile:
    """One UTF-8 text file materialized by a type pack."""

    path: str
    content: str

    def __post_init__(self) -> None:
        """Reject pack paths that could escape the selected destination."""
        relative = PurePosixPath(self.path)
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            msg = f"pack file path must be relative and contained: {self.path!r}"
            raise ValueError(msg)
        if not self.content.endswith("\n"):
            msg = f"pack file must end with one newline: {self.path}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class TypePack:
    """A versioned set of ordinary OKF specs and schemas."""

    name: str
    description: str
    version: str
    files: tuple[PackFile, ...]
    standard: str | None = None
    standard_version: str | None = None
    standard_schema: str | None = None

    def summary(self) -> dict[str, str]:
        """Return deterministic JSON-ready pack metadata."""
        result = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
        }
        if self.standard is not None:
            result["standard"] = self.standard
        if self.standard_version is not None:
            result["standard_version"] = self.standard_version
        if self.standard_schema is not None:
            result["standard_schema"] = self.standard_schema
        return result


type PackFactory = Callable[[], TypePack]


def _registered_entry_points() -> tuple[EntryPoint, ...]:
    """Return pack entry points in deterministic name/value order."""
    points = entry_points(group=PACK_ENTRY_POINT_GROUP)
    return tuple(sorted(points, key=lambda point: (point.name, point.value)))


def _load_entry_point(point: EntryPoint) -> TypePack:
    """Load one registered pack and verify its metadata identity."""
    factory = cast("PackFactory", point.load())
    pack = factory()
    if pack.name != point.name:
        msg = f"pack entry point {point.name!r} loaded mismatched pack {pack.name!r}"
        raise ValueError(msg)
    return pack


def list_type_packs() -> list[dict[str, str]]:
    """List every opt-in type pack registered by installed package metadata."""
    return [_load_entry_point(point).summary() for point in _registered_entry_points()]


def get_type_pack(name: str) -> TypePack:
    """Resolve one registered pack by exact name."""
    matches = [point for point in _registered_entry_points() if point.name == name]
    if len(matches) == 1:
        return _load_entry_point(matches[0])
    if len(matches) > 1:
        msg = f"multiple type packs are registered as {name!r}"
        raise ValueError(msg)
    available = ", ".join(point.name for point in _registered_entry_points()) or "none"
    msg = f"unknown type pack {name!r}; available packs: {available}"
    raise ValueError(msg)


def _safe_target(root: Path, relative_path: str) -> Path:
    """Resolve one pack path while refusing symlink/path traversal outside root."""
    relative = PurePosixPath(relative_path)
    target = root.joinpath(*relative.parts)
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_root):
        msg = f"pack target escapes destination through a symlink: {relative_path}"
        raise ValueError(msg)
    return target


def install_type_pack(
    name: str, destination: str | Path, *, write: bool = False
) -> dict[str, object]:
    """Preview or materialize one pack without overwriting authored files."""
    pack = get_type_pack(name)
    root = Path(destination)
    if root.exists() and not root.is_dir():
        msg = f"pack destination is not a directory: {root}"
        raise ValueError(msg)

    planned: list[str] = []
    unchanged: list[str] = []
    collisions: list[str] = []
    targets: dict[str, Path] = {}

    for pack_file in sorted(pack.files, key=lambda item: item.path):
        target = _safe_target(root, pack_file.path)
        targets[pack_file.path] = target
        if not target.exists():
            planned.append(pack_file.path)
            continue
        if not target.is_file():
            collisions.append(pack_file.path)
            continue
        try:
            existing = target.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            collisions.append(pack_file.path)
            continue
        if existing == pack_file.content:
            unchanged.append(pack_file.path)
        else:
            collisions.append(pack_file.path)

    written: list[str] = []
    if write and not collisions:
        root.mkdir(parents=True, exist_ok=True)
        by_path = {pack_file.path: pack_file for pack_file in pack.files}
        for relative_path in planned:
            target = targets[relative_path]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(by_path[relative_path].content, encoding="utf-8", newline="\n")
            written.append(relative_path)

    return {
        "pack": pack.summary(),
        "destination": str(root),
        "write": write,
        "planned": planned,
        "unchanged": unchanged,
        "collisions": collisions,
        "written": written,
    }


_NEWS_ITEM_SPEC = """---
type: Spec
title: NewsItem
description: OKF profile for an IPTC ninjs 3.2 news object
standard: IPTC ninjs
standard_version: \"3.2\"
standard_schema: https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json
---

# NewsItem

`NewsItem` is the OKF authoring profile for an IPTC ninjs 3.2 news object. The
standard remains the semantic authority; this profile only adapts the JSON model
to an OKF Markdown concept.

## Mapping rules

- `uri` keeps the ninjs meaning and is the globally unique identifier of the news
  object. ninjs 3.2 requires it.
- OKF already owns the frontmatter key `type`, so ninjs `type` is authored as
  `ninjs_type`. Its values are the ninjs nature values: `text`, `audio`, `video`,
  `picture`, `graphic`, `composite`, `component`, `event`, or `planning`.
- Other declared fields preserve the ninjs 3.2 property spelling and semantics.
- The Markdown body is the canonical textual body in OKF. A ninjs exporter may
  project it into `bodies`; authors should not duplicate the same text in
  frontmatter merely to imitate the JSON representation.
- Structured ninjs collections such as `headlines`, `infoSources`, `subjects`,
  and `associations` are stored as structured frontmatter and declared as JSON
  in the physical DuckDB schema.

## Sources and associations

Do not use `infoSources` as a generic list of documentary evidence. In ninjs it
means parties -- people or organisations -- that originated, modified,
enhanced, distributed, aggregated, supplied, or otherwise provided information
used by the news object.

`associations` relates this item to other news objects. A consumer that needs an
auditable observation/retrieval history, preservation state, claim-to-evidence
mapping, or other newsroom workflow data should model that as an explicit OKF
extension instead of changing the ninjs meaning of `infoSources`.

## Interoperability

This pack targets IPTC ninjs 3.2, whose official JSON Schema is:

<https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json>

The schema is published by IPTC under CC BY 4.0. The pack does not copy the IPTC
JSON Schema; it records the mapping and a DuckDB declaration for the OKF profile.
"""

_NEWS_ITEM_SCHEMA = """CREATE TABLE \"NewsItem\" (
    uri VARCHAR,
    ninjs_type VARCHAR,
    representationType VARCHAR,
    profile VARCHAR,
    version VARCHAR,
    firstCreated TIMESTAMPTZ,
    versionCreated TIMESTAMPTZ,
    contentCreated TIMESTAMPTZ,
    embargoedUntil TIMESTAMPTZ,
    pubStatus VARCHAR,
    urgency INTEGER,
    language VARCHAR,
    title VARCHAR,
    by VARCHAR,
    slugline VARCHAR,
    located VARCHAR,
    headlines JSON,
    descriptions JSON,
    infoSources JSON,
    subjects JSON,
    people JSON,
    organisations JSON,
    places JSON,
    events JSON,
    associations JSON,
    genres JSON,
    trustIndicators JSON,
    digitalSourceType JSON,
    standard JSON
);

COMMENT ON TABLE \"NewsItem\" IS
    'OKF authoring profile for IPTC ninjs 3.2 news objects.';
COMMENT ON COLUMN \"NewsItem\".uri IS
    'Maps to ninjs uri; the ninjs 3.2 standard requires it.';
COMMENT ON COLUMN \"NewsItem\".ninjs_type IS
    'Maps to ninjs type; renamed because OKF reserves frontmatter type.';
COMMENT ON COLUMN \"NewsItem\".infoSources IS
    'Maps to ninjs infoSources: parties that supplied or contributed information.';
COMMENT ON COLUMN \"NewsItem\".associations IS
    'Maps to ninjs associations: other news objects associated with this item.';
"""


def journalism_pack() -> TypePack:
    """Return the built-in journalism pack based on IPTC ninjs 3.2."""
    return TypePack(
        name="journalism",
        version="1",
        description="Journalism types based on the IPTC ninjs 3.2 news model.",
        standard="IPTC ninjs",
        standard_version="3.2",
        standard_schema=NINJS_SCHEMA,
        files=(
            PackFile("specs/news-item.md", _NEWS_ITEM_SPEC),
            PackFile("specs/news-item.schema.sql", _NEWS_ITEM_SCHEMA),
        ),
    )
