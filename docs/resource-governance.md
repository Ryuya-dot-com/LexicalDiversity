# Resource governance

This document is the Phase 0 release gate for lexical resources used by the
LexicalDiversity web application. It governs data files, derived lookup tables,
corpus statistics, models, and legacy application bundles independently of the
repository's source-code license.

The machine-readable source of truth is
[`data/resource_registry.json`](../data/resource_registry.json). A resource that
is absent from the registry, has an incomplete registry record, or fails its
recorded hash must not enter a public build.

## Status gates

| Status | Meaning | Public repository | Public server use | Client delivery | Private local use |
| --- | --- | --- | --- | --- | --- |
| `green` | Source, applicable terms, evidence, and intended use have been reviewed. | Allowed under the recorded conditions. | Allowed under the recorded conditions. | Only when explicitly allowed in the entry. | Allowed. |
| `yellow` | Provenance is known, but redistribution or public web-use evidence is incomplete. | Metadata only; payload is blocked. | Blocked unless supplied and authorized by the operator or user under separate terms. | Blocked. | Allowed for authorized local evaluation. |
| `red-local-only` | The resource is a migration/research reference and is not cleared for deployment. | Payload and derived bulk tables are blocked. | Blocked. | Blocked. | Allowed only in an access-controlled local environment. |

Status is fail-closed. `yellow` and `red-local-only` resources must not be
copied into containers, static assets, package data, CI artifacts, deployment
secrets, caches, or downloadable exports. A public build must also reject an
unregistered resource rather than silently loading it.

`green` does not mean public domain. Attribution, notices, ShareAlike duties,
field-of-use limits, and other recorded conditions remain mandatory.

## Three independent registry axes

`status`, `tier`, and `provisioning` answer different questions and must be
reviewed independently:

- `status` records the evidence and rights decision for the exact uses stated in
  the entry; it is not a statement that the payload belongs in the product;
- `tier` records the resource's intended role: public-tool runtime,
  offline-evaluation benchmark, or migration reference; and
- `provisioning.mode` records how an authorized environment obtains the payload:
  bundled, operator-injected, fetched into an ignored local root, or retained as
  a local snapshot.

No value on one axis implies a value on another. In particular, a `green`
`evaluation-benchmark` may be rights-reviewed while every public payload flag
remains `false`; `fetched` does not mean redistributable; and a bundled runtime
resource still remains subject to its recorded license conditions. A change in
role, delivery mechanism, or intended use therefore triggers review without
silently promoting either of the other axes.

## Required registry fields

Every entry must contain all of the following fields, even when a value is
unknown. Unknown facts are represented explicitly with `null` and an explanation;
they must not be replaced with an unsupported assumption.

- `id`, `name`, and `category`: stable identity and resource class.
- `tier`: intended role, independently of rights status and acquisition mode.
- `provisioning`: mode, governing specification, default local root, and
  explicit Git, package, container, and CI payload flags.
- `source`: creator or rights holder, primary distribution URL, acquisition
  channel/date, and a concise origin description.
- `license`: license name or rights statement, URL, SPDX identifier where one
  exists, local notice path, scope, and verification state.
- `evidence`: dated, reviewable records supporting source, license,
  redistribution, and web-use claims. A citation is provenance evidence, not by
  itself permission evidence.
- `version`: upstream version and the identity of the local snapshot.
- `hash`: algorithm, scope, value/state, and verification date. Collections may
  use `state: "per-artifact"` when every deployable artifact has its own SHA-256.
- `artifacts`: local path, byte size, SHA-256, and whether the file may enter a
  public build. Directories or incomplete inventories must say so explicitly.
- `redistribution`: separate decisions for repository bundling, server copying,
  client download, and derived bulk data, with conditions.
- `web_use`: separate decisions for public SaaS processing, private server use,
  private local use, and publication of aggregate results.
- `build_provenance`: upstream inputs, transformations, build script, and whether
  the artifact can be reproduced. Direct snapshots must be labelled as such.
- `status`: one of the three gates above, together with a reason and effective
  date.
- `review`: reviewer/authority, last review date, next review trigger, and open
  questions.

An entry is not build-eligible merely because every key exists. For `green`, the
license/evidence must support the exact distribution and web-use flags, deployable
artifacts must have verified hashes, and all required notices must be present.

### Custom-permission assurance

Registry schema 1.3 adds `permission_assurance_contract`. A resource relying on
a bespoke permission rather than a standard license cannot become public from
project-owner testimony alone. Public GitHub distribution and downstream forks,
release archives, container images, public SaaS processing, transformations and
derived results, commercial and non-commercial use, and revocation/expiry terms
must all be covered by an original external permission record. The record must
identify the grantor, the grantor's authority, and the record editor. Its named
rights reviewer must be different from both that editor and the owner-attestor.

