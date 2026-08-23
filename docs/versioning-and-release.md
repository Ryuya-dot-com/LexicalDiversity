# Versioning and immutable-release policy

## Current status

The current application identity is `0.10.0-dev.0`; it is development work, not
a published release. The public output contract is independently versioned as
`2.0.0`. There are no Git version tags, complete application-image digest, or
archive DOI. The original clean public-history root and its hosted CI run were
complete, but a subsequent public commit included NJ8 under an owner-attested
decision that is now `review-pending`. Its removal from the current/future tree
does not remove it from reachable commits, clones, or caches, so
`check_git_history.py` now
correctly blocks a release tag pending a reviewed history-boundary decision.
Current-tree hosted CI is a separate claim and does not clear that blocker. The former
22-commit history containing excluded paths is retained in a separate private
legacy repository; see
[`public-history-migration.md`](public-history-migration.md).

The single machine-readable source is [`ldfreq/release.json`](../ldfreq/release.json).
The UI, JSON export, isolated-worker validator, tests, changelog, and tag gate
must agree with it. A version must never be inferred from file modification
times, the branch name, or an uncommitted checkout.

## Separate version axes

| Axis | Authority | Change rule |
|---|---|---|
| Application | `application_version` in `ldfreq/release.json` | SemVer; identifies code and bundled behavior |
| Public JSON/XLSX contract | `output_schema_version` and `docs/v1-metric-scope.json` | Independent SemVer; MAJOR for incompatible keys, types, semantics, defaults, or privacy boundaries |
| Isolated-worker wire protocol | `PROTOCOL_VERSION` in `ldfreq/analysis_worker.py` and `ldfreq/isolated.py` | Integer; increment before an incompatible parent/worker envelope change |
| Resource registry | `schema_version` in `data/resource_registry.json` | SemVer for the governance-document schema |
| Algorithms and resources | Version/hash in code and each resource manifest | Never replace an old identity silently; add or explicitly migrate it |
| Dependency/build inputs | wheel locks and `deploy/cloud-run/base-image.json` | Exact artifact SHA-256 and platform identity |
| Synthetic or human benchmark | Its own manifest and DOI | Never reuse the application version as a dataset version |

The current isolated-worker wire protocol is `2`. It makes `index_records` and
`panel_a_records` mandatory in the result envelope; a protocol-1 parent or
worker must fail closed rather than accepting the incompatible shape.

Every public JSON document and XLSX metadata sheet records both
`ldfreq_version` and `output_schema_version`. This lets a PATCH application
release retain the same output contract and lets consumers reject an unsupported
schema without guessing from the application number.

## Application progression

- Development: `0.10.0-dev.0`, `0.10.0-dev.1`, and so on. These must not be tagged.
- Release candidate: `0.10.0-rc.1`, then `rc.2` if evidence changes. An annotated
  tag may be made only after the release-candidate gates pass.
- Stable: `0.10.0` or `1.0.0`. No prerelease identifier is allowed.
- Published tags and release assets are immutable. A correction receives a new
  PATCH version; force-updating a tag or replacing an archive is prohibited.

For the frozen v1 contract, removing or renaming a field/metric, changing a
formula or denominator, changing tokenizer/lookup semantics, changing an
existing default, replacing a resource identity, or weakening aggregate-only
privacy requires an output-schema MAJOR increment. Compatible optional additions
require MINOR. Documentation, packaging, performance, and formula-restoring bug
fixes may be PATCH, but any numerical delta must be disclosed and receive new
golden fixtures.

## Commit and branch discipline

The broad legacy worktree was used only to construct the reviewed clean root and
must not be pushed to the public repository. Future public changes should be
made from a clean clone as coherent pull requests, organized along these lines:

1. resource rights, manifests, and removal of non-public payloads;
2. aggregate-only analysis, isolation, privacy, and runtime resources;
3. scope freeze, deterministic exports, and golden fixtures;
4. CPython/wheel/base-image locks, Docker, and CI;
5. version identity, changelog, and release gates.

