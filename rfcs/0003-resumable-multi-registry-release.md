---
type: RFC
title: Resumable multi-registry release pipeline
status: accepted
description: Publish synchronized Python and TypeScript artifacts with OIDC, provenance and deterministic recovery from partial releases
---

# RFC 0003: Resumable multi-registry release pipeline

## Summary

Add one tag-driven GitHub Actions release pipeline for the three synchronized
artifacts produced by this repository:

- the Python `okf-parser` distribution on PyPI;
- the TypeScript `@franklinbaldo/okf-parser` package on npm;
- the optional `@franklinbaldo/okf-parser-duckdb` package on npm.

The workflow builds all artifacts once from one tagged commit, verifies them as
installed consumer artifacts, records checksums and provenance, and then
publishes the exact files that were tested. Authentication uses OpenID Connect
trusted publishing rather than long-lived registry tokens after one-time npm
bootstrap.

A truly atomic transaction across PyPI and npm is impossible because neither
registry supports a distributed prepare/commit protocol and published versions
are intentionally immutable. The pipeline therefore provides the strongest
practical replacement: a **monotonic, resumable release**. It publishes in a
safe dependency order, never overwrites an existing version, detects whether a
previous attempt already published the exact artifact, resumes missing steps,
and creates the GitHub Release only after every registry contains the complete
version set.

## Motivation

Version `0.11.0` completes the planned first-class TypeScript capability set.
The repository now has mature build and consumer-install gates, but publication
is still manual and therefore weaker than the code path it distributes.

Three facts make a naive `npm publish && twine upload` workflow unsafe:

1. the artifacts live in two independent registries;
2. `@franklinbaldo/okf-parser-duckdb` has a peer dependency on the main npm package and must
   not become visible first;
3. a network or registry failure can occur after one immutable upload succeeds.

Release engineering must make those failure states explicit rather than
pretending that multiple remote publishes are atomic.

The release pipeline should also avoid long-lived secrets. Both PyPI and npm
support GitHub Actions trusted publishing through OIDC. PyPI recommends a
dedicated least-privilege workflow, while npm trusted publishing requires npm
CLI 11.5.1 or newer and Node.js 22.14.0 or newer. npm automatically emits
provenance for public packages published through trusted publishing from a
public GitHub repository.

References:

- https://docs.pypi.org/trusted-publishers/
- https://docs.pypi.org/trusted-publishers/security-model/
- https://docs.npmjs.com/trusted-publishers/
- https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations

## Decision drivers

The design prioritizes, in order:

1. publishing exactly the artifacts that passed release verification;
2. recovery from partial publication without version reuse or deletion;
3. short-lived, workflow-bound credentials;
4. one understandable version across all runtime artifacts;
5. deterministic evidence linking tag, commit, files and registry entries;
6. explicit human authorization at the deployment boundary;
7. minimal special-case logic for the initial release;
8. a workflow small enough to audit as a privileged security boundary.

## Terminology

- **release set**: the Python wheel/sdist and the two npm tarballs for one
  synchronized semantic version;
- **build manifest**: a machine-readable inventory containing file names,
  package identities, version, byte sizes and SHA-256 digests;
- **registry state**: whether a package version is absent, present with the
  expected digest, or present with a conflicting digest;
- **complete release**: all three registry artifacts exist with the expected
  version and digest, and a GitHub Release points to the tag;
- **partial release**: at least one expected registry artifact exists while one
  or more are absent;
- **bootstrap publication**: the one-time first npm publish needed before the
  package settings page can be used to configure a trusted publisher.

## Goals

- build all release artifacts once from one immutable tag;
- verify tag, package versions and changelog before any upload;
- test the built wheel/sdist and npm tarballs as external consumers;
- publish with PyPI and npm trusted publishers through OIDC;
- require an explicit protected GitHub environment approval before publication;
- publish npm packages in dependency order;
- make retries idempotent and safe after partial publication;
- refuse any registry version whose digest differs from the built artifact;
- attach checksums and GitHub artifact attestations to the release evidence;
- create the GitHub Release only after all registry publications are complete;
- keep action references pinned to immutable commit SHAs;
- document the one-time human configuration required in PyPI, npm and GitHub.

## Non-goals

