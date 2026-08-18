from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OLD = "0.42.7"
NEW = "0.42.8"
MATURIN_ACTION = "PyO3/maturin-action@e83996d129638aa358a18fbd1dfb82f0b0fb5d3b # v1.51.0"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace(path: str, old: str, new: str, *, expected: int = 1) -> None:
    text = read(path)
    count = text.count(old)
    if count != expected:
        raise RuntimeError(f"{path}: expected {expected} occurrences of {old!r}, found {count}")
    write(path, text.replace(old, new))


def harden_release_workflow(path: str, *, public_smoke: bool) -> None:
    text = read(path)

    old_linux = """  wheels-linux:\n    name: Build wheel (linux-${{ matrix.target }})\n    runs-on: ubuntu-latest\n    strategy:\n      fail-fast: false\n      matrix:\n        target: [x86_64, aarch64]\n"""
    new_linux = """  wheels-linux:\n    name: Build wheel (linux-${{ matrix.target }})\n    runs-on: ${{ matrix.runner }}\n    strategy:\n      fail-fast: false\n      matrix:\n        include:\n          - target: x86_64\n            runner: ubuntu-latest\n          - target: aarch64\n            runner: ubuntu-24.04-arm\n"""
    if text.count(old_linux) != 1:
        raise RuntimeError(f"{path}: Linux matrix shape changed")
    text = text.replace(old_linux, new_linux)

    text = text.replace("        if: matrix.target == 'x86_64'\n", "")

    action_marker = f"        uses: {MATURIN_ACTION}\n        with:\n"
    action_count = text.count(action_marker)
    if action_count != 4:
        raise RuntimeError(f"{path}: expected four maturin-action calls, found {action_count}")
    text = text.replace(
        action_marker,
        f"        uses: {MATURIN_ACTION}\n        with:\n          maturin-version: v1.14.1\n",
    )

    wheel_args = "          args: --release --locked --out dist\n"
    wheel_count = text.count(wheel_args)
    if wheel_count != 3:
        raise RuntimeError(f"{path}: expected three wheel build arg blocks, found {wheel_count}")
    text = text.replace(
        wheel_args,
        "          args: --release --locked --out dist --compatibility pypi\n",
    )

    # The build image already supplies a working pip. Avoid pulling an unpinned
    # newer pip into a release gate before installing the exact wheel.
    text = text.replace('          "$bin/python" -m pip install --quiet --upgrade pip\n', "")

    smoke_line = '          "$bin/okf-parser" duckdb "$RUNNER_TEMP/fixture" "$RUNNER_TEMP/bundle.duckdb"\n'
    smoke_count = text.count(smoke_line)
    if smoke_count != 3:
        raise RuntimeError(f"{path}: expected three pre-publish DuckDB smokes, found {smoke_count}")
    smoke_query = smoke_line + """          BUNDLE="$RUNNER_TEMP/bundle.duckdb" "$bin/python" - <<'PY'\n          import os\n          import duckdb\n\n          with duckdb.connect(os.environ["BUNDLE"], read_only=True) as connection:\n              assert connection.sql("SELECT concept_type FROM okf.concepts").fetchone() == ("Note",)\n          PY\n"""
    text = text.replace(smoke_line, smoke_query)

    old_native = """          mkdir -p release/npm release/native-npm\n          cargo build --release --locked --manifest-path rust-core/Cargo.toml\n          mkdir -p native-npm-linux-x64/bin\n          cp target/release/okf-parser native-npm-linux-x64/bin/okf-core\n          chmod +x native-npm-linux-x64/bin/okf-core\n"""
    new_native = """          mkdir -p release/npm release/native-npm native-npm-linux-x64/bin\n          version=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')\n          wheel=$(find release/python -maxdepth 1 -type f -name "okf_parser-${version}-py3-none-manylinux*_x86_64.whl" -print -quit)\n          test -n "$wheel"\n          python scripts/native_from_wheel.py extract "$wheel" native-npm-linux-x64/bin/okf-core\n"""
    if text.count(old_native) != 1:
        raise RuntimeError(f"{path}: native npm build block changed")
    text = text.replace(old_native, new_native)

    verify_anchor = """          listing="$(tar -tzf "$npm_native")"\n          grep -qx 'package/bin/okf-core' <<<"$listing"\n          grep -qx 'package/package.json' <<<"$listing"\n"""
    if text.count(verify_anchor) != 1:
        raise RuntimeError(f"{path}: native npm verification block changed")
    verify_reuse = verify_anchor + """          wheel=$(find release/python -maxdepth 1 -type f -name "okf_parser-${version}-py3-none-manylinux*_x86_64.whl" -print -quit)\n          python scripts/native_from_wheel.py verify "$wheel" "$npm_native"\n"""
    text = text.replace(verify_anchor, verify_reuse)

    if public_smoke:
        old_matrix = "        os: [ubuntu-latest, windows-latest, macos-14]\n"
        new_matrix = (
            "        os: [ubuntu-latest, ubuntu-24.04-arm, windows-latest, macos-15, macos-15-intel]\n"
        )
        if text.count(old_matrix) != 1:
            raise RuntimeError(f"{path}: public smoke matrix changed")
        text = text.replace(old_matrix, new_matrix)

        public_anchor = """          pip install --quiet "okf-parser==${version}"\n          okf-parser --version\n"""
        if text.count(public_anchor) != 1:
            raise RuntimeError(f"{path}: public-index smoke block changed")
        public_checks = public_anchor + """          mkdir -p "$RUNNER_TEMP/public-fixture"\n          printf '%s\\n' '---' 'type: Note' '---' '# Note' > "$RUNNER_TEMP/public-fixture/note.md"\n          okf-parser check "$RUNNER_TEMP/public-fixture" >/dev/null\n          okf-parser duckdb "$RUNNER_TEMP/public-fixture" "$RUNNER_TEMP/public.duckdb" >/dev/null\n          BUNDLE="$RUNNER_TEMP/public.duckdb" python - <<'PY'\n          import os\n          import duckdb\n\n          with duckdb.connect(os.environ["BUNDLE"], read_only=True) as connection:\n              assert connection.sql("SELECT concept_type FROM okf.concepts").fetchone() == ("Note",)\n          PY\n"""
        text = text.replace(public_anchor, public_checks)
        text = text.replace(
            "      - name: Smoke test each wheel installs without a Rust toolchain\n",
            "      - name: Re-smoke the host wheel without a Rust toolchain\n",
        )
    else:
        old_comment = """        # Only the manylinux x86_64 wheel is installable on this (ubuntu-latest\n        # x86_64) runner — the Windows/macOS/aarch64 wheels are verified for\n        # structure above, not by installing them here (cross-platform install\n        # smoke tests run in okf-parser#147's actual release.yml, on runners\n        # matching each wheel's platform). The sdist install below is the one\n"""
        new_comment = """        # Every platform wheel is already installed and exercised in its native\n        # build job above. This aggregate job rechecks the host x86_64 wheel and\n        # the sdist as consumers; the sdist install below is the one\n"""
        if text.count(old_comment) != 1:
            raise RuntimeError(f"{path}: dry-run consumer comment changed")
        text = text.replace(old_comment, new_comment)

    write(path, text)


