# Web lexical-sophistication roadmap

Last updated: 2026-07-24

This roadmap replaces a direct Web wrapper around legacy TAALES with a Python 3
analysis service built only from cleared, versioned, reproducible resources.
Every phase is gated by `resource-governance.md` and
`privacy-and-data-handling.md`.

## Product priority and execution model

The primary outcome is a public, reproducible, maintainable tool. Publications
are evidence and documentation derived from a released tool; they are not a
separate critical path that may delay release. The application remains a
descriptive lexical profiler. It must not output grades, CEFR estimates, writer-
proficiency predictions, essay-quality predictions, or AI-authorship scores.

The phases below are capability and governance tracks, not a requirement to
finish every proposed corpus, metric, and deployment before v1.0. The minimum
v1.0 scope is the current cleared metric set plus stable schemas, CI, golden
fixtures, method/interpretation cards, reproducible resource identities,
release documentation, CITATION.cff, and an archived release DOI. MASC/OANC,
complete TAALES reconstruction, permission-pending resources, and a real-text
public service are post-v1 or conditional tracks.

Validation is split into three evidence layers:

1. **Computational verification:** deterministic fixtures and property tests
   establish formulas, boundaries, and byte-for-byte reproducibility.
2. **Measurement characterization:** a fully published, provenance-rich LLM
   essay benchmark measures sensitivity to register instructions, repetition,
   length, topic, genre, model, and sampling. It does not stand in for human
   proficiency.
3. **External association:** the freely available ELLIPSE corpus is fetched and
   analyzed locally to test association with human vocabulary ratings. Raw
   learner texts remain outside Git, builds, packages, CI artifacts, and the
   application; no fitted scoring model enters the product.

The execution order and stop rules are maintained in
[`strategic-roadmap-2026-2028.md`](strategic-roadmap-2026-2028.md).
The exact v1.0 metric/output boundary is frozen in
[`v1-scope-freeze.md`](v1-scope-freeze.md) and
[`v1-metric-scope.json`](v1-metric-scope.json). The current prioritization
rationale, including explicitly deferred work, is recorded in
[`decision-log-2026-07-24.md`](decision-log-2026-07-24.md).

## 2026-07-28 release checkpoint

The following contracts are implemented locally but do not by themselves mean
that v1.0, a benchmark dataset, or a validation result has been released:

- a CPython 3.12.13 clean-environment CI workflow, exact production/CI
  dependency graphs with one Linux x86_64 wheel SHA-256 per package, an exact
  Alpine 3.23 linux/amd64 Python base-image manifest, and fail-closed identity
  checks;
- a single `0.9.0-dev.0` application-version authority, independent `1.0.0`
  output-schema identity in every export, an Unreleased changelog, and a
  tag-only immutable-release gate;
- the machine-checked v1.0 metric, runtime-resource, JSON/Excel schema,
  interpretation, and SemVer boundary; and
- the 48-document Synthetic pilot protocol, including prompts, provenance,
  retry/QC rules, a strict pre-request budget reservation, and public-inventory
  controls. The protocol is specified but has made zero API calls and spent
  zero yen.

