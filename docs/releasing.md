---
type: Documentation
title: Releasing okf-parser
description: Build, verify and publish synchronized Python and TypeScript releases
---

# Releasing okf-parser

The repository publishes one synchronized protocol version across one Python project and three npm packages:

- Python `okf-parser` on PyPI;
- TypeScript `@franklinbaldo/okf-parser` on npm;
- TypeScript `@franklinbaldo/okf-parser-duckdb` on npm;
- platform npm companion `@franklinbaldo/okf-parser-native-linux-x64` on npm.

The npm packages are scoped and the PyPI distribution is not. npm refuses the unscoped name `okf-parser`, whose similarity filter considers it too close to the existing `oxc-parser`, and that refusal only happens on upload. The scope is therefore a constraint npm imposes, not a naming preference.

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
│   ├── franklinbaldo-okf-parser-X.Y.Z.tgz
│   └── franklinbaldo-okf-parser-duckdb-X.Y.Z.tgz
├── native-npm/
│   └── franklinbaldo-okf-parser-native-linux-x64-X.Y.Z.tgz
├── manifest.json
├── registry-state.json
└── SHA256SUMS
```

The Python wheel must contain exactly one `okf-parser` executable in its wheel scripts payload. A fresh consumer install must resolve that executable automatically and successfully load a fixture through the public `load_bundle()` API. The source distribution is independently installed as a consumer to prove that it can build the same integrated package from source.

The workflow builds each release artifact once, records its package identity, byte size, SHA-256, SHA-512 and npm-compatible SRI integrity, then installs those same files in clean Python and Node consumers. It does not rebuild before upload.

Pull requests that change release-sensitive files run this workflow automatically. A maintainer can also open **Actions → Release Dry Run → Run workflow** and supply an existing branch, commit or stable `vX.Y.Z` tag.

The uploaded GitHub Actions artifact is evidence for review, not a public release. Its retention period is 14 days.

### Parallel validation and Cargo caches

The cold source consumer depends only on `sdist`, so its bundled DuckDB build runs
alongside the five native wheel builds. `build-release-set` requires both that
consumer and every wheel job before assembling and uploading the tested set.
The source consumer downloads the same immutable `sdist` artifact as the aggregate
job. It has fresh Cargo/uv directories, no restored cache or prebuilt DuckDB, and
uses `--locked` with default features. This proves the actual source fallback on
every run, including warm-cache runs.

Native macOS and Windows wheels cache Cargo dependencies using the runner's
existing Rust compiler, separately by runner/target, no-default-features mode,
Python build configuration and pinned DuckDB fetcher. The cache action also hashes
Rust/compiler environment and Cargo manifests/lockfiles. It does not cache the
workspace crates; compilation, packaging, library verification and consumer tests
always run. Cache restoration precedes the verified DuckDB download because cache
cleanup can remove non-Cargo files from `target/`. The manylinux containers keep
separate uncached builds rather than mixing their compiler/sysroot with host data.
The release profile (`thin` LTO, one codegen unit) is unchanged.

Ubuntu CI jobs cache their native dependencies separately by job, avoiding
first-writer races between immutable debug-only and release caches. An absolute
`CARGO_TARGET_DIR` also lets `uv sync`, direct Cargo commands and `uv build` reuse
compatible dependencies when PEP 517 extracts the source into a temporary folder.
Cargo still checks fingerprints and rebuilds changed workspace sources.

GitHub cache visibility remains branch-scoped: a warm PR rerun can use its own
cache, but a PR cache does not warm `main` or another PR. A cold first run still
compiles everything. Compare the first and second runs of the same commit and
check the cache action's hit/miss logs as well as wall time; the uncached source
consumer may remain the longest job. Cache hits are an optimization, never a
substitute for any release check.

#### Measured cold baseline (2026-09-30)

The [pre-optimization dry run](https://github.com/franklinbaldo/okf-parser/actions/runs/36770932057)
and the [first optimized cold run](https://github.com/franklinbaldo/okf-parser/actions/runs/36776438581)
both passed every applicable check. Times below come from GitHub job/step
`started_at` and `completed_at`, not estimates. Workflow duration is the first
job's start through the final job's completion, excluding initial scheduling.

| Measurement | Before | Optimized, cold Cargo cache |
| --- | ---: | ---: |
| Release dry run critical path | 29m 33s | 17m 22s |
| Release-set aggregate job | 16m 00s | 1m 04s |
| Cold source consumer | 15m 05s (with host wheel, serial) | 16m 00s (own parallel job) |
| macOS Intel wheel job | 13m 30s | 12m 51s |
| CI quality job | 8m 16s | 6m 12s |
| CI `uv build` step | 3m 25s | 1m 24s |

CI measurements use [the previous CI run](https://github.com/franklinbaldo/okf-parser/actions/runs/36770931804)
and [the optimized CI run](https://github.com/franklinbaldo/okf-parser/actions/runs/36776438488).
The first optimized native wheel and quality jobs reported Cargo cache misses.
The 41% shorter dry-run path comes from parallel validation; it does not require a
warm cache. The remaining cold source build is deliberately retained. Individual
runner/compiler timings vary, so the small cold Intel difference is not evidence
of a cache benefit. The `uv build` improvement already reuses compatible
in-job dependencies despite the initially cold external cache.

To measure warm caching, make a documentation-only commit on the same PR after
the cold run finishes, keep all Cargo/Rust, dependency, feature, compiler and
workflow inputs unchanged, and verify exact Cargo cache hits in the next run.
Repeat the full workflow, including the deliberately cold source consumer, and
compare each native build job as well as the overall critical path. This uses a
fresh set of immutable release artifacts and avoids mixing partial rerun uploads.
The final warm observations for this change are recorded in
[PR #308](https://github.com/franklinbaldo/okf-parser/pull/308).

## Source contract

Before building, `scripts/release_contract.py verify-source` requires all of the following to agree:

- `workspace.package.version` in the root `Cargo.toml`, the only authored version;
- Rust crate versions inherited with `version.workspace = true`;
- Python metadata declared with `dynamic = ["version"]`, supplied by Maturin;
- versions in the npm manifests;
- `PROTOCOL_VERSION` in `typescript/src/version.ts`;
- the `@franklinbaldo/okf-parser` peer range in the DuckDB adapter;
- at least one release-note fragment in `changelog/X.Y.Z/`;
- an optional stable tag, exactly `vX.Y.Z`.

Prereleases are deliberately rejected until npm dist-tag policy is implemented.

## One authored release version

Edit only `workspace.package.version` in the root `Cargo.toml`, then run:

```bash
uv run --script scripts/sync_versions.py
uv run --script scripts/sync_versions.py --check
uv run --script scripts/release_contract.py verify-source
```

The synchronizer updates the required static npm metadata, local dependency
version constraints, lockfile workspace snapshots, the TypeScript release
constant and the README Action example. It regenerates `README.pypi.md` from
`README.md`. These derived files remain committed, so ordinary Cargo, Python and
npm commands work on a checkout without a special build wrapper. Synchronization
is idempotent and needs neither a compiler nor registry access; it preserves
external dependency versions, resolutions and integrity hashes. `--check` reports
drift without editing files and runs in CI and both release workflows.

Rust crates inherit the workspace version directly. Maturin derives the Python
distribution version from the `okf-core` crate. uv cache keys include Cargo
metadata so changing the canonical version invalidates dynamic build metadata;
its editable-project lock entry has no static version. Cargo and npm still need
version copies in their lockfiles. After synchronization, `uv lock --check`,
Cargo's `--locked` commands and `npm ci` validate the package-manager contracts.

`typescript/src/version.ts` is generated and preserves the public
`PROTOCOL_VERSION` export used by capabilities and MCP server metadata. The
separate integer shell/binary protocol in Rust and Python is **not** a release
number and is left unchanged. The unpublished DuckDB extension also retains its
independent package version; only its lockfile's `okf-engine` entry follows the
release. Historical release notes and tags are immutable history, not mirrors.

Create a note fragment in `changelog/<version>/` for the change. Do not change a
published tag or publish anything as part of synchronization. The tag is checked
against the canonical version when an authorized release is initiated.

## Version numbers and release notes

A version number identifies a **release**, not a pull request. Several changes may declare the same number, because that is the version they will all carry once they merge. A stack of pull requests should therefore share one version rather than climbing by one at every level -- the CI gate used to force that climb, by comparing each head against its base, and produced chains numbered 0.45.3 through 0.45.8 that nobody intended.

The gate now checks the invariant that matters: the declared version must outrank every published `v*.*.*` tag. It may equal the base's version, and it may not regress below it.

Each change contributes its own note to `changelog/X.Y.Z/`, so changes sharing a version never compete for one file:

```text
changelog/
├── 0.45.2.md              # releases up to 0.45.2 keep their flat file
└── 0.45.3/
    ├── correct-0-45-2-notes.md
    ├── pep723-workflow-python.md
    └── uv-first-release-validation.md
