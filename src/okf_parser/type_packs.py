"""Discover and install opt-in type packs made from ordinary OKF resources."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from importlib.resources import files
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from importlib.resources.abc import Traversable

PACK_ENTRY_POINT_GROUP = "okf_parser.packs"


@dataclass(frozen=True, slots=True)
class PackFile:
    """One UTF-8 file a type pack materializes into a consumer bundle."""

    path: str
    content: str


@dataclass(frozen=True, slots=True)
class TypePack:
    """Metadata plus ordinary OKF files for one opt-in domain pack."""

    name: str
    version: str
    description: str
    types: tuple[str, ...]
    files: tuple[PackFile, ...]
    standard: str | None = None
    standard_version: str | None = None
    standard_schema: str | None = None

    def summary(self) -> dict[str, object]:
        """Return deterministic JSON-ready pack metadata."""
        result: dict[str, object] = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "types": list(self.types),
            "files": [item.path for item in self.files],
        }
        if self.standard is not None:
            result["standard"] = self.standard
        if self.standard_version is not None:
            result["standard_version"] = self.standard_version
        if self.standard_schema is not None:
            result["standard_schema"] = self.standard_schema
        return result


def _walk_text_files(root: Traversable, *, prefix: str = "") -> tuple[PackFile, ...]:
    """Read one packaged resource tree into deterministic relative text files."""
    collected: list[PackFile] = []
    for child in sorted(root.iterdir(), key=lambda item: item.name):
        relative = f"{prefix}/{child.name}" if prefix else child.name
        if child.is_dir():
            collected.extend(_walk_text_files(child, prefix=relative))
        elif child.is_file():
            collected.append(PackFile(relative, child.read_text(encoding="utf-8")))
    return tuple(collected)


def _packaged_files(resource_path: str, *, destination_prefix: str = "") -> tuple[PackFile, ...]:
    """Read files under ``okf_parser/<resource_path>`` from source or an installed wheel."""
    relative = PurePosixPath(resource_path)
    if relative.is_absolute() or ".." in relative.parts:
        msg = f"pack resource path must stay inside okf_parser: {resource_path!r}"
        raise ValueError(msg)
    root = files("okf_parser").joinpath(*relative.parts)
    if not root.is_dir():
        msg = f"pack resource directory is missing: {resource_path!r}"
        raise ValueError(msg)
    resources = _walk_text_files(root)
    if not destination_prefix:
        return resources
    prefix = PurePosixPath(destination_prefix)
    if prefix.is_absolute() or ".." in prefix.parts:
        msg = f"pack destination prefix must be relative: {destination_prefix!r}"
        raise ValueError(msg)
    return tuple(PackFile(str(prefix / item.path), item.content) for item in resources)


def journalism_pack() -> TypePack:
    """Return the journalism starter built by dogfooding IPTC ninjs examples through init."""
    resources = _packaged_files("packs/journalism")
    profile_files = tuple(
        item
        for item in resources
        if item.path.endswith("-mapping.json")
        or item.path.startswith("specs/")
        or item.path.startswith("standards/")
    )
    return TypePack(
        name="journalism",
        version="1",
        description="Journalism types based on the IPTC ninjs 3.2 news model.",
        types=("Body", "NewsItem"),
        files=profile_files,
        standard="IPTC ninjs",
        standard_version="3.2",
        standard_schema="https://www.iptc.org/std/ninjs/ninjs-schema_3.2.json",
    )


def _registered_entry_points() -> tuple[EntryPoint, ...]:
    """Return registered pack factories in deterministic order."""
    points = entry_points(group=PACK_ENTRY_POINT_GROUP)
    return tuple(sorted(points, key=lambda point: (point.name, point.value)))


def _load_entry_point(point: EntryPoint) -> TypePack:
    """Load one pack factory and require its declared identity to match metadata."""
    loaded = point.load()
    if not callable(loaded):
        msg = f"type-pack entry point {point.name!r} is not callable"
        raise TypeError(msg)
    factory = cast("Callable[[], TypePack]", loaded)
    pack = factory()
    if not isinstance(pack, TypePack):
        msg = f"type-pack entry point {point.name!r} did not return TypePack"
        raise TypeError(msg)
    if pack.name != point.name:
        msg = f"type-pack entry point {point.name!r} loaded pack {pack.name!r}"
        raise ValueError(msg)
    return pack


def list_type_packs() -> list[dict[str, object]]:
    """List every opt-in type pack registered in installed package metadata."""
    return [_load_entry_point(point).summary() for point in _registered_entry_points()]


def get_type_pack(name: str) -> TypePack:
    """Resolve exactly one installed type pack by name."""
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
    """Resolve a destination while refusing path or symlink escape."""
    relative = PurePosixPath(relative_path)
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        msg = f"pack file path must be relative and contained: {relative_path!r}"
        raise ValueError(msg)
    target = root.joinpath(*relative.parts)
    resolved_root = root.resolve(strict=False)
    resolved_target = target.resolve(strict=False)
    if not resolved_target.is_relative_to(resolved_root):
        msg = f"pack target escapes destination through a symlink: {relative_path!r}"
        raise ValueError(msg)
    return target


def install_type_pack(
    name: str,
    destination: str | Path,
    *,
    write: bool = False,
) -> dict[str, object]:
    """Preview or copy a pack's ordinary spec/schema files without overwriting authored work."""
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
        by_path = {item.path: item for item in pack.files}
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
