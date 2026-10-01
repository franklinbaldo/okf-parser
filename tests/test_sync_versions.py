"""The release number is authored once; every required mirror is reproducible."""

from __future__ import annotations

import json
import shutil
import tomllib
from pathlib import Path

import pytest

from scripts.project_version import project_version
from scripts.sync_versions import derived_files, main

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """Copy metadata only; synchronizing versions needs no compiler or registry."""
    paths = set(derived_files(ROOT)) | {
        ROOT / "Cargo.toml",
        ROOT / "pyproject.toml",
        ROOT / "okf-engine/Cargo.toml",
        ROOT / "duckdb-extension/Cargo.toml",
        ROOT / "rust-core/src/protocol.rs",
        ROOT / "src/okf_parser/rust_core.py",
    }
    for path in paths:
        target = tmp_path / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return tmp_path


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


def _bump(root: Path) -> None:
    cargo = root / "Cargo.toml"
    old = project_version(cargo)
    cargo.write_text(cargo.read_text().replace(f'version = "{old}"', 'version = "9.8.7"'))


def test_repository_derivatives_are_current() -> None:
    assert main(["--root", str(ROOT), "--check"]) == 0


def test_bump_is_offline_idempotent_and_preserves_unrelated_versions(checkout: Path) -> None:
    before = _snapshot(checkout)
    _bump(checkout)
    assert main(["--root", str(checkout)]) == 0
    after = _snapshot(checkout)
    assert main(["--root", str(checkout)]) == 0
    assert _snapshot(checkout) == after
    assert main(["--root", str(checkout), "--check"]) == 0
    assert 'PROTOCOL_VERSION = "9.8.7"' in (checkout / "typescript/src/version.ts").read_text()
    assert "okf-parser@v9.8.7" in (checkout / "README.pypi.md").read_text()
    for name in (
        "rust-core/src/protocol.rs",
        "src/okf_parser/rust_core.py",
        "duckdb-extension/Cargo.toml",
    ):
        assert before[name] == after[name]
    for name in ("Cargo.lock", "duckdb-extension/Cargo.lock", "uv.lock"):
        old = tomllib.loads(before[name].decode())["package"]
        new = tomllib.loads(after[name].decode())["package"]
        assert [p for p in old if "source" in p and p["name"] != "okf-parser"] == [
            p for p in new if "source" in p and p["name"] != "okf-parser"
        ]
    for directory in ("typescript", "typescript-duckdb"):
        name = f"{directory}/package-lock.json"
        old = json.loads(before[name])["packages"]
        new = json.loads(after[name])["packages"]
        assert {k: v for k, v in old.items() if k.startswith("node_modules/")} == {
            k: v for k, v in new.items() if k.startswith("node_modules/")
        }
        assert new[""]["version"] == "9.8.7"
    uv = tomllib.loads((checkout / "uv.lock").read_text())
    assert "version" not in next(p for p in uv["package"] if p["name"] == "okf-parser")


@pytest.mark.parametrize(
    ("relative", "needle"),
    [
        ("rust-core/Cargo.toml", 'version = "'),
        ("okf-db/Cargo.toml", 'version = "'),
        ("typescript/package.json", '"version": "'),
        ("typescript-duckdb/package.json", '"version": "'),
        ("native-npm-linux-x64/package.json", '"version": "'),
        ("typescript/package-lock.json", '"version": "'),
        ("typescript-duckdb/package-lock.json", '"version": "'),
        ("Cargo.lock", 'name = "okf-core"\nversion = "'),
        ("duckdb-extension/Cargo.lock", 'name = "okf-engine"\nversion = "'),
        ("typescript/src/version.ts", 'PROTOCOL_VERSION = "'),
        ("README.md", "okf-parser@v"),
        ("README.pypi.md", "okf-parser@v"),
    ],
)
def test_check_detects_each_release_mirror(checkout: Path, relative: str, needle: str) -> None:
    path = checkout / relative
    version = project_version(checkout / "Cargo.toml")
    text = path.read_text()
    assert needle + version in text
    path.write_text(text.replace(needle + version, needle + "0.0.0", 1))
    before = _snapshot(checkout)
    assert main(["--root", str(checkout), "--check"]) == 1
    assert _snapshot(checkout) == before
    assert main(["--root", str(checkout)]) == 0
    assert main(["--root", str(checkout), "--check"]) == 0


def test_check_detects_stale_metadata_without_writing(checkout: Path) -> None:
    _bump(checkout)
    before = _snapshot(checkout)
    assert main(["--root", str(checkout), "--check"]) == 1
    assert _snapshot(checkout) == before


@pytest.mark.parametrize("relative", ["typescript/src/version.ts", "README.pypi.md"])
def test_generated_files_can_be_recreated(checkout: Path, relative: str) -> None:
    expected = (checkout / relative).read_bytes()
    (checkout / relative).unlink()
    assert main(["--root", str(checkout), "--check"]) == 1
    assert not (checkout / relative).exists()
    assert main(["--root", str(checkout)]) == 0
    assert (checkout / relative).read_bytes() == expected


def test_malformed_input_does_not_partially_write(checkout: Path) -> None:
    _bump(checkout)
    (checkout / "typescript-duckdb/package.json").write_text("not JSON")
    before = _snapshot(checkout)
    assert main(["--root", str(checkout)]) == 1
    assert _snapshot(checkout) == before


def test_python_build_uses_dynamic_cargo_version() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert "version" not in project["project"]
    assert "version" in project["project"]["dynamic"]
    assert {"file": "Cargo.toml"} in project["tool"]["uv"]["cache-keys"]
    for directory in ("rust-core", "okf-engine", "okf-db"):
        cargo = tomllib.loads((ROOT / directory / "Cargo.toml").read_text())
        assert cargo["package"]["version"] == {"workspace": True}


@pytest.mark.parametrize("suffix", ["-rc.1", ".2", "+build"])
def test_readme_ref_is_replaced_in_full(checkout: Path, suffix: str) -> None:
    path = checkout / "README.md"
    ref = "okf-parser@v" + project_version(checkout / "Cargo.toml")
    path.write_text(path.read_text().replace(ref, ref + suffix))
    assert main(["--root", str(checkout), "--check"]) == 1
    assert main(["--root", str(checkout)]) == 0
    assert ref + suffix not in path.read_text()


@pytest.mark.parametrize(
    ("directory", "key", "section"),
    [
        ("typescript", "", "optionalDependencies"),
        ("typescript-duckdb", "", "peerDependencies"),
        ("typescript-duckdb", "../typescript", "optionalDependencies"),
    ],
)
def test_missing_lock_pins_are_regenerated(
    checkout: Path, directory: str, key: str, section: str
) -> None:
    path = checkout / directory / "package-lock.json"
    data = json.loads(path.read_text())
    del data["packages"][key][section]
    path.write_text(json.dumps(data, indent=2) + "\n")
    assert main(["--root", str(checkout), "--check"]) == 1
    assert main(["--root", str(checkout)]) == 0
    assert json.loads(path.read_text())["packages"][key][section]
