# /// script
# requires-python = ">=3.12"
# ///
"""Generic OKF agent baseline implemented with Python standard library only."""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from pathlib import Path

_LINK_RE = re.compile(r"\[[^\]]+\]\(([^)\s]+)\)")


def _documents(root: Path) -> list[Path]:
    """Return authored Markdown concept paths in deterministic order."""
    return sorted(path for path in root.rglob("*.md") if path.name not in {"index.md", "log.md"})


def _concept_id(root: Path, path: Path) -> str:
    """Derive the benchmark concept identity from relative Markdown path."""
    return path.relative_to(root).with_suffix("").as_posix()


def _frontmatter(path: Path) -> dict[str, str]:
    """Parse the benchmark's deliberately simple top-level scalar frontmatter."""
    lines = path.read_text(encoding="utf-8").splitlines()
    if not lines or lines[0] != "---":
        return {}
    try:
        end = lines.index("---", 1)
    except ValueError:
        return {}
    result: dict[str, str] = {}
    for line in lines[1:end]:
        if not line or line[0].isspace() or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip().strip("\"'")
    return result


def _resolved_targets(root: Path, source: Path) -> list[str]:
    """Resolve Markdown file links to benchmark concept ids."""
    text = source.read_text(encoding="utf-8")
    targets: list[str] = []
    for raw in _LINK_RE.findall(text):
        raw_path = raw.split("#", 1)[0]
        if not raw_path.endswith(".md"):
            continue
        rel_source = source.relative_to(root).parent.as_posix()
        normalized = posixpath.normpath(posixpath.join(rel_source, raw_path))
        candidate = root / normalized
        if candidate.is_file() and candidate.suffix == ".md":
            targets.append(Path(normalized).with_suffix("").as_posix())
    return targets


def validate(root: Path) -> dict[str, object]:
    """Validate the common benchmark subset without OKF-specific libraries."""
    errors: list[str] = []
    documents = _documents(root)
    for path in documents:
        frontmatter = _frontmatter(path)
        if not frontmatter:
            errors.append(f"{path.relative_to(root).as_posix()}: frontmatter")
        elif not frontmatter.get("type"):
            errors.append(f"{path.relative_to(root).as_posix()}: type")
    return {"conformant": not errors, "concept_count": len(documents), "errors": errors}


def inventory(root: Path) -> dict[str, object]:
    """Count concepts by type."""
    counts: dict[str, int] = {}
    for path in _documents(root):
        concept_type = _frontmatter(path).get("type", "")
        counts[concept_type] = counts.get(concept_type, 0) + 1
    return {"types": dict(sorted(counts.items()))}


def show(root: Path, concept_id: str) -> dict[str, object]:
    """Read one concept by deterministic filesystem identity."""
    path = root / f"{concept_id}.md"
    return {
        "concept_id": concept_id,
        "path": path.relative_to(root).as_posix(),
        "text": path.read_text(encoding="utf-8"),
    }


def filter_type(root: Path, concept_type: str) -> dict[str, object]:
    """Find concept ids whose simple frontmatter type matches exactly."""
    ids = [
        _concept_id(root, path)
        for path in _documents(root)
        if _frontmatter(path).get("type") == concept_type
    ]
    return {"concept_ids": ids}


def backlinks(root: Path, concept_id: str) -> dict[str, object]:
    """Find concepts whose Markdown links resolve to the target concept."""
    sources = [
        _concept_id(root, path)
        for path in _documents(root)
        if concept_id in _resolved_targets(root, path)
    ]
    return {"concept_id": concept_id, "sources": sources}


def graph(root: Path) -> dict[str, object]:
    """Compute basic graph counts from Markdown links."""
    documents = _documents(root)
    concept_ids = {_concept_id(root, path) for path in documents}
    edges: set[tuple[str, str]] = set()
    touched: set[str] = set()
    for path in documents:
        source_id = _concept_id(root, path)
        for target_id in _resolved_targets(root, path):
            if target_id not in concept_ids:
                continue
            edges.add((source_id, target_id))
            touched.add(source_id)
            touched.add(target_id)
    return {
        "nodes": len(concept_ids),
        "edges": len(edges),
        "orphans": len(concept_ids - touched),
    }


def edit_title(root: Path, concept_id: str, title: str) -> dict[str, object]:
    """Replace one simple title field in place."""
    path = root / f"{concept_id}.md"
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        message = f"missing frontmatter: {concept_id}"
        raise ValueError(message)
    replaced = False
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if line.startswith("title:"):
            newline = "\n" if line.endswith("\n") else ""
            lines[index] = f"title: {title}{newline}"
            replaced = True
            break
    if not replaced:
        message = f"missing title: {concept_id}"
        raise ValueError(message)
    path.write_text("".join(lines), encoding="utf-8")
    return {"concept_id": concept_id, "title": title}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=("validate", "inventory", "show", "type", "backlinks", "graph", "edit-title"),
    )
    parser.add_argument("root", type=Path)
    parser.add_argument("value", nargs="?")
    return parser


def main() -> None:
    """Execute one generic-agent operation and emit compact JSON."""
    args = _parser().parse_args()
    root = args.root.resolve()
    if args.operation == "validate":
        payload = validate(root)
    elif args.operation == "inventory":
        payload = inventory(root)
    elif args.operation == "show":
        payload = show(root, str(args.value))
    elif args.operation == "type":
        payload = filter_type(root, str(args.value))
    elif args.operation == "backlinks":
        payload = backlinks(root, str(args.value))
    elif args.operation == "graph":
        payload = graph(root)
    else:
        concept_id, separator, title = str(args.value).partition("=")
        if not separator:
            message = "edit-title value must be CONCEPT_ID=TITLE"
            raise ValueError(message)
        payload = edit_title(root, concept_id, title)
    sys.stdout.write(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
    )


if __name__ == "__main__":
    main()