- creating a distributed transaction that the registries do not support;
- deleting or replacing published versions to simulate rollback;
- automatically releasing every merge to `main`;
- deriving versions from commit messages;
- maintaining independent versions for Python and TypeScript artifacts;
- storing npm automation tokens or PyPI API tokens as long-lived secrets;
- publishing from pull-request workflows or arbitrary branch commits;
- allowing a release job to rebuild different bytes after validation;
- using self-hosted runners for OIDC publication;
- solving package-name ownership if an external actor claims an unreserved name.

## Release identity

One semantic version continues to identify one cross-runtime protocol. A release
is valid only when these values are identical:

- `project.version` in `pyproject.toml`;
- `version` in `typescript/package.json`;
- `PROTOCOL_VERSION` in `typescript/src/version.ts`;
- `version` in `typescript-duckdb/package.json`;
- the main package range in `typescript-duckdb`'s peer dependency;
- the version encoded in `changelog/<version>.md`;
- the annotated Git tag `v<version>`.

The existing pull-request version gate remains. Release automation does not bump
versions and does not generate changelogs. It only publishes a version already
reviewed and merged into `main`.

## Trigger and authorization

The production workflow is `.github/workflows/release.yml` and triggers only on
an annotated tag matching `v*.*.*`.

The workflow verifies that:

1. the tag resolves to a commit reachable from `main`;
2. the tag name is exactly `v` followed by the synchronized repository version;
3. the matching changelog exists;
4. the working tree reconstructed from the tag is clean;
5. no package version is ahead of or behind the tag.

Publication jobs use one GitHub environment named `release`. The environment
should require a maintainer approval and should restrict deployment to protected
release tags. The environment contains no registry token; it is an authorization
boundary for OIDC jobs.

The tag itself should be protected so only maintainers can create or delete
`v*` tags. Deleting a tag never deletes registry artifacts and therefore is not
considered rollback.

## Privilege separation

The workflow has four logical phases with separate jobs:

1. `preflight` — read-only metadata and registry-state checks;
2. `build` — deterministic build, consumer tests, checksums and attestations;
3. `publish` — OIDC publication using the protected environment;
4. `finalize` — verify registries and create the GitHub Release.

The build job does not receive `id-token: write`. Only the publication jobs have
OIDC permission. The finalizer receives `contents: write` only for creating the
GitHub Release.

Recommended permissions:

```yaml
permissions:
  contents: read
```

Per-job elevation:

```yaml
publish-pypi:
  permissions:
    id-token: write
    contents: read

publish-npm-parser:
  permissions:
    id-token: write
    contents: read

publish-npm-duckdb:
  permissions:
    id-token: write
    contents: read

finalize:
  permissions:
    contents: write
    id-token: write
    attestations: write
```

No pull-request-controlled job receives publication credentials.

## Build once, publish exact bytes

The build job checks out the tag and creates:

```text
release/
├── python/
│   ├── okf_parser-X.Y.Z-py3-none-any.whl
│   └── okf_parser-X.Y.Z.tar.gz
├── npm/
│   ├── okf-parser-X.Y.Z.tgz
│   └── franklinbaldo-okf-parser-duckdb-X.Y.Z.tgz
└── manifest.json
```

`manifest.json` records for each artifact:

- distribution name;
- version;
- relative file path;
- byte length;
- SHA-256 digest;
- source repository;
- source commit SHA;
- release tag;
- runtime and build-tool versions.

The job then installs the artifacts into clean temporary consumers:

- create a fresh Python environment and install the wheel;
- import the Python package and execute the CLI;
- install the source distribution in a second fresh environment;
- install the main npm tarball in a fresh Node project;
- run the parser CLI, formatter, MCP smoke client and public imports;
- install both npm tarballs together in another fresh project;
- create and query a DuckDB database through both CLI and API.

The artifact directory is uploaded once with a digest-preserving GitHub Actions
artifact. Publication jobs download it; they never run `uv build` or `npm pack`.

## Determinism policy

Byte-for-byte reproducibility across different workflow runs is desirable but is
not assumed until independently proven for every packaging tool. The release
contract instead requires that all registry uploads in one release use the exact
single-build files described by one manifest.

A retried workflow first downloads the retained build artifact from the original
successful build job when GitHub permits job retry. If an entirely new workflow
run is required, it rebuilds and compares its manifest against any already
published registry artifacts. A digest conflict aborts the release permanently
for that version.

