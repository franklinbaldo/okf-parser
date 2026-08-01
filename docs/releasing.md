---
type: Documentation
title: Releasing okf-parser
description: Build, verify and bootstrap synchronized Python and TypeScript releases
---

# Releasing okf-parser

The repository publishes one synchronized protocol version as three packages:

- Python `okf-parser` on PyPI;
- TypeScript `okf-parser` on npm;
- TypeScript `okf-parser-duckdb` on npm.

RFC 0003 defines the production model. PyPI and npm do not offer a distributed
transaction, so releases are monotonic, digest-verified and resumable rather
than falsely described as atomic.

## Current capability: dry run and read-only preflight

The `Release Dry Run` workflow builds the complete release set without registry
credentials or write permissions. It produces exactly four package artifacts:

```text
release/
├── python/
│   ├── okf_parser-X.Y.Z-py3-none-any.whl
│   └── okf_parser-X.Y.Z.tar.gz
├── npm/
│   ├── okf-parser-X.Y.Z.tgz
│   └── okf-parser-duckdb-X.Y.Z.tgz
├── manifest.json
├── registry-state.json
└── SHA256SUMS
```

The workflow builds each artifact once, records its package identity, byte size,
SHA-256, SHA-512 and npm-compatible SRI integrity, then installs those same files
in clean Python and Node consumers. It does not rebuild before upload.

Pull requests that change release-sensitive files run this workflow
automatically. After the workflow exists on `main`, a maintainer can also open
**Actions → Release Dry Run → Run workflow** and supply an existing branch,
commit, or stable `vX.Y.Z` tag.

The uploaded GitHub Actions artifact is evidence for review, not a public
release. Its retention period is 14 days.

## Source contract

Before building, `scripts/release_contract.py verify-source` requires all of the
following to agree:

- `project.version` in `pyproject.toml`;
- `version` in both npm manifests;
- `PROTOCOL_VERSION` in `typescript/src/version.ts`;
- `okf-parser` peer range in the DuckDB adapter;
- `changelog/X.Y.Z.md` frontmatter title;
- an optional stable tag, exactly `vX.Y.Z`.

Prereleases are deliberately rejected until npm dist-tag policy is implemented.

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
python -m scripts.registry_state --manifest release/manifest.json \
  --output release/registry-state.json
```

The manifest command fails on missing, duplicate or unexpected artifacts. The
verification command rejects path traversal, archive identity drift, changed
sizes or digests, and a `SHA256SUMS` file that no longer matches the manifest.

## Public registry preflight

The registry command performs anonymous HTTPS reads only. It compares the
manifest with PyPI SHA-256 file digests and npm SRI integrity, then classifies
each target as `absent`, `present_expected`, `present_conflict` or
`unverifiable`. Its plan uses `publish`, `skip` or `block`, which is the state
machine later consumed by the privileged workflow.

When a target version is absent, the report also probes the package root. This
distinguishes a genuinely available bootstrap name from a package that already
exists at another version. Conflicts, incomplete releases and unverifiable
responses return a non-zero status; no registry mutation is attempted.

## Production publication remains disabled

This phase intentionally does **not** add `.github/workflows/release.yml`, OIDC
permissions, GitHub environments, registry credentials, tags or public package
uploads.

Before production publication is enabled, maintainers must separately review and
complete the RFC bootstrap:

1. protect stable `v*` tags and create a protected `release` environment;
2. configure the pending PyPI trusted publisher for `release.yml`;
3. confirm both unscoped npm names are available;
4. perform the one-time reviewed npm bootstrap publications with account MFA;
5. configure npm trusted publishers for both packages;
6. review a privileged PR that consumes the tested registry-state model and adds
   publication jobs using the already-tested artifact set.

A GitHub Release will eventually be created only after PyPI and both npm package
versions verify with the expected digests.
