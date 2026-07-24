# Changelog

This project follows Semantic Versioning for the application and maintains the
public output-schema version separately. The current development identity is
`0.9.0-dev.0`. No immutable public release or Git version tag exists yet.

## [Unreleased]

### Added

- A frozen v1 metric, interpretation, privacy, and JSON/XLSX output contract.
- Canonical public golden fixtures using the reviewed NGSL, Open English
  WordNet, and TUBELEX runtime resources.
- Exact CPython 3.12.10 Linux x86_64 wheel locks and a platform-specific Python
  base-image manifest identity.
- Reproducible Synthetic-pilot and ELLIPSE-analysis protocols without executing
  either outcome analysis.
- Separate `ldfreq_version` and `output_schema_version` provenance fields.
- Reachable-history and partial-staging gates for release and commit assembly.
- A deterministic clean-history bootstrap archive and canonical per-file
  evidence manifest that derive from the reviewed worktree without `.git/`.
- A clean public GitHub history whose root tree passed the hosted hash-locked CI;
  the legacy history is retained in a separate private repository.
- A manual candidate-image workflow that performs two independent no-cache
  production builds, offline runtime/golden checks, an SPDX SBOM, a
  Critical-gated vulnerability scan, GHCR publication by commit, GitHub build
  attestation, and canonical evidence without presenting the candidate as a
  release.

### Changed

- JSON floating-point serialization and XLSX archive metadata are deterministic.
- Public analysis now runs through a one-shot isolated worker and returns only
  aggregate, pseudonymously labelled results.
- Permission-pending lexical payloads are excluded from public builds.
- Generated Quarto HTML/notebook/support files are excluded from Git and public
  release inventories; CSV byte identity is preserved as binary in Git.
- Server-only UI integration is an explicit operator test, while the default
  rights-gate tests are independent of locally installed restricted resources.
- The Dockerfile frontend is digest-pinned, build timestamps derive from the
  source commit, and verification-only fixtures live in a non-deployable stage.

### Security and privacy

- Release inventory, runtime identity, query containment, upload limits, and
  non-persistence boundaries now fail closed under automated tests.

Release entries are added only when an immutable annotated tag is created. An
already published entry or tag must never be rewritten.
