# Changelog

This project follows Semantic Versioning for the application and maintains the
public output-schema version separately. The current development identity is
`0.10.0-dev.0` with output schema `2.0.0`. No immutable public release or Git
version tag exists yet.

## [Unreleased]

### Added

- A frozen v1 metric, interpretation, privacy, and JSON/XLSX output contract.
- Canonical public golden fixtures using the reviewed NGSL, Open English
  WordNet, and TUBELEX runtime resources.
- Exact CPython 3.12.13 Linux x86_64 wheel locks and a platform-specific Python
  base-image manifest identity.
- Reproducible Synthetic-pilot and ELLIPSE-analysis protocols without executing
  either outcome analysis.
- Separate `ldfreq_version` and `output_schema_version` provenance fields.
- Reachable-history and partial-staging gates for release and commit assembly.
- A deterministic clean-history bootstrap archive and canonical per-file
  evidence manifest that derive from the reviewed worktree without `.git/`.
- A clean public GitHub history whose root tree passed the hosted hash-locked CI;
  the legacy history is retained in a separate private repository.
- A registry-backed derived-result publication contract and candidate evidence
  schema v2 bound to an externally approved exact-byte selection manifest.
- A manual candidate-image workflow that performs two independent no-cache
  OCI production builds with a digest-pinned BuildKit, compares their manifest,
  config, and every layer digest, performs offline runtime/golden checks, scans
  the exact OCI archive with an SPDX SBOM and Critical vulnerability gate, and
  permits GHCR publication by commit only when the rebuilt registry manifest
  has the scanned digest. GitHub attestation and canonical evidence do not
  present the candidate as a release.

### Changed

- Advance the unreleased development identity to `0.10.0-dev.0` and the public
  output contract to `2.0.0`; this is a declared breaking migration rather than
  reuse of the former `1.0.0` schema identity.
- Advance the isolated parent/worker wire protocol to `2` because structured
  Panel A records are now mandatory envelope fields.
- General text analysis now defaults to the fixed `english_unicode_v1`
  tokenizer contract (NFC, Unicode letters/marks, normalized typographic
  apostrophes, and explicit hyphen/numeric/alphanumeric handling). Tokenizer
  provenance accepts registered policy IDs only; `ascii_legacy_v1` remains an
  explicit compatibility option and is pinned for existing MASC aggregates.
- Panel A standard method IDs no longer shrink requested MSTTR segments, MATTR
  windows, HD-D samples, or vocd-D sampling ranges for short texts. Public
  payloads now retain the scalar projection and add structured computation
  records with method identity, missingness, requested/effective parameters,
  and a separate advisory-quality status. Python MTLD is identified as the
  bidirectional `<=`, minimum-factor-length-10 variant and is unavailable below
  its actual 10-token domain.
- Public Panel A entry points now reject non-materialized/non-string token
  inputs, implicit parameter coercions, non-boolean switch values, and record
  requests that contradict the fixed MTLD minimum-factor-length method ID.
- Panel B now names its existing hybrid surface-first/direct-key then normalizer-
  fallback mapping method in settings and returns aggregate-only mapping-path
  diagnostics. NJ8 is no longer described as pre-grouped flemmas, and no pure
  flemma or LexTutor numerical-equivalence claim is made.
- A CC0 cross-language semantic fixture now checks values, method identities,
  status meaning, and missing reasons for the ten formulas shared with the R
  package. The fixture separately records the intentionally different R and
  Python MTLD boundary variants and does not require byte or digest equality.
- JSON floating-point serialization and XLSX archive metadata are deterministic.
- Public analysis now runs through a one-shot isolated worker and returns only
  aggregate, pseudonymously labelled results.
- Permission-pending lexical payloads are excluded from public builds.
- NJ8 is reclassified from owner-attested `green` to `yellow` /
  `review-pending`. Its CSV is removed from the current/future source tree,
  release/package/container/CI inventories, and public selector; explicit local
  restricted mode is now the only runtime path. The owner attestation remains
  recorded but is not represented as an independent permission review.
- Generated Quarto HTML/notebook/support files are excluded from Git and public
  release inventories; CSV byte identity is preserved as binary in Git.
- Server-only UI integration is an explicit operator test, while the default
  rights-gate tests are independent of locally installed restricted resources.
- The Dockerfile frontend is digest-pinned, build timestamps derive from the
  source commit, and verification-only fixtures live in a non-deployable stage.
- Legacy TAALES–COCA outputs and unapproved ELLIPSE result bundles are
  fail-closed across Git, build contexts, source candidates, and release scans.
- The production image now uses the digest-pinned Python 3.12.13 Alpine 3.23
  linux/amd64 manifest and musllinux wheels; watchdog's reviewed pure-Python
  wheel is isolated and structurally validated before installation.
- The watchdog foreign-platform install disables bytecode compilation so pip's
  random staging path cannot make otherwise identical OCI layers differ.

### Security and privacy

- Release inventory, runtime identity, query containment, upload limits, and
  non-persistence boundaries now fail closed under automated tests.
- Public server-only resources now remain unlisted, unmaterialized, and absent
  from isolated-worker forwarding unless an eligible allowlist, rights
  acknowledgement, fixed `shared-abuse-controls-v1` declaration, and valid
  non-secret external evidence ID are all present. The checked-in Cloud Run
  template keeps every activation field off, and the release gate enforces
  those defaults. The ID is a reference only; the application does not claim to
  verify the external limiter, quota, audit, anomaly, or extraction controls.
- Registry schema 1.3 adds a fail-closed custom-permission assurance contract.
  A public custom-permission claim now requires the original off-repository
  record identity, record editor, grantor authority, complete public-use scopes,
  exact artifact bindings, and a rights reviewer distinct from both the editor
  and owner-attestor; the release gate also rejects payloads
  reintroduced beside the NJ8 manifest and the exact attestation-bound NJ8
  artifact bytes under any tracked filename.
- Candidate scans require zero active Critical findings and reject VEX or any
  ignored finding as evidence of a passing gate.

Release entries are added only when an immutable annotated tag is created. An
already published entry or tag must never be rewritten.