The public-history migration is complete under
[`public-history-migration.md`](public-history-migration.md). The reviewed root
commit `1cd75aac8749d7512a629415441ae0703246b38a` has one reachable commit and 131
files; its hosted CPython 3.12.10 hash-locked CI run passed every step. The old
22-commit history is retained separately as a private, unarchived legacy
repository and is not the source of public releases. The clean application-image
build, resulting digest and provenance, and golden reproduction inside that
image are now recorded below. The v0.9 release-candidate package remains later.
Canonical golden fixtures, deterministic JSON/XLSX serialization, package
hashes, and the exact production Python base-image manifest are implemented.
The current local verification environment is CPython 3.12.13. The earlier
CPython 3.12.10 contracts and suite passed in hosted Linux CI from the clean
public root. The Alpine security refresh passes all 46 exact runtime pins,
`pip check`, golden reproduction, and the public-data-independent suite locally
(291 passed, 2 skipped), and passed the hosted CI and Candidate gates.
Package hashes and the exact production Python base-image digest are fixed as
inputs. The successful hosted build below adds end-to-end candidate-image
evidence but is not a stable release claim.
The manual candidate-image workflow now specifies two no-cache production
OCI builds under digest-pinned BuildKit, timestamp normalization, manifest,
config, and full layer-digest equality, and offline golden verification in a
non-deployable stage. SBOM generation and the Critical vulnerability gate read
the exact OCI archive. The gate allows no VEX, ignored finding, or `only-fixed`
filter; active Critical findings must be zero. GHCR login follows the scan, and
the independently rebuilt commit-addressed publication must have the scanned manifest digest
before provenance attestation and canonical evidence are accepted.

### 2026-07-28 Alpine candidate evidence

- Source: commit `dfd25b7179b08b684143f0c6956c5fef6ab0abab`, tree
  `06629605a08f928eb0cdce09ef725008aa98ed87` on protected `main`.
