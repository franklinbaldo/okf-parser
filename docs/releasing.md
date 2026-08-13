---
type: Documentation
title: Releasing okf-parser
description: Build, verify and publish synchronized Python and TypeScript releases
---

# Releasing okf-parser

The repository publishes one synchronized protocol version across one Python project and three npm packages:

- Python `okf-parser` on PyPI;
- TypeScript `okf-parser` on npm;
- TypeScript `okf-parser-duckdb` on npm;
- platform npm companion `okf-parser-native-linux-x64` on npm.

There is no `okf-parser-native` PyPI project. The Rust engine is an implementation detail of the `okf-parser` Python distribution and is embedded directly in its single `okf-parser` executable.

RFC 0003 defines the production model. PyPI and npm do not offer a distributed transaction, so releases are monotonic, digest-verified and resumable rather than falsely described as atomic.

## Release dry run

The `Release Dry Run` workflow builds the complete release set without registry credentials or write permissions. Its tested release tree contains:

```text
release/
├── python/
│   ├── okf_parser-X.Y.Z-<platform>.whl
│   └── okf_parser-X.Y.Z.tar.gz
├── npm/
│   ├── okf-parser-X.Y.Z.tgz
│   └── okf-parser-duckdb-X.Y.Z.tgz
├── native-npm/
│   └── okf-parser-native-linux-x64-X.Y.Z.tgz
├── manifest.json
├── registry-state.json
└── SHA256SUMS
```

The Python wheel must contain exactly one `okf-parser` executable in its wheel scripts payload. A fresh consumer install must resolve that executable automatically and successfully load a fixture through the public `load_bundle()` API. The source distribution is independently installed as a consumer to prove that it can build the same integrated package from source.

The workflow builds each release artifact once, records its package identity, byte size, SHA-256, SHA-512 and npm-compatible SRI integrity, then installs those same files in clean Python and Node consumers. It does not rebuild before upload.

Pull requests that change release-sensitive files run this workflow automatically. A maintainer can also open **Actions → Release Dry Run → Run workflow** and supply an existing branch, commit or stable `vX.Y.Z` tag.

The uploaded GitHub Actions artifact is evidence for review, not a public release. Its retention period is 14 days.

## Source contract

Before building, `scripts/release_contract.py verify-source` requires all of the following to agree:

- `project.version` in `pyproject.toml`;
- the Rust crate version;
- versions in the npm manifests;
- `PROTOCOL_VERSION` in `typescript/src/version.ts`;
- the `okf-parser` peer range in the DuckDB adapter;
- `changelog/X.Y.Z.md` frontmatter title;
- an optional stable tag, exactly `vX.Y.Z`.

Prereleases are deliberately rejected until npm dist-tag policy is implemented.

## Python packaging

The root project uses Maturin as its PEP 517 backend with `bindings = "bin"`. The Python import package and PyPI distribution remain `okf_parser` and `okf-parser`; the sole installed binary target is `okf-parser`.

A platform wheel therefore installs the ordinary Python package behind one `okf-parser` command. That executable forwards public CLI and MCP commands to the packaged Python module and handles private native-engine operations itself. Applications do not depend on, import or locate a second Python distribution. `resolve_rust_core()` discovers the same executable from the interpreter scripts directory before consulting explicit environment overrides or `PATH`.

The source distribution contains the Rust sources required to build that same wheel. Publishing a pure-Python selector wheel is deliberately not part of the Python release model.

## Local contract commands

Build the package files first, then run:

```bash
python scripts/release_contract.py verify-source
python scripts/release_contract.py build-manifest \
  --directory release \
  --repository franklinbaldo/okf-parser \
  --commit "$(git rev-parse HEAD)" \
  --ref "$(git rev-parse --abbrev-ref HEAD)" \
  --python-version "$(python -c 'import platform; print(platform.python_version())')" \
  --node-version "$(node --version)" \
  --npm-version "$(npm --version)" \
  --uv-version "$(uv --version | awk '{print $2}')"
python scripts/release_contract.py verify-local --manifest release/manifest.json
python scripts/release_contract.py verify-contents --manifest release/manifest.json
python -m scripts.registry_state --manifest release/manifest.json \
  --output release/registry-state.json
```

The manifest command fails on missing, duplicate or unexpected release artifacts. Native npm companions are verified separately because they are platform implementation packages rather than protocol-level manifest entries.

## Package contents

`verify-contents` reads the member list of each archive named by the manifest and answers a question digests cannot: whether the bytes that were tested are also the right files to publish.

Every distribution must ship its installable payload and must not ship caches, virtual environments, repository automation, credentials, private keys, compiled Python bytecode, local databases, or source-only development material that does not belong in an installed consumer.

The release dry run additionally verifies the presence and executability of the Rust engine inside the Python wheel and inside the npm platform package.

## Public registry preflight

The registry command performs anonymous HTTPS reads only. It compares the manifest with PyPI SHA-256 file digests and npm SRI integrity, then classifies each target as `absent`, `present_expected`, `present_conflict` or `unverifiable`. Its plan uses `publish`, `skip` or `block`, which makes retries resumable without overwriting immutable registry state.

## Production publication

`.github/workflows/release.yml` runs only for stable `vX.Y.Z` tag pushes. It builds and verifies the release set, uploads the exact tested artifact tree, and gives registry publication to a separate job using the GitHub `pypi` environment and OIDC trusted publishing.

Publication order is:

1. publish the single Python `okf-parser` distribution to PyPI;
2. publish the npm platform companion;
3. publish the main npm parser;
4. publish the npm DuckDB adapter;
5. create the GitHub Release only after all registry publication steps succeed.

The workflow does not use a long-lived PyPI token. The PyPI Trusted Publisher must match repository `franklinbaldo/okf-parser`, workflow `release.yml`, and GitHub environment `pypi`.

npm publication follows the same no-overwrite rule and should use npm Trusted Publishing. A retry first checks whether an exact package version already exists and skips immutable state that has already been published.
