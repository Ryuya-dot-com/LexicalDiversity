# Privacy and Data Handling Specification

> Status: Draft for implementation and institutional review
>
> Version: 0.4
>
> Last updated: 2026-07-22
>
> Target service: Public web edition of the Lexical Diversity & Frequency-Profile Analyzer

## 1. Purpose and status of this document

This document defines the minimum privacy, data-handling, hosting, deletion,
logging, and verification requirements for a public web service that analyzes
learner writing. Its principal design rule is:

> The service processes source text in memory, returns the requested analysis,
> and does not write source text or content-derived diagnostics to durable
> storage.

This is an engineering and governance specification, not legal advice. Before
processing real learner writing, the operating institution must have its legal,
privacy/data-protection, information-security, and, where applicable, research
ethics functions confirm the intended use, notices, lawful basis, contracts,
and safeguards. The applicable rules can differ according to whether the
operator is, for example, a private university, national/public university, or
another body, and according to the location and age of users.

The keywords **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative.

## 2. Scope and service boundary

This specification applies to:

- text pasted into the application;
- uploaded text files and archive members;
- uploaded filenames and document labels;
- tokens, word forms, lemmas/flemmas, word-family matches, off-list items, and
  observed surface forms derived from the text;
- lexical-diversity, frequency-profile, coverage, and related results;
- exports generated for the user;
- application, access, security, error, metric, trace, and provider logs;
- caches, temporary files, crash artifacts, snapshots, and backups; and
- operational access by developers, administrators, and cloud providers.

The service is an analysis tool, not a learner corpus, assignment-submission
system, plagiarism service, or research-participant repository. Retaining texts
for longitudinal analysis, model training, service improvement, or research is
outside this specification and requires a separately designed and approved
system.

## 3. Data classification and retention schedule

| Data class | Examples | Permitted location | Maximum retention | Durable backup |
|---|---|---|---|---|
| Source content | Pasted or uploaded essay text, archive member contents | Originating browser, originating Streamlit session/request memory until clearing, an in-memory pipe, and one-shot isolated-worker memory | Only while the request is validated and analyzed; release references immediately after success, rejection, cancellation, or error | Prohibited |
| Source identifiers | Original filename, archive path, title supplied with a document | Originating browser and originating session memory only | No longer than the result session; remove earlier when not needed | Prohibited |
| Content-derived detail | Tokens, word forms, lemmas/flemmas, word-family mappings, observed forms, off-list items, snippets | Originating process/session memory and the user's transient result or download | No longer than 30 minutes from result creation | Prohibited |
| Aggregate results | Index values, counts, percentages, coverage bands, warnings, parameters and list versions | Originating process/session memory and the user's transient result or download | No longer than 30 minutes from result creation | Prohibited |
| Session abuse-control state | Query-budget credits, monotonic timestamps, success/failure counters, short-rejection count | Originating application session only | Until **Delete data** or session destruction | Prohibited |
| Runtime request/error metadata | Client IP, request URL, user agent, status, latency, application output, and runtime errors | Transient platform processing only; no log bucket or sink destination | Not stored or queryable | Prohibited |
| Provider control-plane audit data | Administrator identity, configuration change, deployment event | Google Cloud Logging `_Required` bucket in the location selected before project creation | 400 days (fixed by Google Cloud) | Provider controlled; MUST NOT contain submitted or derived content |
| Static service assets | Source code, configuration templates, lexical lists, manifests | Approved repositories and a dedicated Tokyo-region Cloud Storage bucket mounted read-only at runtime | According to release and records policy | Permitted; learner data is prohibited |

The 30-minute result TTL is absolute and begins when an analysis result is
created. It is not extended by page views, downloads, or passive activity. A new
analysis MAY create a new 30-minute result, but MUST first release the previous
result and associated details from the session.

“No durable source-text storage” does not mean that bytes can be proven to have
been cryptographically erased from RAM at an exact instant. It means that the
application does not write them to a database, object store, filesystem,
durable queue, cache, log, trace, metric, snapshot, or backup, and releases its
references as soon as processing ends.

## 4. Approved production architecture

### 4.1 Hosting region