- Workflow: [Candidate run 30339125970](https://github.com/Ryuya-dot-com/LexicalDiversity/actions/runs/30339125970),
  successful in one attempt. Two independent no-cache builds produced equal
  OCI manifest, config, and all 23 layer digests; offline smoke and golden
  reproduction passed.
- Candidate image:
  `ghcr.io/ryuya-dot-com/lexicaldiversity@sha256:e5c7d3bf11075dce9531d986b2bc3381126e2aaff7432e5ec72029325760c93a`;
  config digest
  `sha256:3cedd22b5929d470fdb6e5d0f28df1478ceb4ce00327fff32ae96457f808eaf1`.
- Scan: Grype 0.116.0 with database v6.1.9 built 2026-07-27. Findings were
  Critical 0, High 41, Medium 30, Low 4, Negligible 1; ignored findings 0,
  `only-fixed: false`, exception policy `none`.
- Provenance:
  [GitHub attestation 37463385](https://github.com/Ryuya-dot-com/LexicalDiversity/attestations/37463385)
  was pushed for the registry manifest. The workflow artifact was
  `candidate-image-evidence-dfd25b7179b08b684143f0c6956c5fef6ab0abab`
  with archive digest
  `sha256:87fbe85a479049058f2a3809e89978fb09864ea7131a2e500c61df22d08ef58d`.
- Boundary: canonical status `verified-candidate-not-release`; no Git tag,
  GitHub Release, stable image promotion, deployment approval, or v1.0 claim.

The first Alpine run exposed nondeterministic watchdog bytecode generated from
pip's random staging path. PR
[#11](https://github.com/Ryuya-dot-com/LexicalDiversity/pull/11) disabled that
install-time compilation and made the independent layers equal. Do not compute
ELLIPSE rating associations, generate the Synthetic pilot, or add metrics or
corpora merely because the application-image gate is now green.

## Current baseline

- The existing Streamlit application and `ldfreq` core run on Python 3.
- Legacy TAALES is local-only and not imported by the Web application.
- NJ8 and NGSL are enabled for public use.
- AntBNC and the legacy EAPFoundation BNC/COCA snapshot are permission-pending
  and hidden. The official Nation headword and family resources are `green`,
  but remain server-only and hidden until the deployment attestation is set.
- Open English WordNet 2025 polysemy and hypernym-depth metrics are integrated.
- The TUBELEX-EN Treebank published aggregate is pinned and integrated in code
  as an everyday-language frequency/contextual-diversity axis. Its exact
  4,572,297-byte artifact was reproduced byte-for-byte and admitted after the
  full suite and public-inventory gate passed.
- The project-owned `open_flemma` normalizer is the public default. It uses
  deterministic inflection rules plus the cleared NGSL and Open English WordNet
  vocabularies; AntBNC and COCA are not runtime dependencies.
- Source text, original filenames, token sequences, and word-level lookup rows
  are removed before results enter Session State.
- Web analysis now uses a one-shot subprocess with a 120-second default hard
  deadline, rights-gated child-side resource loading, content-free IPC errors,
  core dumps disabled, and TERM/KILL/reap cleanup.

## Phase 0 — governance gate

Status: **implemented; clean public-history boundary verified**

- Maintain the machine-readable resource registry and verified hashes.
- Admit only `green` payloads to public builds.
- Preserve license notices and transformation provenance.
- Keep the complete legacy TAALES tree ignored and local-only.
- AntBNC, EAPFoundation XLSX/PDF, and Nation raw-list payloads have been removed
  from the Git index while remaining available to authorized local/server paths.
- Require `scripts/check_public_release.py` to pass on the exact Git inventory
  used for every public archive and deployment.
- Require `scripts/check_git_history.py` to pass before any release tag. The
  clean public root passes. The separate private legacy checkout still fails by
  design because excluded resources remain reachable in its earlier commits.
- Registry schema v1.1 now separates rights `status`, intended `tier`, and
  `provisioning.mode`. ELLIPSE is therefore rights-reviewed `green` while still
  being a non-runtime `evaluation-benchmark` with every payload flag disabled.
- `.research/` and all non-metadata benchmark files are blocked by Git,
  container-context, and public-release checks. Only the reviewed ELLIPSE
  manifest and analysis plan may enter the public inventory.
- `data/open/` is also an exact registry allow-list: changing a private payload's
  name or extension and placing it there cannot bypass the release gate.
- The current inventory passes that gate after adding only the pinned Open
  English WordNet and TUBELEX derived artifacts, manifests, and notices and
  removing restricted raw payloads from the index. The gate must be rerun for
  every later release.

Exit gate: the public artifact contains only registry-listed `green` resources.

## Phase 1 — privacy-safe service boundary

Status: **application controls and a deployment template implemented;
institutional infrastructure work pending**

Implemented controls include pseudonymous document labels, automatic source-
widget clearing after successful analysis, aggregate-only Session State,
uncached user exports, generic error messages, operator hard limits, ZIP bomb
checks, explicit deletion, a 30-minute aggregate-result expiry check, and a
typed framework-independent `AnalysisConfig` / `analyze_text` service boundary.
Server-only lookup additionally has a framework-independent, monotonic,
content-free per-session document budget with failed/short-query counting,
Retry-After-equivalent responses, cooldown, and deletion coverage.
The Streamlit path now executes one analysis per subprocess. The parent sends
source only through a bounded stdin pipe, receives typed aggregate frames on a
dedicated descriptor, rejects malformed/oversized results, requires normal
worker exit, and enforces a monotonic deadline that covers startup, resource
loading, analysis, and result transfer. Focused tests cover equivalence,
high-entropy canaries, timeout/reaping, input bounds, and a Streamlit-style
`BaseException` during progress handling.

Remaining work:

- add real-browser WebSocket disconnect and in-flight **Delete data**
  cancellation tests; the 120-second worker deadline is currently the backstop
  when Streamlit cannot signal disconnect immediately;
- add container-level process-group/FD leak and concurrent-load tests, plus
  authenticated IP/account/global rate limiting shared across every production
  worker;
- review and instantiate the Tokyo Cloud Run pilot template in a newly created,
  region-pinned institutional project; configure the regional load balancer,
  IAP, Cloud Armor, disabled `_Default` sink, disabled load-balancer logging,
  sink-inventory verification, IAM, VPC egress, and read-only lexical bucket;
- verify deletion, backup exclusion, egress denial, and cross-session isolation;
- complete institutional legal, privacy, security, and ethics review.

The template is not a deployment approval. Real learner essays remain blocked
until IAP authentication and the infrastructure acceptance tests are complete;
anonymous operation is limited to synthetic or already-public text.

Exit gate: canary learner text is absent from logs, cache, disk, backup, traces,
and another user session under success and failure tests.

## Phase 2 — open corpus and lexical-resource layer

Status: **started; first TUBELEX production axis admitted**

Completed or implemented in code:

- deterministic, POS-agnostic `open_flemma` normalization with ambiguity
  retention and a resource-sensitive behavior fingerprint;
- deterministic Open English WordNet 2025 build and runtime artifact;
- POS-agnostic token/type polysemy and noun/verb hypernym-depth baselines;
- a pinned TUBELEX-EN Treebank aggregate integration with a dedicated,
  Punkt-free adapter whose source lineage is NLTK 3.8.1 and whose audited
  production runtime is NLTK 3.10.0, explicit
  coverage, add-one Zipf frequency including unseen units, and Beta(1,1)-
  smoothed log video/channel prevalence; its 515,292-row deterministic derived
  artifact and runtime identity pins have passed the full test and release
  inventory gates;
- an offline, deterministic MASC 3.0.0 data-only ZIP builder for surface
  unigram/document frequency and within-document bigram/trigram aggregates;
- a verified official Nation 10,000-headword resource for server-only aggregate
  lookup; and
- a deterministic official Nation 25,000-family server index containing 25,000
  families and 75,679 forms, with runtime source/artifact verification.

Post-v1 or conditional next work (not on the v1.0 critical path):

1. run and admit the MASC 3.0.0 aggregate build once the local archive identity
   is supported by a normally validated official download, official mirror, or
   independently confirmed checksum;
2. build OANC as the American multi-genre frequency/range and raw n-gram axis;
3. create document frequency, contextual diversity, dispersion, and register
   entropy tables with corpus/version-specific names;
4. add dated Wikipedia and licence-filtered PMC OA register artifacts;
5. derive frequency, range, MI, MI2, t-score, and directional association
   artifacts from those pinned corpora; and
6. report corpus size, coverage, smoothing, tokenization, and uncertainty.

The TUBELEX source is `tubelex-en-treebank.tsv.xz` at commit
`7cb5fb36add76b83a266d1967536e1a1d3faa513` (4,152,940 bytes; SHA-256
`4096022259d5eaa7261c3bf22c3b0af9fd58ae8eebe17894c0b34a163954f936`). It
declares 171,805,865 tokens, 613,309 source vocabulary rows, 105,733 videos, and
68,405 channels. The runtime-safe index contains only adapter-compatible
aggregate rows and no subtitles or source identifiers. The deterministic index
contains 515,292 rows, is 4,572,297 bytes, and has SHA-256
`3731f23f3385ed630777ff56b5edbed5db46eee256ededceb0ac213016f31675`.
TUBELEX scores retain their own names and are not treated as COCA-comparable.
Category entropy remains deferred until a shrinkage/Jensen–Shannon formulation
is validated.

The source review selected the roughly 500,000-word MASC 3.0.0 data-only ZIP
as the pipeline prototype because it is compact and published under CC BY 3.0
US. OANC (14,623,927 words; about 625 MiB compressed and 4.8 GB expanded) is a
second-stage source. The existing ANC Second Release frequency tables are not
an OANC substitute: that release also contains restricted material. No bulk,
reproducible OANC bigram download was located. See
[`open-corpus-source-review.md`](open-corpus-source-review.md).
The COCA dependency audit and multi-axis replacement contract are recorded in
[`coca-free-metric-plan.md`](coca-free-metric-plan.md).

As observed on 2026-07-22, the ANC download host failed normal TLS certificate
validation and publishes no archive checksum. The build must not disable TLS
verification; integration therefore waits for a repaired certificate, an
official safe mirror/checksum, or direct checksum confirmation from ANC.

Exit gate: every statistic can be rebuilt byte-for-byte from pinned green
inputs, or its documented nondeterminism is bounded and tested.

## Phase 3 — TAALES-open extensions

Status: **pending; post-v1 and non-blocking**

- Implement Python 3 metric modules rather than invoking the legacy app.
- Classify every target index as equivalent, open reconstruction, approximate,
  or unsupported.
- Never label a replacement statistic `COCA_*`; corpus-specific method cards are
  required.
- Use legacy TAALES only for offline numerical comparison on public/synthetic
  fixtures; never deploy its resource tables.
- Add POS-aware tokenization/tagging through a replaceable local adapter.

This phase is not a commitment to reproduce all TAALES indices. A candidate
index is admitted only when its construct, formula, resource requirements,
length sensitivity, and incremental interpretive value are documented and it
does not duplicate the stable v1.0 set. Permission-pending or unavailable
corpora cannot hold the public release.

Exit gate: method cards and regression fixtures document formula, resource,
coverage, and expected difference for every exposed index.

## Phase 4 — validation and public evidence package

Status: **started; Layer 3 identity, privacy boundary, verifier, and analysis
plan implemented; no confirmatory metric–rating result has been computed**

The public evidence package combines the three layers defined above without
turning the application into an assessment system.

### Layer 1 — computational verification

- Maintain hand-calculated fixtures for formulas and boundary behavior.
- Add deterministic perturbations for repetition, vocabulary substitution,
  coverage loss, and text length.
- Require property tests for boundedness, monotonicity where theoretically
  expected, serialization stability, resource identity, and random-seed use.
- Treat [`tests/fixtures/v1_golden/manifest.json`](../tests/fixtures/v1_golden/manifest.json)
  as the canonical end-to-end fixture over the actual NGSL, OEWN, and TUBELEX
  runtime resources. Its two CC0 texts are project-authored public test material,
  not learner or LLM-generated writing. JSON bytes and workbook sheet/cell
  snapshots are normative; XLSX binary hashes remain provisional until the
  release image digest is fixed.

### Layer 2 — synthetic measurement benchmark

- Treat
  [`benchmarks/synthetic/pilot-protocol.json`](../benchmarks/synthetic/pilot-protocol.json)
  as the frozen machine-readable contract and
  [`synthetic-pilot-protocol.md`](synthetic-pilot-protocol.md) as its human-
  readable interpretation. It currently authorizes no generation.
- Run the separately gated 48-document pilot before any core generation. The
  pilot covers 12 topics × 2 genres × 2 register instructions × one dated model
  snapshot × one replicate. It omits the neutral condition so the smallest
  useful run can test the direct plain/formal manipulation.
- Publish a core design of 12 topics × 2 genres × 3 register instructions ×
  2 fixed model snapshots × 3 replicates (432 documents) only under a separate
  reviewed protocol and budget authorization after the pilot. The 4,000-yen
  project reserve is not available to automatic continuation. Up to two
  additional models are optional and must not delay v1.0.
- Preserve complete prompts, request/response records, model identifiers,
  settings, dates, token use, costs, hashes, QC flags, tool commit, lockfile,
  and resource hashes. Never publish credentials.
- Guarantee reproducibility of analysis from the released texts. Treat exact
  regeneration from a hosted model as best effort, not as a guarantee.
- Limit claims to measurement responsiveness, stability, and sensitivity. Do
  not infer human proficiency, CEFR, writing quality, or AI authorship.

### Layer 3 — external human-writing association

- Use the author-maintained
  [ELLIPSE repository](https://github.com/scrosseye/ELLIPSE-Corpus) as a
  `fetched` / `evaluation-benchmark` research resource under its recorded
  CC BY-NC-SA 4.0 terms.
- Pin acquisition date, upstream commit, source size, SHA-256, license evidence,
  exclusion rules, and an aggregate-only output policy.
- Use [`benchmarks/ellipse/manifest.json`](../benchmarks/ellipse/manifest.json)
  as the machine-readable data contract and
  [`scripts/fetch_ellipse.py`](../scripts/fetch_ellipse.py) as the only default
  provisioning path. The networked acquisition step checks only the opaque
  outer archive and never opens it. After network access is disabled, content
  verification checks the outer archive before opening it, rejects any
  unexpected or unsafe member, verifies the encrypted final test CSV, and
  creates no analysis destination before the complete 6,482-row contract passes.
- Treat [`benchmarks/ellipse/analysis-plan.json`](../benchmarks/ellipse/analysis-plan.json)
  as the frozen machine-readable analysis plan; its rationale and interpretation
  boundary are documented in
  [`ellipse-confirmatory-analysis-spec.md`](ellipse-confirmatory-analysis-spec.md).
- Treat the audited final files as 6,482 unique essays over 44 prompt labels.
  All 44 prompts occur in both supplied splits, so use prompt-grouped
  resampling for unknown-prompt claims. Record that the preprint also contains
  an inconsistent 29-prompt statement; see
  [`benchmark-resource-audit-2026-07-23.md`](benchmark-resource-audit-2026-07-23.md).
- Use the vocabulary analytic rating as the primary external criterion and the
  holistic rating as secondary. Treat PERSUADE holistic quality as a different
  construct, not a substitute vocabulary criterion.
- Report the pre-adjudication Vocabulary kappa of .518 separately from the
  final MFRM essay-ability reliability of .94. Do not call the rating an
  error-free gold standard, and do not reconstruct final scores from the raw
  rater file until its zero coding and 14 unmatched IDs are resolved.
- Freeze a small metric set and controls before analysis. Control text length,
  established diversity/frequency measures, and prompt. Evaluate unknown-prompt
  generalization with prompt-grouped splits rather than relying only on the
  provided train/test division.
- The frozen primary comparison is 44-fold leave-one-prompt-out macro-MAE for
  Vocabulary: a baseline of log-length spline, MTLD, and NGSL Beyond-K2 versus
  the same model plus type-weighted TUBELEX Zipf frequency and channel
  prevalence. Prompt bootstrap uses 10,000 resamples and seed `20260723`.
- Keep all corpus processing local. Do not send learner texts to an external LLM
  API, distribute a fitted score predictor, or expose individual predictions.

The results are published as one rerunnable validation report. A null or weak
association is still a useful boundary on interpretation and must not block the
software release.

Exit gate: a clean environment reproduces Layer 1 exactly, reproduces Layer 2
from the public benchmark, and reproduces Layer 3 after the operator fetches the
pinned official files; every claim remains inside the interpretation contract.

## Phase 5 — public release, maintenance, and optional controlled pilot

Status: **pending**

- Release v1.0 for local use with CI, lockfiles, method/interpretation cards,
  CITATION.cff, a complete resource inventory, and a Zenodo DOI.
- Limit any unrestricted hosted demonstration to curated synthetic and already-
  public texts. A public source release does not require an anonymous endpoint
  that accepts real learner writing.
- Maintain released schemas and numerical behavior through explicit semantic
  versioning, migration notes, and fixed regression fixtures.
- Develop a client-side Panel A only where its results remain equivalent to the
  main implementation and its dependency/resource scope is explicit.
- Continue with de-identified institutional data only after approvals and only
  when a concrete educational need justifies the operational burden.
- Prefer authenticated institutional delivery for minors, graded work, or
  content likely to contain sensitive information.
- Complete accessibility, security, deletion, incident-response, and
  subprocessor-change reviews before any unrestricted public input endpoint.

Every quarter, record which proposed work improves the public tool,
reproducibility, or user understanding within 90 days; its added maintenance
surface; whether a negative result remains publishable; and what work is being
stopped. Optional research or infrastructure that delays release/maintenance by
four weeks is deferred.

Exit gate: the public release can be independently rebuilt and cited, and any
operated service can demonstrate who processes what data, where, for how long,
under which terms, and how deletion and incident response are verified.