The registry may retain a private permission record's SHA-256 and exact resource
artifact bindings so a review decision cannot silently drift to different
evidence or payload bytes. These hashes bind that evidence and artifact only;
they are not used to claim byte equality between mirrors. The automated gate
checks the completeness and internal consistency of the recorded contract. It
does not retrieve the private record or prove that its assertions are true, so
the human independent review remains substantive rather than ceremonial.

## Current decisions

### Green

- **New General Service List (NGSL) 1.2.** The upstream project states CC BY-SA
  4.0. Preserve attribution, the license link, and ShareAlike obligations for
  distributed copies and adaptations.
- **Open English WordNet 2025 lemma metrics.** The pinned CC BY 4.0 release is
  transformed by a deterministic build into a compact polysemy and hypernym-
  depth table. Ship its `NOTICE.md`, identify the changes, and verify both the
  source and artifact hashes before release.
- **Paul Nation BNC/COCA 10,000 headwords.** The ten local bands match the
  official 10,000-headword archive byte-for-byte. The official resource terms
  support use under CC BY-SA 4.0. This product nevertheless keeps the payload
  out of Git and client downloads, injects it server-side, displays attribution,
  and returns aggregate results only.
- **Paul Nation BNC/COCA 25,000-family server index.** A pinned official ZIP is
  reduced deterministically to 25,000 families and 75,679 form mappings from
  `basewrd1`–`basewrd25` only. The 471,046-byte artifact, its manifest, and
  CC BY-SA NOTICE remain in private runtime storage; runtime loading verifies
  the pinned source identity and artifact SHA-256 before aggregate-only use.
- **ELLIPSE corpus at commit `dc3b8f0b`.** The official repository identifies
  the corpus as CC BY-NC-SA 4.0, and the pinned commit archive and primary final
  train/test files have been independently hash-audited. `green` records that
  completed rights review only: ELLIPSE is an offline
  `evaluation-benchmark`, fetched into an ignored local research root, and no
  essay, row-level attribute, individual feature row, prediction, or fitted
  model may enter the application or a public payload.

### Yellow

- **New JACET8000 (NJ8).** The project owner's 2026-07-22 attestation to
  redistribution and public-Web use is retained as evidence, but it is not the
  original grant and has not received an independent complete-scope review.
  NJ8 is therefore `review-pending`: its CSV is absent from the current Git
  tree, releases, packages, container build context/image, CI payloads, and the
  public UI. An operator may supply the exact recorded snapshot only in local
  mode with both `LDFREQ_SERVING_MODE=local` and
  `LDFREQ_ALLOW_LOCAL_RESTRICTED=1`. The loader's
  `parenthetical_variant_expansion_v1` and Panel B's
  `surface_first_rank_lookup_normalized_fallback_v1` are transformations/uses
  that the eventual permission review must cover. This future-tree removal does
  not retract older commits, clones, caches, or third-party copies; the
  reachable-history gate remains a release blocker until that boundary is
  resolved.
- **AntBNC Lemma List 004.** The official download and creator are known, but the
  local bundle has no explicit data-license text or URL establishing third-party
  repository redistribution and public SaaS use.
- **EAPFoundation BNC/COCA complete-copy v2.** The local XLSX/PDF match the
  EAPFoundation downloads, whose custom terms support acknowledged educational
  non-commercial reuse but do not expressly address public SaaS. They are not
  Paul Nation's official university files and will be replaced by a deterministic
  server index built from official `basewrd1`–`basewrd25` data.

Yellow payloads remain blocked from public builds until the rights holder's or
primary distributor's terms explicitly cover the intended repository and web
uses. A public URL or freely downloadable file alone is not sufficient.

## Server-side-only resources

A resource may be configured for server-side lookup without making its payload
downloadable. This narrows the delivery surface but does not by itself grant
public-SaaS processing rights. The following controls are mandatory:

- keep the payload out of the public Git index, package data, container image,
  static directory, CI artifact, and client download;
- inject it at deployment through an operator-controlled read-only volume or
  secret path;
- return only aggregate measures—never list excerpts, token-to-head mappings,
  ranks by submitted word, or a bulk-list endpoint;