Do not use partial commits that leave code and its test/schema update in
different commits. Before merging, inspect both staged and unstaged diffs. The
local `scripts/check_staging_coherence.py` gate must pass before each commit; it
rejects `AD`, `AM`, `MM`, conflicts, and other paths whose index and worktree
represent different changes. The
recommended GitHub settings are: protect `main`, require the hash-locked CI job,
require pull requests, prohibit force pushes and branch deletion, and require
conversation resolution. Those repository settings are external state and are
not proven by files in this repository.

## Tag release procedure

1. Start from reviewed commits in a clean checkout; run the full hash-locked CI.
2. Change `release_phase` and `application_version` in `ldfreq/release.json`.
3. Move the completed changes from `[Unreleased]` to a dated
   `## [VERSION] - YYYY-MM-DD` changelog entry. Stable releases also require a
   reviewed `CITATION.cff`.
4. Rebuild canonical golden outputs only when version/schema metadata or
   normative results changed; review their diff explicitly.
5. Run `python scripts/check_version_contract.py --development`, all tests,
   runtime/base-image/public-inventory gates, and the golden check.
6. Commit the release preparation, then create one annotated tag exactly named
   `vVERSION`. Do not tag a dirty tree or a `-dev` version.
7. The tag-triggered workflow must pass `--release`; it rejects the wrong tag,
   a lightweight tag, a dirty checkout, or a missing dated changelog entry.
   It also scans every reachable commit tree and rejects paths outside the
   reviewed public-history boundary.
8. Build the source archive twice from the exact Git tag and require byte
   equality. The archive has one fixed directory prefix, fixed gzip metadata,
   the complete tagged-tree inventory, no links/path traversal, and must pass
   the same public-resource inventory policy.
9. Build the complete application image from that tag, record its resulting
   digest, provenance and vulnerability scan, and reproduce the golden outputs
   inside it.
   The tag workflow writes a canonical source/build evidence file to temporary
   storage and prints its SHA-256; it records the validated OCI manifest,
   config, and layer digests separately from the still-pending registry
   manifest digest. A separately daemon-loaded image is only an operational
   smoke-test vehicle and is not the release identity.
10. Publish that evidence, source/archive checksums and, for v1.0, `CITATION.cff` and a durable
   archive DOI. Record the DOI in a new commit/release without rewriting the tag.

The application is release-ready only when the source tag, dependency artifacts,
base image, complete application image, resource identities, output schema, and
golden evidence form one reviewable chain.

That source/image release decision does not authorize a public server-only
resource deployment. `scripts/check_public_release.py` separately rejects a
tracked Cloud Run template unless the server-only allowlist and control fields
are empty and the rights acknowledgement is `0`. A deployment may change those
defaults only after an external record covers the exact deployment/resource
versions under the fixed `shared-abuse-controls-v1` profile and supplies a
valid non-secret opaque evidence ID. Runtime acceptance of that ID verifies only
the declaration shape; it is not proof that the shared limiter, account quota,
audit, anomaly, or extraction-resistance controls exist or pass.

Before changing the version to a release candidate, run the manual
`Candidate application image` workflow from protected `main`. It builds the
production stage twice without cache as OCI archives under a digest-pinned
BuildKit and requires identical manifest, config, and layer digests. It
reproduces golden outputs in a separate non-deployable stage and scans the exact
primary archive. Registry login is unavailable until the Critical gate passes;
the subsequent commit-addressed GHCR rebuild must publish the same manifest
digest before the SBOM, scan, registry manifest, GitHub attestation, and
canonical evidence are complete. This preflight is not a substitute for the
tag-triggered release workflow: candidate tags are not release tags, artifact
retention is finite, and promotion requires a new versioned commit and
immutable annotated Git tag.

This repository is currently a source application, not a PyPI distribution; it
therefore has no independent package-metadata version. If packaging is added,
its metadata must read the same `ldfreq/release.json` authority rather than add
another literal version.
