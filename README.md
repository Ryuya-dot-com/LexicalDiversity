# Lexical Diversity & Frequency-Profile Analyzer

A Streamlit research tool that, from English text, computes **list-independent
lexical diversity** (Panel A) and a **frequency-list profile**
(Panel B), plus open semantic-network metrics derived from Open English WordNet
and an open everyday-language frequency/contextual-diversity axis derived from
published TUBELEX-EN aggregates.

> **Interpretation contract.** The outputs describe properties of the submitted
> text under the recorded tokenizer, normalizer, reference resource, and
> parameters. They are not grades, CEFR levels, diagnoses of vocabulary
> knowledge, or direct estimates of writer proficiency. Compare texts only under
> identical settings and resource versions, and preferably at comparable length
> and for comparable prompts, topics, genres, and sampling conditions. A higher
> or lower value is not automatically better.

```bash
python3 -m pip install -r requirements.txt
python3 -m streamlit run app.py
```

## Reproducible release contracts

The current source identity is `0.9.0-dev.0`; it is not a published release.
Application SemVer and the independent public output-schema version have one
machine-readable authority in [`ldfreq/release.json`](ldfreq/release.json).
Every JSON export and XLSX metadata sheet records both identities. The
[versioning and immutable-release policy](docs/versioning-and-release.md),
[`CHANGELOG.md`](CHANGELOG.md), development check, and tag-only workflow prevent
a dirty tree, `-dev` identity, lightweight/wrong tag, or undocumented version
from being presented as a release.
The public repository now starts from a reviewed clean-history root. The former
22-commit repository, whose reachable history contains legacy resource paths,
was renamed and made private; the local legacy checkout remains attached to it.
The [public-history migration record](docs/public-history-migration.md) preserves
the audit and bootstrap evidence. Release tags remain blocked until the later
version, image, citation, and archive gates pass.
Build the reviewed, deterministic bootstrap archive without copying `.git/` or
trusting the mixed staging area:

```bash
python3 scripts/build_clean_public_candidate.py \
  --output /tmp/ldfreq-clean-source.tar.gz \
  --evidence-output /tmp/ldfreq-clean-source-evidence.json
```

The evidence file records every admitted path, byte size, SHA-256, normalized
mode, source `HEAD`, and dirty-tree state. Outputs are created exclusively, so
an earlier review artifact cannot be overwritten accidentally.

`requirements.txt` is a developer convenience file, not the reproducible v1.0
environment. Clean CI uses CPython 3.12.10 and one SHA-256-pinned Linux x86_64
wheel per production/CI dependency, then runs
`scripts/check_runtime_environment.py`, the complete test suite, and the
public-inventory gate. The production Dockerfile pins the reviewed
`3.12.10-slim-bookworm` linux/amd64 child manifest rather than a mutable tag or
the multi-platform index; its registry evidence is recorded in
`deploy/cloud-run/base-image.json` and can be rechecked with
`python scripts/check_base_image_identity.py --remote`. The candidate
metric/resource/output boundary is frozen
in [the v1.0 scope contract](docs/v1-scope-freeze.md) and its
[machine-readable schema](docs/v1-metric-scope.json).
The remaining byte-reproducibility gate is to build the complete application
image from a clean tracked checkout, record that resulting image digest and
provenance, and reproduce the golden outputs inside it.
The [v1 golden fixture](tests/fixtures/v1_golden/README.md) uses two public CC0
test texts and the actual NGSL, OEWN, and TUBELEX runtime resources to pin
canonical JSON and Excel cell outputs without using learner data or an API.

The manual `Candidate application image` workflow is the pre-release image
gate. It accepts only protected `main`, builds the production stage twice from
scratch with the commit timestamp and pinned Dockerfile frontend, requires both
image IDs to match, and uses a separate non-deployable stage to recompute the
golden outputs offline. Only a Critical-clean candidate is pushed to GHCR under
its full commit, attested, and accompanied by an SPDX SBOM, Grype JSON report,
raw registry manifest, and canonical evidence artifact. A candidate digest is
not a Git tag, GitHub Release, stable image, or deployment approval.