- reject server-only analysis below 100 lexical tokens or 20 distinct types,
  consume one session query-budget credit per submitted document, and count
  consecutive failed requests and short-text rejections without retaining the
  text or lexical items; the current monotonic bucket holds 200 credits, charges
  at least 20 credits per request (or one per document when higher), replenishes
  one credit per 30 seconds, and applies a 120-second cooldown after three
  consecutive failed requests, returning Retry-After-equivalent seconds;
- require authentication plus an ingress-level IP/account/global limiter and
  anomaly controls before an unrestricted public rollout: the application guard
  is intentionally session-scoped and cannot prevent evasion through new
  sessions or coordinate a budget across multiple workers; aggregate metrics
  are not a formal guarantee against every differential-inference attack;
- require an explicit eligible-resource allow-list, a separate operator rights
  acknowledgement, the exact fixed control profile
  `shared-abuse-controls-v1`, and a valid short external evidence-record ID
  before listing, materializing, or forwarding a public server-only resource;
  and
- keep the registry's repository, server-copy, public-SaaS, client-download,
  and derived-data decisions independent.

The runtime implements this as all four of
`LDFREQ_SERVER_ONLY_RESOURCE_IDS`,
`LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED=1`,
`LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION=shared-abuse-controls-v1`, and
`LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID=<opaque-record-id>`. The checked-in
deployment template leaves the allowlist and control fields empty and rights at
`0`. The evidence ID must be a bounded opaque identifier, not a URL, path,
secret, or placeholder. Its external record should bind the exact deployment
and resource versions to review evidence for a shared limiter, authenticated
account quota, content-free audit trail, anomaly detection, and
extraction-resistance testing.

The runtime verifies only that the declarations have the required literal
values and ID shape. It does not retrieve that record, validate legal rights,
or test any shared control. Therefore a syntactically accepted declaration is
not deployment approval or proof that the infrastructure exists. Missing or
invalid declarations leave the resources unlisted, unmaterialized, and absent
from isolated-worker forwarding. AntBNC remains `yellow` until written
permission covers the intended server-side use and is therefore not in the
public server-only eligible set. The official Paul Nation
10,000-headword and 25,000-family resources are `green` under CC BY-SA 4.0,
while client delivery remains disabled by product policy. The separate
EAPFoundation XLSX/PDF snapshot remains `yellow`.

An independently authorized desktop/private deployment is a distinct mode:
both `LDFREQ_SERVING_MODE=local` and
`LDFREQ_ALLOW_LOCAL_RESTRICTED=1` are required. Such a result records
`list_delivery=local-private`; it must never be described as bundled-public or
as satisfying the public server-only gate.

## Research-only fetched evaluation benchmarks

Fetched evaluation benchmarks are not runtime resources. Only their explicitly
allowed governance metadata may be versioned. Row-level payloads and generated
results remain under the registry's ignored local root by default. They must not
enter Git, package data, container layers, static assets, CI caches or artifacts,
deployment synchronization, application state, server or client downloads,
telemetry, logs, or external APIs.

ELLIPSE processing is limited to an access-controlled non-commercial local
research environment. The fetch step must pin the upstream commit and archive,
verify all recorded SHA-256 values before analysis, and extract encrypted
members locally. Decryption credentials must not appear in a manifest, command
log, test fixture, generated report, or application configuration. Aggregation
alone does not authorize publication. The completed ELLIPSE result bundle is
currently `review-required`, has `public_build: false`, and has no approved
result manifest, so its coefficients, performance tables, figures, QC output,
and measurement record remain local-only. The public inventory may contain only
the reviewed corpus manifest and frozen analysis plan until a result-specific
review passes. This workflow is outside the public application's startup gate,
but every source/archive candidate must verify that unapproved inputs and
results remain absent.

## Derived-result publication gate

`data/resource_registry.json` now declares a fail-closed contract for published
result bundles. A publishable bundle must be uniquely registered under the
controlled `results/public/` root with `public_build: true`, registry status
`approved`, and a byte- and SHA-256-pinned manifest. That manifest must inventory
the bundle and every artifact class, all upstream resource IDs, plan and
generator identities, runtime/environment identities, aggregation boundaries,
attribution, and a dated publication review.

The gate verifies that every upstream resource is `green`, its license evidence
is verified, its `aggregate_result_publication` decision permits the requested
artifact classes, and every citation, attribution, or disclosure-review
condition is recorded as satisfied. It also requires exact agreement between
the registered and selected files, rejects archives disguised with an allowed
extension, and rejects row-level data, individual predictions, and fitted
models. A result manifest cannot override an upstream registry decision.

