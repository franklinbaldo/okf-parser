# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "okf-parser>=0.45.4,<0.46",
#   "packaging>=26,<27",
# ]
# ///
# ruff: noqa: T201
"""Project PEP 621 project metadata into an existing codebase OKF bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from okf_parser import validate_path
from packaging.requirements import InvalidRequirement, Requirement

PROJECT_TYPE = "CodeProject"
DEPENDENCY_TYPE = "CodeDependency"
GENERATED_BY = "codebase-to-okf"
MANIFEST_NAME = "pyproject.toml"


class ManifestError(ValueError):
    """Expected user-facing failure while projecting a Python project manifest."""


@dataclass(frozen=True)
class DependencyRecord:
    """One dependency declaration parsed from PEP 621 metadata."""

    group: str
    declaration: str
    name: str
    extras: tuple[str, ...]
    specifier: str
    marker: str
    url: str


def _fail(message: str) -> None:
    """Raise an expected manifest projection failure."""
    raise ManifestError(message)


def _frontmatter(fields: dict[str, object]) -> str:
    """Render deterministic JSON frontmatter accepted by the OKF YAML parser."""
    return json.dumps(fields, ensure_ascii=False, indent=2, sort_keys=True)


def _slug(value: str) -> str:
    """Return a small filename-safe slug for human-readable generated paths."""
    cleaned = "".join(char.lower() if char.isalnum() else "-" for char in value)
    compact = "-".join(part for part in cleaned.split("-") if part)
    return compact[:72] or "item"


def _stable_filename(prefix: str, label: str, identity: str) -> str:
    """Return a deterministic collision-resistant generated Markdown filename."""
    digest = hashlib.sha256(identity.encode()).hexdigest()[:10]
    return f"{prefix}-{_slug(label)}-{digest}.md"


def _parse_requirement(group: str, declaration: str) -> DependencyRecord:
    """Parse one PEP 508 requirement while preserving its authored declaration."""
    try:
        requirement = Requirement(declaration)
    except InvalidRequirement as exc:
        _fail(f"invalid dependency declaration in {group!r}: {declaration!r}: {exc}")
    return DependencyRecord(
        group=group,
        declaration=declaration,
        name=requirement.name,
        extras=tuple(sorted(requirement.extras)),
        specifier=str(requirement.specifier),
        marker=str(requirement.marker or ""),
        url=requirement.url or "",
    )


def _dependency_records(project: dict[str, object]) -> tuple[DependencyRecord, ...]:
    """Collect PEP 621 runtime and optional dependency declarations in stable order."""
    records: list[DependencyRecord] = []
    dependencies = project.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(
        isinstance(item, str) for item in dependencies
    ):
        _fail("[project].dependencies must be an array of PEP 508 strings")
    records.extend(_parse_requirement("runtime", item) for item in dependencies)

    optional = project.get("optional-dependencies", {})
    if not isinstance(optional, dict):
        _fail("[project].optional-dependencies must be a table")
    for group in sorted(optional):
        values = optional[group]
        if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
            _fail(
                f"[project].optional-dependencies.{group} must be an array of PEP 508 strings"
            )
        records.extend(_parse_requirement(f"optional:{group}", item) for item in values)
    return tuple(records)


def _render_dependency(
    project_name: str,
    project_path: str,
    record: DependencyRecord,
) -> str:
    """Render one manifest-level dependency declaration as an OKF concept."""
    fields: dict[str, object] = {
        "type": DEPENDENCY_TYPE,
        "title": f"{record.name} ({record.group})",
        "ecosystem": "python",
        "manifest_path": MANIFEST_NAME,
        "project": project_name,
        "project_concept": project_path,
        "group": record.group,
        "declaration": record.declaration,
        "dependency_name": record.name,
        "resolution": "manifest-declared",
        "generated_by": GENERATED_BY,
    }
    if record.extras:
        fields["extras"] = list(record.extras)
    if record.specifier:
        fields["specifier"] = record.specifier
    if record.marker:
        fields["marker"] = record.marker
    if record.url:
        fields["url"] = record.url

    project_link = PurePosixPath("..").joinpath(project_path).as_posix()
    body = [
        f"# {record.name} ({record.group})",
        "",
        f"Declared by [{project_name}]({project_link}) in `{MANIFEST_NAME}`.",
        "",
        f"Authored requirement: `{record.declaration}`.",
        "",
        "Resolution status: `manifest-declared`.",
        "",
        "This concept proves only that the dependency is declared in project metadata. It does ",
        "not prove installation, import, reachability, or runtime use.",
    ]
    return f"---\n{_frontmatter(fields)}\n---\n\n{'\n'.join(body)}\n"


def _render_project(
    project: dict[str, object],
    dependencies: tuple[DependencyRecord, ...],
    dependency_paths: tuple[str, ...],
) -> str:
    """Render the project manifest summary and links to dependency declarations."""
    name = str(project.get("name") or "python-project")
    fields: dict[str, object] = {
        "type": PROJECT_TYPE,
        "title": name,
        "ecosystem": "python",
        "manifest_path": MANIFEST_NAME,
        "name": name,
        "generated_by": GENERATED_BY,
    }
    for key in ("version", "description", "requires-python"):
        value = project.get(key)
        if isinstance(value, str) and value:
            fields[key.replace("-", "_")] = value
    dynamic = project.get("dynamic")
    if (
        isinstance(dynamic, list)
        and all(isinstance(item, str) for item in dynamic)
        and dynamic
    ):
        fields["dynamic_fields"] = dynamic
    groups = list(dict.fromkeys(record.group for record in dependencies))
    if groups:
        fields["dependency_groups"] = groups

    body = [
        f"# {name}",
        "",
        f"Project metadata observed in `{MANIFEST_NAME}`.",
        "",
        "This is authored manifest evidence, not an environment snapshot.",
    ]
    if dependency_paths:
        body.extend(["", "## Declared dependencies", ""])
        for record, path in zip(dependencies, dependency_paths, strict=True):
            link = PurePosixPath("..").joinpath(path).as_posix()
            body.append(f"- [{record.name} — {record.group}]({link})")
    return f"---\n{_frontmatter(fields)}\n---\n\n{'\n'.join(body)}\n"


def _is_own_concept(path: Path, expected_type: str) -> bool:
    """Return whether a generated Markdown file belongs to this metadata projector."""
    try:
        text = path.read_text(encoding="utf-8")
        _, raw_frontmatter, _ = text.split("---", 2)
        fields = json.loads(raw_frontmatter.strip())
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    return fields.get("type") == expected_type and fields.get("generated_by") == GENERATED_BY


def _clear_previous(root: Path) -> None:
    """Remove only prior manifest concepts generated by this recipe."""
    generated_dirs = (("project", PROJECT_TYPE), ("dependencies", DEPENDENCY_TYPE))
    for directory, expected_type in generated_dirs:
        target = root / directory
        if not target.is_dir():
            continue
        for path in sorted(target.glob("*.md")):
            if _is_own_concept(path, expected_type):
                path.unlink()


def _load_project(source: Path) -> dict[str, object] | None:
    """Load the standard PEP 621 project table, returning None when absent."""
    manifest = source / MANIFEST_NAME
    if not manifest.is_file():
        return None
    try:
        payload = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        _fail(f"cannot parse {MANIFEST_NAME}: {exc}")
    project = payload.get("project")
    if project is None:
        return None
    if not isinstance(project, dict):
        _fail("[project] must be a TOML table")
    return project


def _write_projection(source: Path, root: Path) -> tuple[int, int, list[str]]:
    """Write project and dependency concepts, returning counts and groups."""
    project = _load_project(source)
    _clear_previous(root)
    if project is None:
        return 0, 0, []

    dependencies = _dependency_records(project)
    project_name = str(project.get("name") or "python-project")
    identity = f"{MANIFEST_NAME}:{project_name}"
    project_filename = _stable_filename("project", project_name, identity)
    project_path = f"project/{project_filename}"

    dependency_paths: list[str] = []
    (root / "dependencies").mkdir(parents=True, exist_ok=True)
    for record in dependencies:
        identity = f"{project_name}:{record.group}:{record.declaration}"
        filename = _stable_filename("dependency", record.name, identity)
        relative = f"dependencies/{filename}"
        (root / relative).write_text(
            _render_dependency(project_name, project_path, record),
            encoding="utf-8",
        )
        dependency_paths.append(relative)

    (root / "project").mkdir(parents=True, exist_ok=True)
    (root / project_path).write_text(
        _render_project(project, dependencies, tuple(dependency_paths)),
        encoding="utf-8",
    )
    groups = list(dict.fromkeys(record.group for record in dependencies))
    return 1, len(dependencies), groups


def build_parser() -> argparse.ArgumentParser:
    """Build the project-manifest projection CLI."""
    parser = argparse.ArgumentParser(
        description="Project PEP 621 project metadata into an existing codebase OKF bundle."
    )
    parser.add_argument("source", type=Path, help="Python project/source root")
    parser.add_argument("bundle", type=Path, help="existing generated codebase OKF bundle")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Project manifest declarations without claiming installation or runtime use."""
    args = build_parser().parse_args(argv)
    source = args.source.resolve()
    root = args.bundle.resolve()
    try:
        if not source.is_dir():
            _fail(f"source is not a directory: {source}")
        if not root.is_dir():
            _fail(f"bundle is not a directory: {root}")
        before = validate_path(root)
        if not before.is_conformant:
            _fail("bundle must be conformant before manifest projection")
        projects, dependencies, groups = _write_projection(source, root)
        after = validate_path(root)
        if not after.is_conformant:
            _fail("bundle is not conformant after manifest projection")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            {
                "manifest": MANIFEST_NAME if projects else None,
                "projects": projects,
                "dependencies": dependencies,
                "dependency_groups": groups,
                "concepts": projects + dependencies,
                "conformant": True,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