The production application MUST be self-hosted from a customer-selected Tokyo
region. The initial reference deployment is Google Cloud Run in
`asia-northeast1` (Tokyo). The institution MUST set its organization/folder
[Cloud Logging default resource settings](https://cloud.google.com/logging/docs/default-settings)
to Tokyo and disable the automatic `_Default` sink **before** creating a new,
dedicated project; changing that setting is not retroactive. The Cloud Run
service and dedicated static-lexical-resource Cloud Storage bucket MUST then be
created in Tokyo. No custom application, request, security, or error-log bucket
is created. Google documents Cloud Run regionality in its
[location guide](https://cloud.google.com/run/docs/locations) and Tokyo Cloud
Storage availability in its
[bucket-location guide](https://docs.cloud.google.com/storage/docs/bucket-locations).
The repository's [Tokyo pilot template](cloud-run-tokyo-pilot.md) translates
these requirements into a review checklist; it is not deployment approval.

Selecting a workload region does not by itself prove that account metadata,
support access, control-plane records, or every subprocessor activity remains in
that region. Before production, the operator MUST document:

- the contracted controller/processor roles and data-processing terms;
- the selected workload and required control-plane audit-log region;
- provider support-access locations and controls;
- all relevant subprocessors and the change-notification process;
- any international-transfer mechanism that is required; and
- retention behavior for required provider control-plane audit logs and backups.

The runtime MUST use a dedicated user-managed service account. Its only object
permission is a bucket-scoped read-only grant (`roles/storage.objectViewer`) on
the one Tokyo bucket that contains static lexical resources. The bucket MUST
contain no learner data, and the mount MUST set `read_only=true`; Google
documents that configuration in the
[Cloud Storage volume-mount guide](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts).
The role MUST NOT be granted at project scope. The runtime identity MUST have no
database, object-storage write, durable-queue, or backup-write permission. Build
and resource-publishing identities MUST be separate. If a future feature
requires a runtime write permission, it is a material architecture change and
requires a new privacy review.

### 4.2 Data flow

The approved data flow is:

1. The user's browser sends the input over TLS to the Tokyo endpoint.
2. One isolated application session validates and analyzes the input in memory.
3. Static dictionaries and models are read from immutable image assets or the
   dedicated read-only Tokyo Cloud Storage mount.
4. Results are returned only to the originating browser/session.
5. Source text references are released immediately after analysis.
6. Result objects expire automatically no later than 30 minutes after creation,
   or sooner when the user selects **Delete data**.

No third-party NLP, generative-AI, translation, analytics, session-replay, or
error-reporting service may receive source content or content-derived detail.
The reference egress design uses
[Direct VPC egress](https://cloud.google.com/run/docs/configuring/vpc-direct-vpc)
with all traffic routed through the VPC, no Cloud NAT, and deny-by-default
firewall policy. Its sole runtime exception is controlled access through Private
Google Access and `restricted.googleapis.com` to read static objects from the
approved lexical-resource bucket. This exception is required by the read-only
Cloud Storage mount; the design MUST NOT be described as absolute zero egress.
The dedicated project and lexical bucket MUST also be covered by a reviewed VPC
Service Controls perimeter so the restricted Google API path cannot be used to
reach resources outside the approved perimeter. No general Internet egress or
unapproved Google API is permitted. See Google's
[Cloud Run VPC Service Controls guide](https://cloud.google.com/run/docs/securing/using-vpc-service-controls).

### 4.3 Transport and session isolation

The reference ingress path is a **regional external Application Load Balancer
-> Cloud Armor -> IAP -> Cloud Run in Tokyo**. IAP authentication is mandatory
for real learner writing; an anonymously accessible real-writing endpoint is
not permitted. Cloud Run MUST use `internal-and-cloud-load-balancing` ingress,
disable its default `run.app` URL, grant no `allUsers` invoker role, and grant
invocation only through the approved IAP path. These are complementary controls:
Google notes that enabling IAP only on a load balancer does not by itself secure
the service's direct URL. See the official
[Cloud Run ingress](https://cloud.google.com/run/docs/securing/ingress),
[regional external load-balancer](https://cloud.google.com/load-balancing/docs/https/setting-up-reg-ext-https-serverless),
and [IAP for Cloud Run](https://cloud.google.com/iap/docs/enabling-cloud-run)
guides.

Google currently documents disabling the default Cloud Run URL as Preview. The
institution MUST review the Pre-GA terms/support posture. If that feature is not
approved, an equivalent ingress/IAM design and external bypass test are required;
the control MUST NOT simply be omitted.

- All public connections MUST use HTTPS with TLS 1.2 or later.
- HTTP MUST redirect to HTTPS without reflecting input data in the redirect.
- Source text MUST NOT appear in a URL, query string, route, cookie, header,
  referer, or browser-history entry.
- Session identifiers MUST be random and MUST NOT be derived from text,
  filename, user identity, email address, or IP address.
- Session cookies MUST be `Secure`, `HttpOnly` where compatible, and use an
  appropriate `SameSite` policy.
- One user MUST NOT be able to retrieve another user's input or results by
  guessing or replaying an identifier.
- Dynamic input and result responses MUST use `Cache-Control: no-store` where
  the serving framework permits it. The load balancer MUST also enforce this
  response header as defense in depth. Cloud CDN MUST be disabled; Google
  documents `no-store` behavior in its
  [cacheability guide](https://cloud.google.com/cdn/docs/caching).

Cloud Armor can inspect and rate-limit the initial HTTP request, including a
WebSocket upgrade, but it does not inspect subsequent WebSocket frames. IAP
also authenticates the initial upgrade rather than continuously reauthorizing
each frame. Long-lived sessions therefore require application-side ownership,
expiry, cancellation, and reconnect checks; see Google's
[IAP session guidance](https://cloud.google.com/iap/docs/sessions-howto) and
[Cloud Run WebSocket guidance](https://cloud.google.com/run/docs/triggering/websockets).

## 5. Collection and use limitations

### 5.1 Permitted purpose

The operator may process a submission only to:

- calculate the analysis explicitly requested by the user;
- generate the result and user-initiated download;
- protect the service against failures and abuse using transient, content-free
  session or platform state that is not written to logs;
  and
- maintain reproducibility metadata such as application, list, tokenizer, and
  parameter versions.

### 5.2 Prohibited secondary use

The operator MUST NOT use submitted or derived content for:

- research, learner profiling, grading, or disciplinary decisions unless these
  are separately approved and disclosed;
- product improvement, quality review, benchmarking, or debugging corpora;
- model training or evaluation;
- advertising or behavioral analytics; or
- any human review, except when the user has separately and expressly submitted
  material through an approved support or research channel.

Consent to run the analysis is not consent to create a research corpus. A future
research repository requires a separate purpose, lawful basis, information
notice/consent process where applicable, ethics review, access controls,
retention schedule, withdrawal/deletion process, and data-management plan. It
MUST be opt-in and MUST NOT be enabled by default.

## 6. Input handling

- The interface MUST ask users to remove names, student numbers, contact
  details, and unnecessary personal references before submission.
- The interface MUST warn that free writing can reveal health, disability,
  racial or ethnic origin, religion, political views, sexual life or
  orientation, family circumstances, and other sensitive information.
- The service MUST collect no more text or metadata than needed for the selected
  analysis.
- Per-file, total uncompressed-size, document-count, and processing-time limits
  MUST be enforced before expensive processing.
- Archive extraction, if enabled, MUST reject traversal paths, links, nested
  archives, encrypted members, unsupported file types, excessive compression
  ratios, and entries exceeding configured limits. Extraction MUST occur only in
  memory or an approved per-request memory-backed temporary area.
- An original filename MAY be shown transiently to its submitting user, but it
  MUST NOT be placed in application/access logs, cache keys or values, metrics,
  traces, persistent exports retained by the server, or backups. Internal
  processing SHOULD use labels such as `Document 1` and a random request-local
  identifier.
- Rejected or malformed input receives the same no-persistence treatment as a
  successful input.

### 6.1 Server-only lexical-resource abuse controls

Server-only list lookup MUST apply a content-free query budget before loading or
querying the resource. The application-level guard counts submitted documents,
successful/failed requests, consecutive failures, and short-text rejections. It
MUST NOT accept or retain source text, filenames, lexical items, per-document
token counts, or lookup outcomes. Denials MUST provide a Retry-After-equivalent
duration, and **Delete data** MUST also remove the guard state. The current
session bucket has 200 credits, charges at least 20 credits per request (or the
document count when higher), and restores one credit per 30 seconds. Thus a
session permits at most ten immediately repeated single-document requests, then
requires about ten minutes to restore the minimum charge.

This session guard is defense in depth, not a complete rate limiter. A new
browser session receives a new application bucket, and separate workers do not
share this state. Production therefore MUST add an authenticated account and/or
IP budget, a deployment-wide limiter at the ingress or shared infrastructure
layer, and anomaly monitoring. Those controls MUST use content-free metadata
and MUST NOT send learner writing to a third-party abuse-detection service.

The current session guard can be bypassed by starting a new session, and Cloud
Armor rate limiting is an ingress abuse control rather than a strict per-account
quota. A durable, authenticated account-wide quota and production anomaly rule
remain future implementation tasks. Until they are complete, the Tokyo pilot
MUST be restricted to a small IAP-authorized cohort and MUST NOT be presented as
an unrestricted production service.

## 7. Caching and temporary data

### 7.1 Application cache

`st.cache_data` and any equivalent shared or persistent cache MUST NOT be used
for a function whose arguments, keys, values, exceptions, or return value can
contain:

- source text or filename;
- tokens or lexical units;
- observed forms or snippets;
- off-list items; or
- per-document or per-user analysis results.

`st.cache_resource` MAY be used only for immutable, content-independent assets,
such as lexical lists, tokenizers, lemmatizers, and fitted constants loaded with
the application. Cached resources MUST NOT be mutated with request data.

### 7.2 Temporary files and memory

Processing SHOULD use memory directly. If a library unavoidably requires a
path, the application MUST use a unique per-request directory on a memory-backed
filesystem with owner-only permissions, remove it in a `finally` path after
success or failure, and prevent reuse by another session.

Core dumps MUST be disabled. Swap MUST be disabled or encrypted under an
institutionally approved configuration. Cloud Run's container contract makes
the container filesystem writable and memory-backed; the platform does not
offer a read-only-root switch. The compensating controls are to run as a
non-root UID/GID, keep application and lexical-resource paths root-owned and
non-writable to that UID, mount a separately size-limited in-memory volume at
the approved application temporary path, and direct `TMPDIR` and XDG cache/
configuration paths into it. The image SHOULD remove ordinary write permission
from standard temporary directories. Because the platform can still expose
other writable memory-backed paths, acceptance testing MUST verify that request-
derived bytes are written only under the approved mount and scan every writable
path for canaries; it MUST NOT claim an OS-level read-only root. See the official
[container runtime contract](https://cloud.google.com/run/docs/container-contract)
and [in-memory volume guide](https://cloud.google.com/run/docs/configuring/services/in-memory-volume-mounts).

## 8. Logging, metrics, tracing, and debugging

The production service retains no application, access, request, security, or
error logs. It does not install a Cloud Logging client, error-reporting agent,
session-replay service, or request tracer. The Streamlit parent process and the
one-shot analysis worker send ordinary stdout and stderr to `/dev/null` so that
framework or dependency messages do not become Cloud Run container logs.

The following MUST NOT enter logs, traces, monitoring tags, alert payloads, or
exception reports:

- full or partial source text;
- filename, archive path, document title, or export name supplied by the user;
- tokens, lemmas/flemmas, word families, observed forms, snippets, or off-list
  items;
- cache keys derived from any of the above;
- request or response bodies;
- form values or uploaded-file objects;
- local variables, memory dumps, or rendered session state; and
- identifiers derived by hashing source content or filenames.

Plain `print` debugging of request objects is prohibited. Exception handlers
MUST return only a stable, content-free message to the submitting browser and
MUST NOT emit a traceback or exception object to stdout, stderr, an error
reporter, or a trace backend. Local-variable capture, request-body capture,
session replay, and automatic attachment of user input to errors are disabled.
Platform metrics MAY be used only when they are aggregate counters or resource
utilization measurements that contain no client IP, URL, user agent, filename,
text, exact request event, or application error detail.

Cloud Run creates request log entries automatically and does not provide a
service-level switch that prevents their generation. This architecture therefore
makes a narrower, verifiable claim: those runtime entries are **not stored or
queryable**. The organization/folder Logging defaults MUST select Tokyo and
disable the automatic `_Default` sink before the dedicated project is created.
The project MUST have no user-defined or inherited aggregated sink that routes
Cloud Run request/container/system logs, load-balancer logs, Cloud Armor logs,
IAP Data Access logs, or application errors to any destination. The load-balancer
backend MUST have request logging disabled, which also disables Cloud Armor
per-request logging; Cloud Armor verbose logging MUST remain off. IAP Data Access
logging MUST not be enabled for end-user requests. No custom runtime log bucket
or application/security sink is created. See the official
[Cloud Run logging guide](https://cloud.google.com/run/docs/logging),
[Logging default-settings guide](https://cloud.google.com/logging/docs/default-settings),
[load-balancer logging guide](https://cloud.google.com/load-balancing/docs/https/https-logging-monitoring),
and [Cloud Armor request-logging guide](https://cloud.google.com/armor/docs/request-logging).

The only stored log class is Google Cloud's `_Required` control-plane bucket.
It retains Admin Activity, System Event, and related required audit logs for 400
days; its retention and contents cannot be changed or excluded. Access MUST be
restricted, and release verification MUST confirm that it contains only
administrative/configuration events, not end-user HTTP requests, application
errors, source text, filenames, tokens, derived lexical detail, or request/
response bodies. See
[Cloud Logging retention](https://cloud.google.com/logging/docs/store-log-entries).

Application-enforced processing deadlines and cancellation are required in
addition to the Cloud Run request timeout. A platform timeout can close the
client connection while container code continues running, so the request-timeout
setting alone does not establish deletion or cancellation. The application now
uses a one-shot subprocess and a 120-second default monotonic deadline covering
worker startup, rights-gated resource loading, analysis, and aggregate-result
transfer. Deadline and callback-abort tests verify process termination and
reaping. Real-browser disconnect and in-flight user-deletion propagation are
not yet guaranteed by Streamlit and remain release blockers; see Google's
[request-timeout documentation](https://cloud.google.com/run/docs/configuring/request-timeout).

## 9. Results, downloads, expiry, and user deletion

- Results MUST remain isolated to the originating session.
- Aggregate results and any token/off-list detail selected for display or export
  MUST exist only in transient session memory and the user's browser/download.
- Generated downloads MUST be streamed or generated in memory. The server MUST
  NOT retain an export file after delivering it.
- Download names SHOULD be generic and MUST NOT include the original filename
  unless the name is generated entirely in the user's browser and is never sent
  back to the server.
- Every results view MUST provide a clearly visible **Delete data**
  control.
- Selecting the control MUST invalidate the result immediately, release source
  identifiers, derived objects, and session abuse-control counters from session
  state, clear the displayed result, and prevent reuse of any previous download
  URL or result identifier.
- Closing or losing the browser connection SHOULD trigger prompt cleanup, but
  automatic 30-minute expiry is the authoritative fallback and MUST not depend
  solely on a client-side event.
- At TTL expiry, subsequent access MUST return an expired/not-found response and
  MUST NOT recreate the result from a cache, queue, log, or backup.

The user-facing notice must explain that files downloaded to the user's own
device are outside the server's deletion control.

## 10. Backups, snapshots, and restoration

Backups and snapshots MAY include application code, deployment configuration,
static lexical resources, and content-free operational records within their
approved retention. They MUST NOT include:

- source text or uploaded files;
- filenames or archive paths;
- token, observed-form, or off-list tables;
- analysis results or exports;
- session-state stores;
- per-request temporary directories; or
- application caches containing request data.

Restoring a service from backup MUST NOT restore an expired or user-deleted
submission or result. If any future durable store is introduced, its backup
deletion behavior and maximum restoration window must be specified before use;
until then, such a store is prohibited.

## 11. Minors, sensitive information, and research ethics

Learner writing may identify an individual directly, through a filename, or in
combination with context. It may also contain sensitive or special-category
information even when the assignment did not request it. Therefore:

- The public service MUST NOT claim that submissions are anonymous merely
  because no account name is collected.
- The service MUST minimize incidental IP/device data and MUST NOT use age,
  identity, or behavioral tracking for analytics.
- The operator MUST determine whether and how minors may use the service before
  launch, considering the applicable jurisdiction, school authority, guardian
  involvement, and power imbalance in educational settings.
- Real graded assignments, compulsory use, or use involving minors SHOULD be
  offered through an institutionally controlled and authenticated service, not
  an anonymous third-party-hosted demonstration.
- A warning not to submit sensitive information is an additional safeguard; it
  is not a substitute for lawful processing, security, or appropriate ethics
  review.
- Any study that recruits participants, retains text, links analysis to learner
  records, compares groups, publishes quotations, or reuses submissions MUST be
  routed through the institution's applicable research-ethics process before
  collection.

## 12. Streamlit Community Cloud restriction

Streamlit Community Cloud MUST NOT process real learner writing. It may be used
only for a clearly labelled technical demonstration based on curated synthetic
text or text that the operator has verified may be publicly redistributed and
processed for that purpose.

The application MUST show its synthetic/already-public-text-only prototype
banner unless the reviewed deployment sets `LDFREQ_REAL_WRITING_APPROVED=1`
after every applicable acceptance gate is complete. This flag only controls the
notice; it does not itself establish legal authority, regional controls, or
technical approval.

The Community Cloud demonstration SHOULD disable arbitrary paste, upload, and
URL-fetch inputs and expose only bundled demonstration examples. Its page MUST
state: “Demonstration only — do not submit learner writing or personal
information.” A checkbox or user assertion alone is not an adequate control for
an otherwise unrestricted input box.

This restriction is based on, among other issues, Community Cloud's fixed United
States hosting, platform terms concerning age, personal and sensitive
information, broad User Content terms, and the absence in the reviewed public
documentation of a fixed retention/deletion commitment for uploaded essay data
across platform logs and backups. The self-hosted Apache-2.0 Streamlit software
is distinct from the Community Cloud service terms.

## 13. Transparency and user notice

Before the input control, the service MUST provide a concise Japanese notice and
an accessible full privacy notice. An English version SHOULD be provided when
the service is offered internationally. The notice must identify or explain:

- the operator/controller and privacy contact;
- the analysis purpose and any applicable lawful basis;
- categories of input and operational data;
- that application, request, access, security, and error logs, including client
  IP addresses, are not stored or queryable;
- that Cloud Run automatically generates request-log entries but the disabled
  `_Default` sink and absence of any alternate sink prevent their storage;
- that submitted text is processed in the Tokyo region and is not durably
  stored by the application;
- the 30-minute result TTL;
- the separate status and fixed 400-day retention of required provider
  control-plane audit records;
- recipients/processors, subprocessors, support-access locations, and any
  relevant international transfers;
- that text, filenames, tokens, and off-list items are excluded from logs,
  caches, and backups;
- the deletion control and its limits, including user-downloaded files;
- the prohibition on undisclosed research/model-training reuse;
- foreseeable risks of including identifiers and sensitive information;
- applicable access, correction, deletion, objection, complaint, and withdrawal
  routes; and
- security-incident contact information.

The interface MUST NOT state that the service is “fully anonymous,” “zero risk,”
or that RAM is instantaneously and irreversibly erased.

## 14. Operations and incident response

- Production access MUST use least privilege, individual administrator accounts,
  multi-factor authentication, and audited changes.
- Routine administrators and developers MUST not have a facility to view live
  submission bodies.
- Operational dashboards MUST use only aggregate platform metrics that cannot
  identify a client or reconstruct an individual request.
- Releases that change input handling, sessions, caching, logging, exports,
  observability, hosting region, processors, or subprocessors require privacy
  regression testing and review.
- The operator MUST maintain an incident runbook covering containment,
  assessment, processor escalation, evidence preservation, notification
  decision-making, and remediation.
- Incident evidence collection MUST use required control-plane audit events and
  aggregate metrics without enabling runtime request, IP, error, or learner-text
  logging.
- Suspected exposure of source text, filenames, token/off-list detail, or results
  in a log, cache, backup, or another session MUST be treated as a data-handling
  incident even if no external attacker is confirmed.
- Applicable notification duties and deadlines MUST be assessed by the operator's
  privacy/legal function; engineers must not assume that zero durable storage
  eliminates breach obligations.

## 15. Production acceptance criteria

All criteria below are release-blocking unless explicitly marked as recurring.
Evidence must be retained without retaining the test text itself beyond the test
window; the test may retain a one-way inventory record stating that a scan
completed with zero matches.

### 15.1 Region and vendor governance

- [ ] Organization/folder Logging defaults select Tokyo and disable the default
  sink before the dedicated project is created; evidence shows the setting was
  inherited at project creation rather than changed afterward.
- [ ] Deployment configuration shows every workload service in Tokyo
  (`asia-northeast1` for the reference Cloud Run deployment).
- [ ] The required `_Required` bucket is in Tokyo; `_Default` is disabled, and
  the sink inventory proves that no runtime log has a storage destination.
- [ ] A data-flow diagram and register list every processor, relevant
  subprocessor, support location, transfer mechanism, and retention period.
- [ ] The applicable cloud DPA/contract and subprocessor-change notification have
  been reviewed and recorded by the institution.
- [ ] The runtime identity is demonstrably unable to write to databases, object
  storage, durable queues, or backup services.
- [ ] The runtime's only object permission is bucket-scoped
  `roles/storage.objectViewer` on the Tokyo static-lexical-resource bucket; the
  mount is read-only and the bucket contains no learner data.

### 15.2 Content non-persistence test

For each release, an automated test MUST submit unique high-entropy canaries in:

1. the essay body;
2. the original filename and archive-member path;
3. an ordinary token; and
4. an off-list token.

The test MUST exercise successful analysis, validation rejection, malformed
archive, timeout/cancellation, deliberate internal exception, user deletion,
session disconnect, and TTL expiry. After each path it must inspect the Logs
Router, `_Default`, `_Required`, every inherited or user-defined sink, trace and
metric backend, cache, temporary volume, database, object store, durable queue,
export area, snapshot, and backup inventory.

- [ ] Exact and normalized canary searches return zero matches outside the
  originating active session and the test client's received result.
- [ ] Captured stdout and stderr contain no canary, source excerpt, filename,
  token, or off-list item.
- [ ] No exception-reporting record exists; the submitting browser receives only
  a stable, content-free error message.
- [ ] Restarting/replacing all application instances does not make any prior
  input or result retrievable.
- [ ] Static code inspection or instrumentation confirms that no request-derived
  value reaches `st.cache_data`, another shared cache, or a cache key.

### 15.3 Session isolation, expiry, and deletion

- [ ] Two concurrent sessions analyzing different canaries cannot observe,
  enumerate, download, or infer one another's results.
- [ ] A result is accessible at 29 minutes 59 seconds only to its originating
  session and is inaccessible at 30 minutes 00 seconds.
- [ ] Passive page viewing and repeated downloads do not extend expiry.
- [ ] Expired session objects are removed from server memory by the next cleanup
  cycle, which runs at least once per minute.
- [ ] Selecting **Delete data** makes the result and any download route
  inaccessible within 5 seconds and removes the session's filename, tokens,
  off-list detail, and aggregate result objects.
- [ ] Back-button, refresh, stale WebSocket, and replayed download-link tests do
  not restore deleted or expired results.

### 15.4 Runtime-log non-retention and backups

- [ ] The project inherited a disabled `_Default` sink before creation, and no
  project, folder, or organization sink routes runtime logs elsewhere.
- [ ] Load-balancer backend request logging is disabled; Cloud Armor per-request
  and verbose logging are off; IAP Data Access logging is not enabled for
  end-user requests.
- [ ] Automatically generated Cloud Run request/container/system entries and all
  application errors are not stored or queryable in any log destination.
- [ ] The deployed Streamlit parent and analysis worker discard ordinary stdout
  and stderr, and no error-reporting, tracing, analytics, or replay agent is
  installed.
- [ ] The Google Cloud `_Required` bucket is separately recorded as fixed at 400
  days, and a sample review confirms that it contains control-plane events but
  no source text, filename, token, derived lexical detail, or request/response
  body.
- [ ] A backup manifest and restore test demonstrate that only code,
  configuration, static lexical assets, and approved content-free records are
  restored.
- [ ] Restoring the most recent backup cannot restore a submission, filename,
  token/off-list detail, session, analysis result, or export.

### 15.5 Input and security controls

- [ ] Upload count, individual size, total uncompressed size, compression ratio,
  and analysis-time limits fail closed and are tested at each boundary.
- [ ] Archive tests cover traversal paths, absolute paths, links, nested and
  encrypted archives, duplicate/confusable paths, and decompression bombs.
- [ ] TLS, secure cookie/session settings, CSRF defenses where applicable,
  content-security headers, and `no-store` behavior are verified from outside
  the application.
- [ ] The only real-writing path is the regional external load balancer through
  Cloud Armor and IAP; anonymous invocation, the default `run.app` URL, and
  direct Internet ingress are disabled.
- [ ] Load-balancer request logging and Cloud Armor per-request/verbose logging
  are off, and a WebSocket test confirms that session expiry and authorization
  do not rely on inspection of post-upgrade frames.
- [ ] Runtime egress testing shows that source-processing paths cannot call an
  unapproved third-party API.
- [ ] Direct VPC egress sends all traffic to a no-NAT VPC; the sole allow-listed
  API path is Private Google Access/`restricted.googleapis.com` read access to
  the approved static lexical bucket.
- [ ] A VPC Service Controls perimeter covers the Cloud Run project and static
  lexical bucket, and staging proves the restricted API path cannot reach an
  out-of-perimeter bucket or service.
- [ ] Rate limiting and abuse controls operate without forwarding essay content
  to an abuse-detection provider.
- [x] A one-shot worker enforces the bounded application deadline, disables
  core dumps, rejects source-size/protocol violations, and is reaped after
  success, timeout, and callback abort in repository POSIX tests.
- [ ] Real-browser disconnect and in-flight deletion cancellation stop work and
  release request-derived objects even when the Cloud Run connection times out;
  repeat this test in the production container and inspect descendant
  processes/file descriptors.
- [ ] Before expansion beyond the limited IAP pilot, a deployment-wide
  authenticated account quota is implemented and tested against new-session and
  multi-worker bypasses.

### 15.6 Notice, minors, and ethics

- [ ] The Japanese short and full notices are visible before submission and
  accurately state region, purpose, retention, deletion, processors, and no
  secondary research/model-training use.
- [ ] The UI asks users to remove identifiers and warns about sensitive
  information without claiming this makes the text anonymous.
- [ ] The operator has documented the permitted user-age population and the
  pathway for minors or institutionally required use.
- [ ] A privacy impact/DPIA screening and research-ethics screening have been
  completed and signed off by the appropriate institutional functions.
- [ ] A Community Cloud deployment, if maintained, has arbitrary user input
  disabled and contains only approved synthetic or redistributable public
  examples plus the required demonstration warning.

### 15.7 Operational and incident exercises

- [ ] Before first production use, a tabletop exercise covers accidental body
  logging, cross-session result exposure, compromised administrator access, and
  cloud-region/configuration drift.
- [ ] In a staging-only drill, a synthetic leak canary triggers the configured
  security alert within 5 minutes; the alert contains the request ID and event
  type but not the canary or surrounding text.
- [ ] An on-call operator can stop new submissions within 15 minutes of the
  drill declaration while preserving content-free audit evidence and leaving
  the public privacy/incident contact page available.
- [ ] The drill assigns an incident owner, restricts or revokes the affected
  access, records the affected data classes and time window, and reaches the
  institution's privacy/security contacts within the internal escalation times
  approved before launch.
- [ ] The exercise verifies contacts, containment steps, provider escalation,
  decision ownership, and notification assessment.
- [ ] A deletion drill confirms user deletion, TTL cleanup, log expiry, and
  backup non-restoration end to end.
- [ ] Content-leak regression tests run in CI for every release that changes a
  data-handling path.
- [ ] Region, IAM, log-retention, backup, and egress drift checks run at least
  daily; failures page the service owner without attaching request content.
- [ ] The incident tabletop, vendor/subprocessor review, and restore/deletion
  drill recur at least annually and after a material architecture change.

## 16. Primary references

### Streamlit and Snowflake

- Streamlit Community Cloud, status and fixed United States hosting:
  <https://docs.streamlit.io/deploy/streamlit-community-cloud/status>
- Streamlit Community Cloud, trust and security:
  <https://docs.streamlit.io/deploy/streamlit-community-cloud/get-started/trust-and-security>
- Streamlit file-uploader memory behavior:
  <https://docs.streamlit.io/knowledge-base/using-streamlit/where-file-uploader-store-when-deleted>
- Streamlit caching behavior and cross-user cache availability:
  <https://docs.streamlit.io/develop/concepts/architecture/caching>
- Streamlit Terms of Use, including the Community Cloud addendum:
  <https://streamlit.io/terms-of-use>
- Snowflake Privacy Notice:
  <https://www.snowflake.com/en/legal/privacy/privacy-policy/>
- Snowflake subprocessor list:
  <https://www.snowflake.com/en/legal/privacy/snowflake-sub-processors/>

### Reference production cloud

- Google Cloud Run locations and customer-data regionality:
  <https://cloud.google.com/run/docs/locations>
- Google Cloud Run ingress restrictions:
  <https://cloud.google.com/run/docs/securing/ingress>
- Google Cloud regional external Application Load Balancer with Cloud Run:
  <https://cloud.google.com/load-balancing/docs/https/setting-up-reg-ext-https-serverless>
- Identity-Aware Proxy for Cloud Run:
  <https://cloud.google.com/iap/docs/enabling-cloud-run>
- Google Cloud Run WebSocket support:
  <https://cloud.google.com/run/docs/triggering/websockets>
- Google Cloud Run container runtime contract and request timeouts:
  <https://cloud.google.com/run/docs/container-contract>
  and <https://cloud.google.com/run/docs/configuring/request-timeout>
- Google Cloud Run read-only Cloud Storage volume mounts:
  <https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts>
- Google Cloud Storage bucket locations:
  <https://docs.cloud.google.com/storage/docs/bucket-locations>
- Google Cloud Logging default resource settings:
  <https://cloud.google.com/logging/docs/default-settings>
- Google Cloud Run automatic request logging:
  <https://cloud.google.com/run/docs/logging>
- Google Cloud Logging routing and required-log retention:
  <https://cloud.google.com/logging/docs/routing/overview>
  and <https://cloud.google.com/logging/docs/store-log-entries>
- Google Cloud load-balancer and Cloud Armor request logging:
  <https://cloud.google.com/load-balancing/docs/https/https-logging-monitoring>
  and <https://cloud.google.com/armor/docs/request-logging>
- Google Cloud Data Processing Addendum:
  <https://cloud.google.com/terms/data-processing-addendum/>
- Google Cloud subprocessors:
  <https://cloud.google.com/terms/subprocessors>

### Japan and European Union

- Personal Information Protection Commission, General Guidelines under the
  Act on the Protection of Personal Information:
  <https://www.ppc.go.jp/personalinfo/legal/guidelines_tsusoku/>
- Personal Information Protection Commission, APPI Guidelines Q&A:
  <https://www.ppc.go.jp/personalinfo/faq/APPI_QA/>
- Personal Information Protection Commission, provision to a third party in a
  foreign country:
  <https://www.ppc.go.jp/personalinfo/legal/guidelines_offshore/>
- Personal Information Protection Commission, guidelines for administrative
  organs and similar bodies:
  <https://www.ppc.go.jp/personalinfo/legal/guidelines_administrative/>
- EU General Data Protection Regulation, including Articles 5, 8, 9, 13, 28,
  32, and 35:
  <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX%3A32016R0679>
- European Data Protection Board, controller or processor guidance:
  <https://www.edpb.europa.eu/sme/learn-the-basics/data-controller-or-data-processor_en>
- European Commission, Standard Contractual Clauses for international
  transfers:
  <https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/standard-contractual-clauses-scc_en>