The [Synthetic pilot protocol](docs/synthetic-pilot-protocol.md) is also frozen,
but has not been executed: no essays have been generated, no external API has
been called, and no budget has been spent. Current priorities and explicit
deferments are tracked in the
[2026–2028 strategic roadmap](docs/strategic-roadmap-2026-2028.md) and the
[2026-07-24 decision log](docs/decision-log-2026-07-24.md).

The wheel choice is reproducible and fail-closed. With CPython 3.12.10 and pip
25.0.1, download the reviewed platform set to an empty temporary directory,
then compare it byte-for-byte with both committed locks:

```bash
python3.12 scripts/download_linux_wheels.py --dest /tmp/ldfreq-linux-wheels
python3.12 scripts/build_linux_wheel_locks.py \
  --wheel-dir /tmp/ldfreq-linux-wheels --check
```

For an intentional dependency migration, add `--allow-lock-change` to the
download step, inspect the retained wheel set, then use `--write` instead of
`--check`. The normal developer install remains platform-native; the strict
Linux locks define CI and production, where silent source builds and dependency
resolution are disabled.

The complete Streamlit analysis path intentionally requires a POSIX host
(Linux or macOS) because every submitted text is processed in a one-shot,
deadline-controlled subprocess. On Windows, run the application in a Linux
Docker container or WSL; the UI disables **Analyze** instead of bypassing this
privacy boundary with an in-process fallback.

The default public test suite does not depend on an operator's server-only
Nation artifact. After provisioning its verified artifact, run the additional
Streamlit integration test explicitly with
`LDFREQ_RUN_SERVER_INTEGRATION=1 python -m pytest tests/test_app_query_guard.py`.

## Panel A — Lexical Diversity (12 indices, list-independent)

| | indices |
|---|---|
| ratios | TTR, RTTR (Guiraud), CTTR, Herdan C, **Maas ↓** |
| segment/window | MSTTR, MATTR |
| sampling / length-adjusted procedures | MTLD, HD-D, vocd-D |
| repetition | **Yule's K ↓**, Yule's I |