Future work may add SOURCE_DATE_EPOCH and cross-run reproducibility assertions,
but release safety does not depend on making an unsupported claim today.

## Registry preflight

Before publication, the workflow queries each registry for the target version.
Each expected artifact is classified as:

- `absent` — safe to publish;
- `present_expected` — already published from the expected release artifact;
- `present_conflict` — same version exists but its digest does not match;
- `unverifiable` — registry state cannot be confirmed.

`present_conflict` and `unverifiable` stop publication. `present_expected` is
considered complete and is skipped, enabling safe retries.

### PyPI digest comparison

PyPI exposes SHA-256 digests for release files. The workflow matches the expected
wheel and source distribution filenames and requires both digests to equal the
build manifest.

A release is not treated as present merely because the project version exists;
every expected file must match.

### npm digest comparison

npm metadata exposes distribution integrity and tarball information. The
workflow computes the SRI integrity value for each built tarball and compares it
with the registry version metadata.

A package version that exists with a different integrity value is a hard failure.
The pipeline never attempts `npm unpublish`, deprecation or overwrite as an
automatic recovery action.

## Publication order

The safe dependency order is:

1. Python `okf-parser` to PyPI;
2. npm `@franklinbaldo/okf-parser`;
3. npm `@franklinbaldo/okf-parser-duckdb`.

PyPI and the main npm package do not depend on one another and may technically be
published in parallel. This RFC chooses a linear sequence because it produces a
simpler, auditable recovery state and the release volume does not justify
parallel complexity.

The DuckDB adapter is always last because its peer dependency names the same
version line of the main npm package.

Each publication job behaves as follows:

```text
registry state absent           -> publish exact artifact
registry state present_expected -> no-op success
registry state present_conflict -> fail
registry state unverifiable     -> fail
```

## PyPI trusted publishing

PyPI publication uses the official PyPA publish action and a pending or existing
Trusted Publisher configured for:

- owner: `franklinbaldo`;
- repository: `okf-parser`;
- workflow: `release.yml`;
- environment: `release`.

PyPI supports pending publishers for a project that does not exist yet, so the
first Python publication can use OIDC without a bootstrap API token.

The publish job downloads only `release/python/` and passes that directory to
the action. It does not check out or execute repository source after entering
the protected publication environment.

## npm trusted publishing

Both npm packages configure the same GitHub Actions trusted publisher identity:

- owner: `franklinbaldo`;
- repository: `okf-parser`;
- workflow filename: `release.yml`;
- environment: `release`;
- allowed action: `npm publish`.

The workflow uses a GitHub-hosted runner, Node.js 24 and an explicitly verified
npm CLI version meeting npm's trusted-publishing minimum. It sets
`id-token: write` and uses `npm publish` without `NODE_AUTH_TOKEN`.

Trusted publishing automatically provides npm provenance for public packages in
a public repository. The workflow does not add `--provenance` as a second,
divergent mechanism.

## First npm publication

npm's trusted publisher is configured from an existing package's settings page.
Therefore each unscoped package requires a one-time bootstrap publication before
OIDC can become its only publishing path.

The bootstrap process is deliberately outside the automated production
workflow:

1. run the full release build and verification without publication;
2. download the exact reviewed npm tarball and manifest;
3. confirm that the intended unscoped name is still available;
4. publish version `X.Y.Z` interactively from a maintainer workstation using
   npm account MFA;
5. configure `release.yml` as the trusted publisher for the new package;
6. repeat for `@franklinbaldo/okf-parser-duckdb` only after `@franklinbaldo/okf-parser` exists;
7. remove any temporary automation credential if one was created;
8. re-run the tag workflow, which verifies the existing digest and completes
   the other registries/finalization idempotently.

## Amendment: the npm names are scoped

This section originally assumed unscoped npm names matching the PyPI
distribution. That assumption did not survive contact with the registry.

npm refused `okf-parser` with `403 Package name too similar to existing package
oxc-parser`. The refusal is produced by npm's similarity filter at upload time,
so the availability check in step 3 above -- which only proves that nobody has
registered the exact name -- can never predict it. All three packages are
therefore published under the `@franklinbaldo` scope, while the PyPI
distribution stays unscoped as `okf-parser`.

Scoping does not remove the bootstrap requirement: npm still refuses to
configure a trusted publisher for a package that does not exist, whether or not
it is scoped. It does remove the risk of a second similarity refusal, because
scoped names are not subject to that filter.