def add_native_release_contract() -> None:
    path = "scripts/release_contract.py"
    text = read(path)
    replacements = [
        (
            'KINDS: Final = (*WHEEL_KINDS, "python-sdist", "npm-parser", "npm-duckdb")',
            'KINDS: Final = (*WHEEL_KINDS, "python-sdist", "npm-parser", "npm-duckdb", "npm-native")',
        ),
        (
            '    "npm-duckdb",\n]',
            '    "npm-duckdb",\n    "npm-native",\n]',
        ),
        (
            '    "npm-duckdb": "package/",\n}',
            '    "npm-duckdb": "package/",\n    "npm-native": "package/",\n}',
        ),
        (
            '    "npm-duckdb": ContentPolicy(\n        required=(\n            "package.json",\n            "README.md",\n            "LICENSE",\n            "dist/index.js",\n            "dist/index.d.ts",\n            "dist/cli.js",\n        ),\n        forbidden_prefixes=("src/", "test/", "scripts/", "tsconfig"),\n    ),\n}',
            '    "npm-duckdb": ContentPolicy(\n        required=(\n            "package.json",\n            "README.md",\n            "LICENSE",\n            "dist/index.js",\n            "dist/index.d.ts",\n            "dist/cli.js",\n        ),\n        forbidden_prefixes=("src/", "test/", "scripts/", "tsconfig"),\n    ),\n    "npm-native": ContentPolicy(\n        required=("package.json", "README.md", "bin/okf-core"),\n        forbidden_prefixes=("src/", "test/", "scripts/", "tsconfig"),\n    ),\n}',
        ),
        (
            'def _verify_npm_contract(root: Path, version: str) -> None:\n    parser_path = root / "typescript" / "package.json"\n    _verify_npm_manifest(parser_path, "okf-parser", version)\n    adapter_path = root / "typescript-duckdb" / "package.json"\n',
            'def _verify_npm_contract(root: Path, version: str) -> None:\n    parser_path = root / "typescript" / "package.json"\n    parser = _verify_npm_manifest(parser_path, "okf-parser", version)\n    optional = _mapping(parser.get("optionalDependencies"), "optionalDependencies")\n    if _string(optional, "okf-parser-native-linux-x64", "optionalDependencies") != version:\n        _fail(f"native optional dependency must be {version}")\n    native_path = root / "native-npm-linux-x64" / "package.json"\n    _verify_npm_manifest(native_path, "okf-parser-native-linux-x64", version)\n    adapter_path = root / "typescript-duckdb" / "package.json"\n',
        ),
        (
            '        ExpectedArtifact(\n            "npm-duckdb",\n            "okf-parser-duckdb",\n            "npm",\n            filename=f"okf-parser-duckdb-{version}.tgz",\n        ),\n    )',
            '        ExpectedArtifact(\n            "npm-duckdb",\n            "okf-parser-duckdb",\n            "npm",\n            filename=f"okf-parser-duckdb-{version}.tgz",\n        ),\n        ExpectedArtifact(\n            "npm-native",\n            "okf-parser-native-linux-x64",\n            "native-npm",\n            filename=f"okf-parser-native-linux-x64-{version}.tgz",\n        ),\n    )',
        ),
        (
            '        for name in ("python", "npm")\n',
            '        for name in ("python", "npm", "native-npm")\n',
        ),
    ]
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{path}: release-contract anchor occurs {count} times: {old[:60]!r}")
        text = text.replace(old, new)
    write(path, text)