The present registry intentionally has no public result bundle. ELLIPSE is
`review-required`; the legacy TAALES–COCA comparison is `blocked`. Running the
COCA comparison, obtaining positive or negative correlations, or publishing
only summaries does not alter that decision. Its plan, wrappers, test,
aggregates, figures, coverage, and fingerprints remain quarantined.

### Red, local only

- **TAALES 2.8.1 legacy application bundle.** The local `.app` is a migration
  reference containing software and many third-party datasets. No bundle-level
  license/NOTICE granting web redistribution has been located.
- **TAALES-bundled COCA-derived tables.** This includes
  `academic_bi_contingency.csv` and the associated frequency, range, lemma, and
  n-gram tables. The academic contingency table has 150,000 records whose n-gram,
  frequency, and order match the bundled COCA Academic bigram table exactly.
  Citation and local possession do not establish redistribution or SaaS rights.
- **Other TAALES-bundled third-party datasets.** ELP/HAL, BNC-derived data,
  TOEFL11/NNS, TASA/LSA, MRC, EAT/USF, SUBTLEX, concreteness, age-of-acquisition,
  and similar snapshots remain local only until reviewed as individual resources.

The entire `LexicalSophistication/` tree must remain excluded from public source,
build contexts, deployment uploads, and runtime synchronization. The separately
authored comparison wrappers and derived outputs are also blocked while the
COCA result bundle remains `blocked`; excluding the source tables alone is not a
publication clearance.

## Promotion procedure

Changing `yellow` or `red-local-only` to `green` requires one reviewable change
that updates both this policy when necessary and the registry entry. The change
must include:

1. the exact upstream version and acquisition date;
2. a stable primary-source URL or written permission record;
3. the applicable license/terms captured in a local NOTICE when redistribution
   requires it;
4. explicit decisions for repository distribution, server-side processing,
   client download, and derived bulk tables;
5. SHA-256 for every deployable artifact;
6. a reproducible build script or an explicit direct-snapshot explanation;
7. validation that no uncleared upstream data are embedded in an ostensibly
   cleared derivative; and
8. reviewer identity, decision date, and a future review trigger.

For a custom permission, the evidence must additionally identify the original
record, record editor, grantor and grantor authority, grant date, complete
machine-readable public-use scopes, revocation/expiry terms, and the exact
artifact binding. The independent reviewer must be different from both the
owner-attestor and the permission-record editor and must record a separate
decision reference. Until then, owner attestation remains evidence but cannot
set `release_eligible=true`.

If permission is narrow—for example, server-side lookup but no file download—the
entry stays conditional and the implementation must enforce that narrower mode.

## Build and release checks

Phase 0 establishes the policy and registry. The current release gate enforces
the following checks automatically:

1. parse `data/resource_registry.json` and reject unknown fields/status values;
2. verify every present `green` artifact before use, and every bundled `green`
   artifact before startup and release;
3. reject public-build inclusion of every `yellow` or `red-local-only` artifact;
4. reject public-build inclusion of any artifact whose entry or provisioning
   record sets the relevant public payload flag to `false`, including `green`
   evaluation benchmarks;
5. require local license/NOTICE files named by a bundled `green` entry;
6. require a complete independently reviewed permission-assurance record before
   any custom-permission resource claims a public payload or public use, and
   reject any NJ8 payload reintroduced under `data/NJ8/` beside its manifest or
   the exact attestation-bound artifact bytes under any tracked filename;
7. require every public result bundle to pass the derived-result contract above;
8. emit a deployment inventory containing resource ID, version, hash, status,
   tier, provisioning mode, and license—not the protected payload itself;
9. reject a checked-in Cloud Run template unless its public server-only
   allowlist/control fields are empty and its rights acknowledgement is `0`;
   and
10. require explicit review when a URL, version, file, transformation, intended
   use, license term, result artifact class, or publication scope changes.

Run `python3 scripts/check_public_release.py` against the exact Git inventory
before creating a public archive or deployment. The gate deliberately fails
while a blocked payload remains tracked or a required green artifact has not
yet been added to the release inventory. `.gitignore` alone cannot remove an
already tracked file.

Clean-candidate construction has a second independent gate. The builder requires
an approved selection manifest stored outside the repository and compares its
exact path/byte/SHA-256/role inventory with the discovered worktree. Its schema-v2
evidence records the selection and registry identities, required externally
attested zero-finding
scans, reviewed result bundles, and excluded output families. This Q0 machinery
is implemented. An actual candidate is not approved until a reviewer creates
that external manifest for the current bytes and accepts the resulting evidence;
ELLIPSE result publication remains a separate unresolved review.

This registry is an engineering control and evidence log, not a substitute for
legal advice or rights-holder permission.
