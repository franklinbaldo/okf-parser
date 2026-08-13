from pathlib import Path
import shutil

OLD = "0.41.2"
NEW = "0.41.3"

pyproject = Path("pyproject.toml")
text = pyproject.read_text()
text = text.replace('version = "0.41.2"', 'version = "0.41.3"', 1)
text = text.replace('  "okf-parser-native==0.41.2",\n', '')
text = text.replace(
    '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"',
    '[build-system]\nrequires = ["maturin>=1.10,<2"]\nbuild-backend = "maturin"',
)
start = text.find("\n[tool.hatch.build.targets.sdist]")
if start != -1:
    end = text.find("\n[tool.pytest.ini_options]", start)
    text = text[:start] + text[end:]
start = text.find("\n[tool.uv.sources]")
if start != -1:
    text = text[:start].rstrip() + "\n"
text += '''
[tool.maturin]
manifest-path = "rust-core/Cargo.toml"
bindings = "bin"
python-source = "src"
python-packages = ["okf_parser"]
strip = true
locked = true
sdist-generator = "git"
'''
pyproject.write_text(text)

for name in [
    "rust-core/Cargo.toml",
    "Cargo.lock",
    "typescript/package.json",
    "typescript/package-lock.json",
    "typescript/src/version.ts",
    "typescript-duckdb/package.json",
    "typescript-duckdb/package-lock.json",
    "native-npm-linux-x64/package.json",
    "README.md",
]:
    path = Path(name)
    if path.exists():
        path.write_text(path.read_text().replace(OLD, NEW))

release = Path(".github/workflows/release.yml")
text = release.read_text()
text = text.replace(
    "mkdir -p release/python release/native-python release/npm release/native-npm",
    "mkdir -p release/python release/npm release/native-npm",
)
text = text.replace("          uv build --wheel native-python-stub --out-dir release/native-python\n", "")
text = text.replace("          uv build --wheel rust-core --out-dir release/native-python\n", "")
text = text.replace(
    "          rm -f release/python/.gitignore release/native-python/.gitignore",
    "          rm -f release/python/.gitignore",
)
block_start = text.index("          stub=$(find release/native-python")
block_end = text.index("          listing=\"$(tar -tzf \"$npm_native\")\"", block_start)
prefix = text[:block_start]
suffix = text[block_end:]
replacement = '''          python_wheel=$(find release/python -maxdepth 1 -type f -name "okf_parser-${version}-*.whl" -print -quit)
          npm_native="release/native-npm/okf-parser-native-linux-x64-${version}.tgz"
          test -n "$python_wheel"
          test -f "$npm_native"
          PYTHON_WHEEL="$python_wheel" python - <<'PY'
          import os
          import zipfile
          from pathlib import Path

          wheel = Path(os.environ["PYTHON_WHEEL"])
          with zipfile.ZipFile(wheel) as archive:
              scripts = [name for name in archive.namelist() if name.endswith('.data/scripts/okf-core')]
              if len(scripts) != 1:
                  raise SystemExit(f"expected one bundled okf-core script, found {scripts}")
          PY
'''
text = prefix + replacement + suffix
text = text.replace(
    '''      - name: Publish Python native companion first
        env:
          UV_PUBLISH_CHECK_URL: https://pypi.org/simple
        run: uv publish release/native-python/*.whl

''',
    "",
)
release.write_text(text)

shutil.rmtree("native-python-stub")
Path("rust-core/pyproject.toml").unlink()
Path("changelog/0.41.3.md").write_text('''---
type: Release
title: okf-parser 0.41.3
---

# okf-parser 0.41.3

- ships the Rust `okf-core` executable inside the `okf-parser` Python wheel via Maturin;
- removes the separate `okf-parser-native` PyPI distribution and dependency;
- keeps `load_bundle(root)` automatic engine discovery unchanged for consumers;
- publishes only the single `okf-parser` Python project through the existing `pypi` environment.
''')