def update_release_contract_tests() -> None:
    path = "tests/test_release_contract.py"
    text = read(path)
    replacements = [
        (
            '    (root / "typescript-duckdb").mkdir()\n    (root / "changelog").mkdir()\n',
            '    (root / "typescript-duckdb").mkdir()\n    (root / "native-npm-linux-x64").mkdir()\n    (root / "changelog").mkdir()\n',
        ),
        (
            '        json.dumps({"name": "okf-parser", "version": VERSION}), encoding="utf-8"\n',
            '        json.dumps(\n            {\n                "name": "okf-parser",\n                "version": VERSION,\n                "optionalDependencies": {"okf-parser-native-linux-x64": VERSION},\n            }\n        ),\n        encoding="utf-8",\n',
        ),
        (
            '    (root / "typescript-duckdb" / "package.json").write_text(\n',
            '    (root / "native-npm-linux-x64" / "package.json").write_text(\n        json.dumps({"name": "okf-parser-native-linux-x64", "version": VERSION}),\n        encoding="utf-8",\n    )\n    (root / "typescript-duckdb" / "package.json").write_text(\n',
        ),
        (
            'NPM_MEMBERS = (\n',
            'NPM_NATIVE_MEMBERS = ("README.md", "bin/okf-core")\nNPM_MEMBERS = (\n',
        ),
        (
            '    npm_dir = release / "npm"\n    python_dir.mkdir(parents=True)\n    npm_dir.mkdir()\n',
            '    npm_dir = release / "npm"\n    native_dir = release / "native-npm"\n    python_dir.mkdir(parents=True)\n    npm_dir.mkdir()\n    native_dir.mkdir()\n',
        ),
        (
            '    kinds = {"okf-parser": "npm-parser", "okf-parser-duckdb": "npm-duckdb"}\n',
            '    with tarfile.open(\n        native_dir / f"okf-parser-native-linux-x64-{VERSION}.tgz", mode="w:gz"\n    ) as archive:\n        _tar_member(\n            archive,\n            "package/package.json",\n            json.dumps(\n                {"name": "okf-parser-native-linux-x64", "version": VERSION}\n            ).encode(),\n        )\n        for member in NPM_NATIVE_MEMBERS + additions.get("npm-native", ()):\n            _tar_member(archive, f"package/{member}", b"content\\n")\n    kinds = {"okf-parser": "npm-parser", "okf-parser-duckdb": "npm-duckdb"}\n',
        ),
        (
            '        ("npm-duckdb", ".npmrc", "excluded file name"),\n',
            '        ("npm-duckdb", ".npmrc", "excluded file name"),\n        ("npm-native", ".npmrc", "excluded file name"),\n',
        ),
    ]
    for old, new in replacements:
        count = text.count(old)
        if count != 1:
            raise RuntimeError(f"{path}: test anchor occurs {count} times: {old[:60]!r}")
        text = text.replace(old, new)

    anchor = """def test_verify_source_rejects_protocol_drift(tmp_path: Path) -> None:\n"""
    test = """def test_verify_source_rejects_stale_native_optional_dependency(tmp_path: Path) -> None:\n    _write_source(tmp_path)\n    parser_path = tmp_path / "typescript" / "package.json"\n    parser = json.loads(parser_path.read_text(encoding="utf-8"))\n    parser["optionalDependencies"]["okf-parser-native-linux-x64"] = "1.2.2"\n    parser_path.write_text(json.dumps(parser), encoding="utf-8")\n    with pytest.raises(ContractError, match="native optional dependency"):\n        verify_source(tmp_path)\n\n\n"""
    if text.count(anchor) != 1:
        raise RuntimeError(f"{path}: protocol test anchor changed")
    text = text.replace(anchor, test + anchor)
    write(path, text)