Two further details in this RFC are stale: the publication workflow is
`publish.yml`, not `release.yml`, and there are three npm packages rather than
two, since the platform companion did not exist when this RFC was written.

The first public release version is chosen only after package-name availability
is confirmed. If either name has been claimed, package renaming requires a
separate reviewed change; the release workflow must not improvise a registry
identity.

## Partial-release recovery

Published package versions are immutable. Recovery is forward-only.

Example states:

| PyPI     | npm parser | npm DuckDB | Action                            |
| -------- | ---------- | ---------- | --------------------------------- |
| absent   | absent     | absent     | publish all in order              |
| expected | absent     | absent     | skip PyPI; publish npm packages   |
| expected | expected   | absent     | publish DuckDB adapter            |
| expected | expected   | expected   | finalize GitHub Release           |
| conflict | any        | any        | stop permanently for this version |
| any      | conflict   | any        | stop permanently for this version |
| any      | any        | conflict   | stop permanently for this version |

A failed job is retried through GitHub Actions. The workflow must not require a
new tag or version merely because a transient network error occurred after a
successful upload.

If the registry accepted bytes but the workflow lost the response, the next run
identifies `present_expected` and continues.

## GitHub artifact attestations

The finalizer generates GitHub artifact attestations for:

- the Python wheel;
- the Python source distribution;
- the main npm tarball;
- the DuckDB adapter tarball;
- the build manifest.

It uses the current official `actions/attest` action pinned to a full commit SHA
and grants only `id-token: write` and `attestations: write` in that job.

The npm registry's own provenance remains authoritative for npm package pages;
GitHub attestations provide a uniform repository-level verification path for all
five release files.

## GitHub Release finalization

The GitHub Release is created only after a final registry verification reports
all three package versions as `present_expected`.

The release:

- uses the existing annotated tag;
- takes its title from `okf-parser X.Y.Z`;
- uses `changelog/X.Y.Z.md` as the release notes source;
- attaches the wheel, source distribution, both npm tarballs and
  `manifest.json`;
- records SHA-256 checksums in a human-readable `SHA256SUMS` attachment;
- is marked latest unless the version is a prerelease.

The absence of a GitHub Release therefore means the release set is incomplete or
failed, even if one registry artifact already exists.

## Prereleases

A SemVer prerelease tag such as `v1.0.0-rc.1` is supported by the release
contract but is not included in the first implementation PR unless required by
a concrete release.

When added:

- PyPI receives the prerelease version normally;
- npm publishes under a matching non-`latest` dist-tag such as `next`;
- the GitHub Release is marked prerelease;
- a stable version never reuses prerelease artifact bytes or metadata.

The initial implementation should reject prerelease versions rather than make an
implicit dist-tag choice.

## Release contract script

A language-light script under `scripts/release_contract.py` centralizes metadata
checks and manifest generation. It provides commands such as:

```text
verify-source --tag vX.Y.Z
build-manifest --directory release/
verify-local --manifest release/manifest.json
registry-state --manifest release/manifest.json
```

The script uses Python's standard library for local checks and simple HTTPS JSON
requests for registry metadata. It does not perform publication.

Keeping release policy outside shell fragments makes it testable with fixtures
for absent, expected, conflicting and incomplete registry responses.

## CI changes

Pull-request CI gains release-contract tests but never contacts registries for
write operations.

The version gate is extended to verify:

- the TypeScript protocol constant;
- the DuckDB peer dependency range;
- the changelog frontmatter title and version;
- that package names match the release contract.

A package-content test inspects each tarball and Python distribution to ensure no
source-only files, temporary workflows, credentials, caches or build directories
are included.

## Security considerations

The release workflow is equivalent to a registry credential and receives stricter
review than ordinary CI.

Required controls:

- pin third-party actions by full commit SHA;
- use a dedicated workflow file named exactly `release.yml`;
- avoid `pull_request_target` entirely;
- never execute artifacts supplied by an untrusted workflow run;
- never use mutable branch refs as a release source;
- grant `id-token: write` only to OIDC publication/attestation jobs;
- use GitHub-hosted runners for trusted publishing;
- disable dependency caching in publication jobs;
- use a protected `release` environment with maintainer approval;
- protect release tags;
- never print OIDC or short-lived registry tokens;
- treat any modification to `release.yml` or release scripts as security-sensitive;
- retain registry verification logs and the manifest as release evidence.