```

A fragment is a `type: Release Note` concept with a title and a body of bullets. `scripts/changelog_notes.py X.Y.Z` assembles them, in sorted file-name order, into the body `finalize` publishes as the GitHub Release; prefix a file name if a fragment must sort somewhere particular. The assembler strips frontmatter, which the old `--notes-file changelog/X.Y.Z.md` did not -- v0.45.1's release body still opens with a byte-order mark and a `---` block.

Preview a release's notes at any time:

```bash
uv run --script scripts/changelog_notes.py "$(uv run --script scripts/project_version.py)"
```

## Python packaging

The root project uses Maturin as its PEP 517 backend with `bindings = "bin"`. The Python import package and PyPI distribution remain `okf_parser` and `okf-parser`; the sole installed binary target is `okf-parser`.

A platform wheel therefore installs the ordinary Python package behind one `okf-parser` command. That executable declares the whole CLI and MCP surface and answers `check`, `inventory`, `graph`, `init`, `duckdb` and the private native-engine operations itself; only the commands still written in Python are forwarded to the packaged Python module (RFC 0024). Applications do not depend on, import or locate a second Python distribution. `resolve_rust_core()` discovers the same executable from the interpreter scripts directory before consulting explicit environment overrides or `PATH`.

### DuckDB in the wheel

The executable carries DuckDB (RFC 0024 phase 4) in one of two ways:

- **Release wheels and CI** link the library DuckDB itself publishes. `scripts/fetch_libduckdb.py` downloads it for one Rust target, checks it against a pinned SHA-256 (the pins follow the `duckdb` crate in `Cargo.lock`, and a crate bump without new pins fails), cuts a macOS universal library down to the target's slice and exports `DUCKDB_LIB_DIR`; the build runs with `--no-default-features`. `scripts/ship_libduckdb.py` then puts the library into the wheel's `.data/scripts/`, which installers copy next to the executable, where its run path (`$ORIGIN`, `@executable_path`, set in `rust-core/build.rs`) and the Windows DLL search find it. `scripts/check_duckdb_shipped.py` proves every wheel ships exactly the library its executable links, the failure that broke the 0.42.6 macOS and Windows wheels, and each wheel is installed into a fresh environment and run before it is uploaded. Maturin's `auditwheel` is set to `warn`: its repair would move the binary behind a Python shim.
- **Source builds** (the sdist, `cargo build`) keep the default `bundled` feature and compile DuckDB in, so they need nothing downloaded; it costs about half an hour of C++.

The npm native package carries the same library next to `bin/okf-core`, byte for byte (`scripts/native_from_wheel.py`).

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

`.github/workflows/publish.yml` runs only for stable `vX.Y.Z` tag pushes. It builds and verifies the release set, uploads the exact tested artifact tree, and gives registry publication to a separate job using the GitHub `pypi` environment and OIDC trusted publishing.

Publication order is:

1. publish the single Python `okf-parser` distribution to PyPI;
2. publish the npm platform companion;
3. publish the main npm parser;
4. publish the npm DuckDB adapter;
5. create the GitHub Release only after all registry publication steps succeed.

The workflow does not use a long-lived PyPI token. The PyPI Trusted Publisher must match repository `franklinbaldo/okf-parser`, workflow `publish.yml`, and GitHub environment `pypi`.

npm publication follows the same no-overwrite rule and should use npm Trusted Publishing. A retry first checks whether an exact package version already exists and skips immutable state that has already been published.