def write_native_helper_and_tests() -> None:
    helper = '''"""Reuse the exact Linux x86_64 executable from a Python wheel in npm-native."""\n\nfrom __future__ import annotations\n\nimport argparse\nimport hashlib\nimport stat\nimport tarfile\nimport zipfile\nfrom pathlib import Path\n\n_WHEEL_SUFFIX = ".data/scripts/okf-parser"\n_NPM_MEMBER = "package/bin/okf-core"\n\n\nclass ArtifactError(ValueError):\n    """Report a malformed wheel or native npm artifact."""\n\n\ndef wheel_executable(wheel: Path) -> bytes:\n    """Return the single packaged Linux executable from a maturin bin wheel."""\n    try:\n        with zipfile.ZipFile(wheel) as archive:\n            names = [name for name in archive.namelist() if name.endswith(_WHEEL_SUFFIX)]\n            if len(names) != 1:\n                raise ArtifactError(f"{wheel}: expected one okf-parser executable, found {names}")\n            return archive.read(names[0])\n    except (OSError, KeyError, zipfile.BadZipFile) as exc:\n        raise ArtifactError(f"cannot read wheel {wheel}: {exc}") from exc\n\n\ndef npm_executable(tarball: Path) -> bytes:\n    """Return bin/okf-core bytes from one npm-native tarball."""\n    try:\n        with tarfile.open(tarball, mode="r:gz") as archive:\n            member = archive.getmember(_NPM_MEMBER)\n            file_object = archive.extractfile(member)\n            if file_object is None:\n                raise ArtifactError(f"{tarball}: {_NPM_MEMBER} is not a regular file")\n            return file_object.read()\n    except (OSError, KeyError, tarfile.TarError) as exc:\n        raise ArtifactError(f"cannot read npm tarball {tarball}: {exc}") from exc\n\n\ndef digest(data: bytes) -> str:\n    """Return the SHA-256 identity used to prove byte reuse."""\n    return hashlib.sha256(data).hexdigest()\n\n\ndef extract(wheel: Path, destination: Path) -> str:\n    """Write the wheel executable verbatim to the npm-native staging path."""\n    payload = wheel_executable(wheel)\n    destination.parent.mkdir(parents=True, exist_ok=True)\n    destination.write_bytes(payload)\n    destination.chmod(\n        destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH\n    )\n    return digest(payload)\n\n\ndef verify(wheel: Path, tarball: Path) -> str:\n    """Prove the npm-native executable is byte-identical to the wheel executable."""\n    wheel_payload = wheel_executable(wheel)\n    npm_payload = npm_executable(tarball)\n    if wheel_payload != npm_payload:\n        raise ArtifactError(\n            f"native executable mismatch: wheel={digest(wheel_payload)} npm={digest(npm_payload)}"\n        )\n    return digest(wheel_payload)\n\n\ndef _parser() -> argparse.ArgumentParser:\n    parser = argparse.ArgumentParser(description=__doc__)\n    commands = parser.add_subparsers(dest="command", required=True)\n    extract_command = commands.add_parser("extract")\n    extract_command.add_argument("wheel", type=Path)\n    extract_command.add_argument("destination", type=Path)\n    verify_command = commands.add_parser("verify")\n    verify_command.add_argument("wheel", type=Path)\n    verify_command.add_argument("tarball", type=Path)\n    return parser\n\n\ndef main(argv: list[str] | None = None) -> int:\n    """Run extract or byte-identity verification."""\n    arguments = _parser().parse_args(argv)\n    try:\n        if arguments.command == "extract":\n            sha256 = extract(arguments.wheel, arguments.destination)\n        else:\n            sha256 = verify(arguments.wheel, arguments.tarball)\n    except ArtifactError as exc:\n        raise SystemExit(f"native artifact error: {exc}") from exc\n    print(f"native executable sha256={sha256}")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'''
    write("scripts/native_from_wheel.py", helper)

    tests = '''"""Tests for byte-identical reuse of the wheel executable in npm-native."""\n\nfrom __future__ import annotations\n\nimport io\nimport tarfile\nimport zipfile\nfrom typing import TYPE_CHECKING\n\nimport pytest\n\nfrom scripts.native_from_wheel import ArtifactError, extract, verify\n\nif TYPE_CHECKING:\n    from pathlib import Path\n\n\ndef _wheel(path: Path, payload: bytes) -> None:\n    with zipfile.ZipFile(path, mode="w") as archive:\n        archive.writestr("okf_parser-1.2.3.data/scripts/okf-parser", payload)\n\n\ndef _npm(path: Path, payload: bytes) -> None:\n    with tarfile.open(path, mode="w:gz") as archive:\n        member = tarfile.TarInfo("package/bin/okf-core")\n        member.size = len(payload)\n        archive.addfile(member, io.BytesIO(payload))\n\n\ndef test_extract_and_verify_reuse_exact_bytes(tmp_path: Path) -> None:\n    wheel = tmp_path / "parser.whl"\n    destination = tmp_path / "package" / "bin" / "okf-core"\n    tarball = tmp_path / "native.tgz"\n    payload = b"\\x7fELFsame-native-engine"\n    _wheel(wheel, payload)\n\n    sha256 = extract(wheel, destination)\n    assert destination.read_bytes() == payload\n    assert sha256\n\n    _npm(tarball, destination.read_bytes())\n    assert verify(wheel, tarball) == sha256\n\n\ndef test_verify_rejects_a_rebuilt_or_changed_native_binary(tmp_path: Path) -> None:\n    wheel = tmp_path / "parser.whl"\n    tarball = tmp_path / "native.tgz"\n    _wheel(wheel, b"wheel-binary")\n    _npm(tarball, b"different-build")\n\n    with pytest.raises(ArtifactError, match="native executable mismatch"):\n        verify(wheel, tarball)\n'''
    write("tests/test_native_from_wheel.py", tests)