## Failure policy

The workflow fails closed when:

- the tag/version/changelog relationship is inconsistent;
- the tagged commit is not reachable from `main`;
- any build or consumer test fails;
- an expected artifact is missing from the manifest;
- a registry request cannot be authenticated or verified;
- an existing version has a conflicting digest;
- the protected environment is not approved;
- any publication step reports an ambiguous result that registry re-check cannot
  resolve;
- final registry verification is incomplete.

It does not automatically delete tags, unpublish versions, create replacement
versions or change npm dist-tags after failure.

## Observability

Every run writes a concise step summary containing:

- tag and commit;
- synchronized version;
- artifact filenames and digests;
- preflight registry state;
- publication actions taken or skipped;
- final registry state;
- GitHub Release URL when complete.

The summary must not include tokens, request authorization headers or unrelated
account metadata.

## Implementation plan

### PR 1 — release contract and dry-run build

- add `scripts/release_contract.py` and unit tests;
- strengthen synchronized-version validation;
- add `release.yml` with preflight/build/consumer-test jobs only;
- upload release artifacts and manifest but do not publish;
- support manual dry-run invocation against an existing tag or commit;
- document bootstrap and registry configuration.

### Human bootstrap

- confirm package-name availability;
- configure the PyPI pending trusted publisher;
- create the protected `release` environment and tag rules;
- perform the reviewed first npm publications;
- configure npm trusted publishers for both package settings.

### PR 2 — production publication

- enable tag trigger and protected publication jobs;
- add registry-state comparison and idempotent skips;
- publish PyPI, npm parser and npm adapter in order;
- generate attestations;
- create the GitHub Release after final verification.

Splitting dry-run from write capability ensures the privileged workflow is based
on code already exercised in the repository before registry access is enabled.

## Acceptance criteria

The RFC is implemented when:

- one tag names one synchronized version across all manifests and changelog;
- one build job creates the complete release set and manifest;
- clean consumer installations pass for wheel, sdist and both npm packages;
- publication jobs consume the tested files without rebuilding;
- PyPI and npm publication use OIDC trusted publishing after bootstrap;
- npm parser publication precedes the DuckDB adapter;
- rerunning after any partial success safely skips exact existing artifacts;
- conflicting registry digests fail without mutation;
- no GitHub Release is created before all registries verify complete;
- release artifacts have checksums and GitHub attestations;
- the workflow uses protected tags/environment and least privilege;
- no long-lived write token remains in repository secrets;
- release-policy tests cover absent, complete, partial, conflicting and
  unverifiable registry states.

## Alternatives considered

### Publish independently from each package directory

Rejected. Separate workflows can race, rebuild different bytes and leave no
single authoritative release manifest.

### Publish automatically on every merge to `main`

Rejected. A merge is not sufficient authorization for an irreversible registry
operation, and many reviewed changes are not intended as immediate public
releases.

### Use API tokens stored as GitHub secrets

Rejected for steady-state publication. Long-lived tokens expand the credential
lifetime and rotation burden when both registries support OIDC.

### Roll back by unpublishing successful artifacts

Rejected. Registry deletion policies differ, consumers may already have fetched
the artifact, and deletion cannot restore an atomic history.

### Rebuild separately in each publication job

Rejected. Passing tests on one artifact does not justify publishing newly built
bytes from another job.

### Publish the DuckDB adapter before the main npm package

Rejected. The adapter's peer dependency should resolve at the moment it becomes
visible.

### Give every package an independent version

Rejected. The repository intentionally defines one observable cross-runtime
protocol and already enforces synchronized versions.

### Use one monolithic job with all permissions

Rejected. It combines source execution, build tools, registry credentials and
GitHub write access into one unnecessarily broad trust boundary.

## Recommended resolution

Adopt the resumable release model described here:

- tag-driven and manually authorized;
- one build, one manifest and exact artifact reuse;
- OIDC trusted publishing;
- linear dependency-aware publication;
- digest-based idempotent recovery;
- GitHub Release only after complete registry verification;
- dry-run implementation before enabling writes.

This does not claim impossible cross-registry atomicity. It makes every partial
state observable, safe to resume and impossible to silently overwrite.