`↓` = *lower* means more diverse (Maas, Yule's K). Formula-based indices are
implemented directly, while stochastic or procedure-dependent measures
(MTLD, vocd-D, P_Lex, S) record their parameters for reproducibility. Each index
has a project screening or computational minimum-token floor; below it the value
is still shown where computable, with a warning column for interpretation (cf.
Kyle et al. 2024; Kojima & Yamashita 2014). These floors are transparent
pragmatic flags, not validated reliability cutoffs: meeting a floor does not by
itself make an estimate stable, reliable, or comparable.

## Panel B — Frequency-based richness (selected reference list)

- **Lexical Frequency Profile**: token coverage per frequency band + off-list, cumulative.
- **Privacy-preserving lookup**: aggregate bands and coverage are retained;
  token-, word-, and observed-surface-form rows are intentionally discarded.
- **Coverage thresholds**: smallest selected-list band reaching **90 / 95 / 98 %**
  matched text coverage. Proper nouns, marginal words, acronyms, and other
  potentially known items are not automatically credited unless matched by the
  selected list/normalizer.
- **Advanced Guiraud** (advanced types / √N), **% beyond-K** (advanced *families* /
  total families, Laufer's Beyond-2000), **mean rank / log-rank**.
- **Interpretation**: more low-frequency, advanced-band, or off-list material
  means less match to the selected list under the recorded lookup policy. It is
  not automatically evidence of better writing or greater proficiency, and an
  off-list item is not automatically an unknown word.
- **P_Lex** (Poisson curve-fit; Meara & Bell 2001) and **S** (coverage-curve fit
  `C(x)=ln(x)/ln(S)·100`; Kojima & Yamashita 2014). Both are documented
  approximations over the selected list. S is not comparable to K&Y's published
  values unless the same BNC-spoken family lists and preprocessing are used.
- **Interactive diagnostics**: Plotly coverage profile, P_Lex observed-vs-fitted
  distribution, and S empirical-vs-fitted curve.
- **Index profile radar**: Panel A/B indicators are rescaled to heuristic 0-100
  profile scores to show the shape of a text's diversity, repetition,
  sophistication, and in-list coverage. Use raw values for reporting.
- **Band-wise diversity**: per-band type/token counts plus MTLD/MATTR/HD-D where
  computable. Low-frequency bands below the recommended floor are still reported
  with a warning column.
- **Batch workflow**: upload one or more `.txt` files or a `.zip` archive of
  `.txt` files. Public hard ceilings are 5 MB per text, 20 MB total extracted
  text, and 200 documents; defaults are 2 MB, 10 MB, and 100 documents. ZIP
  archive size and compression-ratio checks run before extraction.
- **Exports**: download per-document summaries, descriptive statistics, and
  detail sheets as JSON or Excel (`.xlsx`).
- **Batch diagnostics**: file-by-band coverage, index reliability, and aggregate
  lexical-overlap heatmaps. Token-level off-list tables are not retained.

## Open semantic-network indices

The bundled Open English WordNet 2025 artifact supplies POS-agnostic aggregate
polysemy and noun/verb hypernym-depth means, with token/type weighting and
coverage reporting. These are an open reconstruction, not numerical replicas of
TAALES. The official source asset is version- and SHA-256-pinned; the runtime
ships only a deterministic derived table of about 753 KB.

Polysemy means are conditional on normalized lemmas that match OEWN: the token
mean weights repeated covered lemmas and the type mean gives each distinct
covered lemma one vote. Unmatched lemmas are omitted from these means rather
than assigned zero, so token and type coverage must always be reported with the
values. Hypernym-depth means use a smaller conditional denominator: only covered
items with a noun/verb depth value enter the mean. `depth_covered_tokens`,
`depth_covered_types`, `depth_token_coverage`, and `depth_type_coverage` make
that eligible subset explicit relative to all input lemmas/types.

The current lookup does not tag part of speech or disambiguate the sense used in
the submitted sentence. Higher polysemy therefore means more dictionary-listed
senses, not that the writer used more meanings; greater depth means a longer
recorded noun/verb hypernym path, not greater abstractness, specificity,
quality, or proficiency.

Rebuild it from the official CC BY 4.0 release with:

```bash
python3 scripts/fetch_open_resources.py
```

## Open everyday-language frequency and dispersion

The TUBELEX-EN Treebank-variant integration uses only the upstream project's
already-published aggregate frequency table. The pinned source is
`tubelex-en-treebank.tsv.xz` at commit
`7cb5fb36add76b83a266d1967536e1a1d3faa513` (4,152,940 bytes; SHA-256
`4096022259d5eaa7261c3bf22c3b0af9fd58ae8eebe17894c0b34a163954f936`). Its
declared reference totals are 171,805,865 tokens, 613,309 source vocabulary
rows, 105,733 videos, and 68,405 channels.

The runtime reports token/type coverage, add-one-smoothed Zipf frequency, and
Beta(1,1)-smoothed log video/channel prevalence, with both token- and
type-weighted means. Unseen lookup units remain in the corpus means at the
documented smoothing floor instead of silently disappearing. A dedicated
adapter applies NFKC, maps common typographic apostrophes such as `’` to ASCII,
lower-cases, pre-segments sentences deterministically, and invokes the
model-free `TreebankWordTokenizer` directly without Punkt. TUBELEX used NLTK
3.8.1 for the source variant; production pins NLTK 3.10.0 after confirming that
its word-tokenizer rules remain compatible. The derived server index retains
only lookup-compatible aggregate rows.

Token-weighted means count every occurrence, so repeated high- or low-frequency
units can dominate them; type-weighted means give each distinct lookup unit one
vote and therefore emphasize vocabulary composition. Report both with token and
type coverage. The warning shown below 50 TUBELEX lookup tokens or 20 lookup
types is a pragmatic project display flag, not a validated reliability boundary;
passing it does not establish score stability.

These are TUBELEX-specific measures of mixed global YouTube language exposure,
not COCA measures and not numerically comparable replacements for COCA spoken
or register scores. Raw category entropy is not exposed in the first release:
rare-word category profiles need a shrinkage/Jensen–Shannon design and
out-of-sample validation before they become a defensible statistic. No subtitle
text, video/channel identifier, title, or source document name is bundled.

## Counting unit & Panel B lookup

- **Counting unit**: `token` only. Panel A uses lower-cased surface tokens; no
  lemmatization is applied to Panel A diversity indices.
- **TUBELEX lookup unit**: a separate NFKC/typographic-apostrophe/lower
  Treebank-tokenizer adapter is applied directly to the submitted text. It is
  independent of Panel B
  flemma/headword lookup and requires no Punkt runtime model.
- **Panel B lookup**: maps each token to the selected frequency list's lookup
  unit. NJ8/NGSL/headword lists use flemma/head-style lookup; the BNC/COCA word
  family list maps related forms to family heads before assigning a band.
  User-supplied AntWordProfiler/Range baseword lists are also supported.
- **Panel B normalizer** (pluggable, recorded in metadata): used as a fallback
  when the raw token is not directly present in the selected list. True
  POS-distinguished *lemma* is not offered for NJ8 because the list carries no POS.
  - `open_flemma` — public default. A project-owned, deterministic POS-agnostic
    normalizer built from inflection-only rules, the redistributable NGSL 1.2
    research form table, and rule-candidate validation against Open English
    WordNet 2025. It does not read AntBNC or COCA. Unique mappings include
    `went → go`, `better → good`, and `children → child`; unresolved homographs
    such as `saw`, `found`, and `left` retain their surface form rather than being
    guessed. The algorithm and effective resource vocabulary are fingerprinted
    in the exported version string.
  - `simplemma` — retained as an optional comparison back-end; it is no longer
    the public default (for example, version 1.2.0 maps `went → wend`).
  - `antbnc` — optional local-only **NWLC近似 (AntBNC)**: *approximate* parity with Mizumoto's
    [New Word Level Checker](https://mizumot.com/nwlc/). Uses the **AntBNC Lemma
    List by Laurence Anthony**, downloadable from
    [laurenceanthony.net/software/antconc](https://www.laurenceanthony.net/software/antconc/)
    (higher-quality English lemmatization than simplemma, e.g. `went → go`).
    NWLC uses an AntBNC list **manually matched to JACET headwords**; the raw list
    gets you close but **not bit-identical**. Strict comparison also requires
    matching tokenization, off-list/variant policy, and the exact list version — so
    use the preset, and never label output "identical".

Panel B coverage will not necessarily match LexTutor/VocabProfile unless the same
frequency-list version, word-family expansion, tokenizer, proper-noun/number
policy, and flemma/lemma normalization are used. Public BNC/COCA analysis uses
only the verified official Paul Nation 10,000-headword or 25,000-family
server-side resources. **Nation BNC/COCA** is the name of a pedagogical
headword/family list; selecting it does not load a COCA corpus dataset or produce
TAALES COCA frequency/range scores. The legacy EAPFoundation XLSX/PDF is not
public-enabled.

AntWordProfiler/Range baseword lists can be supplied with `LDFREQ_RANGE_PATH`
pointing to either a single level-list file or a directory of files such as
`basewrd1.txt`, `basewrd2.txt`, ... . The parser treats an unindented word as a
new baseword family and indented words as family members of the current baseword.

### Reproducibility — version recording

Every analysis records, in the on-screen metadata banner and the exported JSON:
list name + version, **lemmatizer name + version** (e.g.
`open_flemma open-flemma-1.0.0+…`),
unit, thresholds, and all index parameters (`mtld_threshold`, `vocd_seed`, …).
Pin these to reproduce or compare results.

Exports are available in two formats:

- JSON preserves the nested machine-readable payload.
- Excel (`.xlsx`) provides separate sheets for summary, Panel A, frequency
  profile, descriptive statistics, thresholds, P_Lex/S, band-wise diversity, and
  semantic-network and TUBELEX aggregate metrics, plus machine-readable
  interpretation cards and quality flags. Multi-file exports also include batch
  bands, reliability, and aggregate overlap sheets.

## Licensing

- **Source code**: MIT (see `LICENSE`).
- **Bundled open data**: NGSL is included under CC BY-SA 4.0. The derived Open
  English WordNet 2025 metric table is included under CC BY 4.0 with its NOTICE.
  The TUBELEX-EN runtime index is derived solely from the project's published
  frequency aggregate under BSD-3-Clause and retains its attribution and
  transformation notice; the underlying subtitles are not included.
- **Reference lists**: New JACET8000 is approved for redistribution in this
  project. Paul Nation's official 10,000 BNC/COCA headwords are CC BY-SA 4.0
  and admitted for aggregate-only server use, but are not bundled or delivered
  to clients. The EAPFoundation XLSX/PDF remain `permission-pending` for public
  SaaS and are being replaced by an official Nation-derived family index.
- **Server-only lemmatizer data**: AntBNC is not bundled and remains
  `permission-pending` for public-SaaS use. It is excluded from the public
  server-only eligible set; only an independently authorized private deployment
  may load it with `LDFREQ_SERVING_MODE=local` and
  `LDFREQ_ALLOW_LOCAL_RESTRICTED=1`.
- ShareAlike or third-party data terms bind the *data files*, not the
  MIT-licensed source code.

## Streamlit deployment

Real learner writing must use a region-pinned, institutionally reviewed
self-hosted deployment. Streamlit Community Cloud is limited to synthetic or
already-public fixed examples; it is not the production target for learner
submissions. See the
[privacy and data-handling specification](docs/privacy-and-data-handling.md)
and the non-approval [Cloud Run Tokyo pilot template](docs/cloud-run-tokyo-pilot.md).
The UI displays a synthetic/public-text-only banner unless
`LDFREQ_REAL_WRITING_APPROVED=1`; that flag may be set only by the reviewed
release configuration after all institutional and infrastructure gates pass.
It is a display fail-safe, not approval by itself.

The default public application is wired for NGSL, New JACET8000, and the
TUBELEX-EN aggregate axis. The exact 4,572,297-byte TUBELEX artifact has passed
byte-for-byte rebuild checks, the full test suite, and the public-inventory
gate. AntBNC and BNC/COCA
payloads are excluded from Git. Only the green official Nation
resources may be enabled in the server-only public gate; AntBNC and the legacy
EAPFoundation snapshot remain unavailable there. On Cloud Run, the Nation
resource is supplied only through a `read_only=true` mount of a dedicated
Tokyo-region Cloud Storage bucket that contains static lexical assets and no
learner data. The runtime service account receives bucket-scoped
`roles/storage.objectViewer` on that bucket and no object-write permission.
The reference network routes all egress through a no-NAT Direct VPC, permits
only the restricted Google API path needed for this mount, and places the
project and bucket inside a reviewed VPC Service Controls perimeter.
Enable only the required runtime IDs:

```bash
LDFREQ_SERVER_ONLY_RESOURCE_IDS=bnc_coca,nation_bnc_coca_families
LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED=1
```

The second setting is an operator attestation, not a substitute for permission.
`antbnc` and the legacy `bnc_coca_families` EAPFoundation snapshot are not
eligible IDs and cannot be activated by this switch.
The app never offers the payloads as downloads; exports contain aggregate
analysis results, attribution, and resource/version metadata only. Server-only
analysis also rejects documents below 100 lexical tokens or 20 distinct types.
It also applies a content-free, monotonic session budget per submitted document,
with a minimum 20-credit request charge, counts failed/short-text requests, and
returns Retry-After-equivalent seconds when the budget or failure cooldown
blocks a request. **Delete data** removes this state. This blocks trivial
one-word rank probes but is not a mathematical
non-inference guarantee: a new session starts a new bucket and multiple workers
do not share it. Authentication plus an IP/account/global limiter and anomaly
detection at the ingress/shared-infrastructure layer remain deployment
requirements before unrestricted public use.

The Tokyo pilot is IAP-authenticated and is not an anonymous real-writing
service. Its reference path is regional external Application Load Balancer ->
Cloud Armor -> IAP -> Cloud Run `asia-northeast1`, with direct `run.app` access
disabled. Cloud Armor sees the HTTP/WebSocket upgrade request, not later
WebSocket frames; the current session guard can also be reset with a new
session. Each analysis now runs in a one-shot subprocess with a 120-second
default wall-clock deadline: input crosses an in-memory pipe, worker
stdout/stderr are discarded, resources are loaded behind the rights gate in the
child, and timeout/error paths terminate and reap the process. The production
entrypoint also discards Streamlit stdout/stderr; the disabled Cloud Logging
`_Default` sink and disabled load-balancer logging leave runtime request,
application, access, security, and error logs not stored or queryable. Only the
fixed 400-day `_Required` control-plane audit log remains. Immediate
WebSocket-disconnect/user-delete cancellation and a durable authenticated
account quota remain explicit release blockers.

Base64 secrets remain supported for small, authorized private overrides outside
the reference Cloud Run path. They are not the Cloud Run transport for the
official Nation family index: its current compressed runtime artifact is
471,046 bytes, above
[Secret Manager's 65,536-byte secret-version payload limit](https://cloud.google.com/secret-manager/quotas).
The app materializes a supported private secret into
`.streamlit/runtime_lists/` at startup when the corresponding secret is present.

Generate a TOML block from local copies:

```bash
python3 scripts/make_streamlit_secrets.py --only nation-family > streamlit-secrets.local.toml
```

Paste the generated `[ldfreq]` block into the secrets configuration of an
authorized private self-hosted deployment. Do not commit the generated TOML.
The supported secret keys are:

- `LDFREQ_NJ8_CSV_B64`
- `LDFREQ_ANTBNC_TXT_B64`
- `LDFREQ_BNCCOCA_ZIP_B64`
- `LDFREQ_RANGE_ZIP_B64`
- `LDFREQ_NATION_BNCCOCA_RUNTIME_ZIP_B64` (server artifact + manifest + NOTICE)

The AntBNC and Range keys are honored only in an authorized private deployment
with both `LDFREQ_SERVING_MODE=local` and
`LDFREQ_ALLOW_LOCAL_RESTRICTED=1`; the public gate materializes only its
explicitly enabled Nation resource IDs. Deployments can alternatively set file paths with `LDFREQ_NJ8_PATH`,
`LDFREQ_BNCCOCA_PATH`, `LDFREQ_BNCCOCA_FAMILIES_PATH`, `LDFREQ_RANGE_PATH`, and
`LDFREQ_ANTBNC_PATH`. The official Nation family index uses either
`LDFREQ_NATION_BNCCOCA_INDEX_PATH` or `LDFREQ_NATION_BNCCOCA_INDEX_DIR`.

Build that index from a verified local copy of the official ZIP; the command
performs no network access and accepts no checksum override:

```bash
python3 scripts/build_nation_bnc_coca_index.py \
  --source /private/path/BNC_COCA_25000.zip \
  --acquired-on YYYY-MM-DD
```

The default output is `.streamlit/runtime_lists/nation_bnc_coca_25000/` and is
ignored by Git. Runtime loading verifies the adjacent manifest, source identity,
artifact byte size, SHA-256, schema, and row count before use.

## Layout

```
app.py              Streamlit UI
ldfreq/
  tokenizer.py      tokenization
  indices.py        Panel A (12 diversity indices)
  frequency.py      Panel B (LFP, coverage, AG, P_Lex, S, band-wise)
  batch.py          multi-document diagnostics
  exporting.py      JSON/Excel export helpers
  uploads.py        .txt/.zip upload decoding and limits
  open_flemma.py    project-owned open flemma algorithm and resource loader
  lemmatizers.py    pluggable: open flemma / simplemma / AntBNC / word form
  privacy.py        session-retention and pseudonymization controls
  query_guard.py    content-free server-only session query budget
  semantic_network.py  Open English WordNet lookup and build logic
  tubelex.py        pinned aggregate builder, tokenizer adapter, and metrics
scripts/                deployment and reproducible resource-build helpers
docs/                   resource governance and privacy specifications
data/open/              cleared, reproducibly derived open resources
data/NJ8/               bundled New JACET8000 data + manifest
data/antbnc/            governance manifest; local AntBNC payload is ignored
data/bnc_coca/          governance manifest; local BNC/COCA payloads are ignored
data/ngsl/               bundled NGSL data + manifest (CC BY-SA 4.0)
NGSL/, NationBNCCOCA/    optional original local downloads (git-ignored)
```

## Roadmap

See [`docs/roadmap.md`](docs/roadmap.md) for phase gates, completed controls,
release blockers, and research-validation criteria.

1. Add register-specific frequency/n-gram statistics, beginning with a safely
   identified MASC build and then OANC.
2. Add POS-aware and contextualized semantic-network metrics with explicit
   incremental-validity testing.
3. Optional lightweight, client-side **Panel-A-only** build on Cloudflare Pages +
   Pyodide (privacy, zero backend) for public use.