def bump_versions() -> None:
    replace("pyproject.toml", f'version = "{OLD}"', f'version = "{NEW}"')
    replace("rust-core/Cargo.toml", f'version = "{OLD}"', f'version = "{NEW}"')
    replace("README.md", f"franklinbaldo/okf-parser@v{OLD}", f"franklinbaldo/okf-parser@v{NEW}")
    replace(
        "typescript/src/version.ts",
        f'export const PROTOCOL_VERSION = "{OLD}";',
        f'export const PROTOCOL_VERSION = "{NEW}";',
    )

    parser_path = ROOT / "typescript" / "package.json"
    parser = json.loads(parser_path.read_text(encoding="utf-8"))
    parser["version"] = NEW
    parser["optionalDependencies"]["okf-parser-native-linux-x64"] = NEW
    parser_path.write_text(json.dumps(parser, indent=2) + "\n", encoding="utf-8")

    adapter_path = ROOT / "typescript-duckdb" / "package.json"
    adapter = json.loads(adapter_path.read_text(encoding="utf-8"))
    adapter["version"] = NEW
    adapter["peerDependencies"]["okf-parser"] = f"^{NEW}"
    adapter_path.write_text(json.dumps(adapter, indent=2) + "\n", encoding="utf-8")

    native_path = ROOT / "native-npm-linux-x64" / "package.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    native["version"] = NEW
    native_path.write_text(json.dumps(native, indent=2) + "\n", encoding="utf-8")

    changelog = f'''---\ntype: Release\ntitle: okf-parser {NEW}\n---\n\n# okf-parser {NEW}\n\n- hardens wheel production by pinning maturin 1.14.1 and enabling its PyPI\n  compatibility preflight on every platform wheel;\n- builds and executes the Linux aarch64 wheel on a native GitHub arm64 runner,\n  so all five published wheels now pass a real install, `check`, DuckDB export,\n  and SQL result assertion before publication;\n- reuses the already-built manylinux x86_64 executable byte-for-byte for the\n  `okf-parser-native-linux-x64` npm package instead of compiling a second native\n  binary during release collection, and verifies both copies have identical\n  SHA-256 identity;\n- brings that npm-native tarball under the immutable release manifest and source\n  version contract, including the parser's exact optional native dependency;\n- makes Rust warnings a CI failure with workspace-wide Clippy and removes the\n  stale `Serialize` import exposed by the DuckDB unlinking;\n- strengthens pre- and post-publication smokes to query the generated DuckDB\n  database, and expands public-index verification to all five native runner\n  architectures.\n'''
    write(f"changelog/{NEW}.md", changelog)


def main() -> None:
    replace("rust-core/src/main.rs", "use serde::{Deserialize, Serialize};", "use serde::Deserialize;")
    replace(
        ".github/workflows/rust-engine.yml",
        "      - name: Check workspace formatting\n        run: cargo fmt --all --manifest-path Cargo.toml --check\n      - name: Test semantic engine\n",
        "      - name: Check workspace formatting\n        run: cargo fmt --all --manifest-path Cargo.toml --check\n      - name: Reject Rust warnings and Clippy findings\n        run: cargo clippy --workspace --all-targets --locked -- -D warnings\n      - name: Test semantic engine\n",
    )
    harden_release_workflow(".github/workflows/publish.yml", public_smoke=True)
    harden_release_workflow(".github/workflows/release-dry-run.yml", public_smoke=False)
    add_native_release_contract()
    update_release_contract_tests()
    write_native_helper_and_tests()
    bump_versions()


if __name__ == "__main__":
    main()
