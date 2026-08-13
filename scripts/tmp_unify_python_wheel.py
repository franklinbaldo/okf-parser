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
