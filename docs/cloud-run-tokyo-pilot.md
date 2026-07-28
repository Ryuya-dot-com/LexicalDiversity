# Cloud Run Tokyo Pilot Template

> Status: Architecture and verification template; **not deployment approval**
>
> Version: 0.2
>
> Last updated: 2026-07-22
>
> Scope: A limited, IAP-authenticated pilot for the Lexical Diversity &
> Frequency-Profile Analyzer in Google Cloud Tokyo

## 1. Decision boundary

This document converts the repository's
[privacy and data-handling specification](privacy-and-data-handling.md) into a
candidate Google Cloud design. It does not authorize a deployment, authorize
the use of real learner writing, or replace institutional privacy, legal,
information-security, procurement, records, accessibility, or research-ethics
approval.

The first deployed environment, if approved, MUST use only synthetic canary text
until every release-blocking item in this document and the privacy specification
passes. A later real-writing pilot MUST be limited to named IAP-authorized users;
there is no anonymous public endpoint for real learner writing.

## 2. Reference topology

```text
named pilot user
    |
    | HTTPS; no source text in URL/header/cookie
    v
regional external Application Load Balancer (Tokyo backend)
    |
    v
Cloud Armor (per-request and verbose logging off)
    |
    v
Identity-Aware Proxy (authentication required)
    |
    v
Cloud Run service, asia-northeast1
    |                         |
    | memory-only request     | read_only=true
    | and result state        v
    |                  dedicated static lexical bucket,
    |                  Cloud Storage asia-northeast1
    v
originating WebSocket/browser only
```

The load balancer is a
[regional external Application Load Balancer with a serverless NEG](https://cloud.google.com/load-balancing/docs/https/setting-up-reg-ext-https-serverless).
The Cloud Run service uses
[`internal-and-cloud-load-balancing` ingress](https://cloud.google.com/run/docs/securing/ingress),
has its default `run.app` URL disabled, grants no `allUsers` invoker role, and
accepts invocation only through the IAP-protected path. IAP enabled only at a
load balancer does not automatically protect a remaining direct service URL, so
all of these controls are required together; see
[IAP for Cloud Run](https://cloud.google.com/iap/docs/enabling-cloud-run).

As of this review, Google documents disabling the default Cloud Run URL as a
Preview feature, which is why the service template declares a launch stage.
The institution must review the applicable Pre-GA terms and support posture. If
Preview features are prohibited, do not silently weaken this gate: document an
approved ingress/IAM alternative and prove that the `run.app` route cannot
bypass IAP, or select another hosting design. See the current
[default-URL guidance](https://cloud.google.com/run/docs/securing/ingress#disable-url).

Cloud CDN is disabled. The current application does not yet set
`Cache-Control: no-store`; therefore the regional load balancer MUST force that
response header on every dynamic response, and the resulting public response
headers MUST be verified from outside the application. Adding the same header
inside the application remains a defense-in-depth task. Google's cacheability
rules document the effect of
[`no-store`](https://cloud.google.com/cdn/docs/caching).

## 3. Institutional prerequisites before project creation

The institution, not an application developer acting alone, owns these steps:

1. Approve the controller/processor roles, DPA, subprocessors, support-access
   locations, international-transfer position, lawful basis, user notices,
   minors policy, incident route, and the limited pilot population.
2. Select a new organization/folder location for the pilot and apply appropriate
   organization policies, billing controls, individual administrator accounts,
   MFA, and break-glass procedures.
3. Set the organization/folder
   [Cloud Logging default resource settings](https://cloud.google.com/logging/docs/default-settings)
   to `asia-northeast1` and disable the automatic `_Default` sink **before** the
   dedicated project is created. The setting affects newly created child
   resources and is not retroactive.
4. Only then create a dedicated project. Existing projects or log buckets are
   not accepted as equivalent merely because their settings were changed later;
   Google states that an existing log bucket's location cannot be changed in
   place in its [regional storage guidance](https://cloud.google.com/logging/docs/region-support).
5. Approve the IAP OAuth/identity configuration and a named pilot access group.
   Do not grant unauthenticated invocation for real-writing use.

The service, Artifact Registry repository, static lexical bucket, and required
`_Required` control-plane log bucket are placed in `asia-northeast1` where the
product supports it. No custom runtime log bucket is created. Google lists
Tokyo for [Cloud Run](https://cloud.google.com/run/docs/locations),
[Artifact Registry](https://cloud.google.com/artifact-registry/docs/repositories/repo-locations),
and [Cloud Storage](https://docs.cloud.google.com/storage/docs/bucket-locations).
Provider control-plane, support, account, and subprocessor behavior still needs
the institutional transfer and contract review; selecting Tokyo is not a claim
that every provider activity occurs only there.

## 4. Separate identities and permissions

| Identity | Intended permissions | Explicitly prohibited |
|---|---|---|
| Runtime service account | Cloud Run execution plus bucket-scoped `roles/storage.objectViewer` on the one static lexical bucket | Project-scoped Storage roles; object writes/deletes; database, durable queue, backup, or learner-data storage access |
| Resource publisher | Upload/version verified lexical assets in the static bucket through an approved release procedure | Cloud Run invocation and access to learner sessions |
| Image builder/publisher | Build and publish the reviewed image to the Tokyo Artifact Registry repository | Runtime access and production IAP user access |
| IAP service identity | Invoke only the intended Cloud Run service | Broad project administration |
| Human pilot user | Reach only the IAP-protected endpoint | Cloud console, logs, buckets, or runtime administration |

Use a dedicated user-managed
[Cloud Run service identity](https://cloud.google.com/run/docs/securing/service-identity).
At runtime, its **only object permission** is the bucket-level read-only role on
the lexical bucket; do not grant that role at project scope. The bucket uses
uniform bucket-level access, contains no learner submissions/results, and is
mounted with `read_only=true` according to the
[Cloud Run Cloud Storage volume-mount guide](https://docs.cloud.google.com/run/docs/configuring/services/cloud-storage-volume-mounts).
Build and deployment identities are separate from runtime.

## 5. Static Nation resource

The Cloud Run pilot reads the official Nation family index from the dedicated
Tokyo static-lexical bucket. The mount contains only the runtime artifact,
manifest, NOTICE/attribution, and other approved immutable lexical assets. The
application points `LDFREQ_NATION_BNCCOCA_INDEX_PATH` or
`LDFREQ_NATION_BNCCOCA_INDEX_DIR` into this read-only mount.

The current compressed Nation runtime artifact is 471,046 bytes. It is not a
Secret Manager payload: Google limits each secret-version payload to 64 KiB
(65,536 bytes) in the
[Secret Manager quotas](https://cloud.google.com/secret-manager/quotas).
Secret Manager is therefore unsuitable even before accounting for packaging and
metadata. No learner file, text, derived lexical row, result, export, or session
state may be written to this or any other Cloud Storage bucket.

The release manifest remains authoritative for artifact size, schema, row count,
source identity, and SHA-256. A resource update is a reviewed release, not a
mutable runtime download. The runtime has no upload or overwrite permission.

## 6. Controlled egress for the read-only mount

The service cannot honestly be described as having absolute zero egress because
the Cloud Storage mount uses Google APIs. The reference design uses
[Direct VPC egress](https://cloud.google.com/run/docs/configuring/vpc-direct-vpc)
for all traffic, a dedicated subnet with Private Google Access, deny-by-default
egress rules, and no Cloud NAT. DNS/routing selects
`restricted.googleapis.com`; the sole application-level API exception is object
read/list access needed by the approved lexical bucket. Google's
[Private Google Access configuration](https://cloud.google.com/vpc/docs/configure-private-google-access)
documents the restricted Google API path.

The dedicated project and lexical bucket also belong to a reviewed VPC Service
Controls perimeter. Private Google Access, firewall policy, and IAM alone do not
prove that an allowed Google API cannot become an exfiltration route to another
project. The perimeter and applicable organization policies must restrict Cloud
Run/Storage access to the approved resources; Google documents the required
ingress/egress organization settings in its
[Cloud Run VPC Service Controls guide](https://cloud.google.com/run/docs/securing/using-vpc-service-controls).

IAM and network policy are both required: network reachability alone must not
open other Google resources, and the bucket-scoped runtime role must not confer
write access. General Internet access, third-party APIs, arbitrary downloads,
telemetry SDK endpoints, and remote NLP/AI services remain blocked. Staging tests
must prove that the lexical mount can read, bucket writes fail, other buckets
and out-of-perimeter services fail, public Internet destinations fail, and
analysis succeeds without any network call containing learner content.

## 7. Candidate Cloud Run service settings

These values are inputs for review, not a command to deploy:

The checked-in template deliberately sets
`LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED=0`. The operator may change it to `1`
only alongside the recorded rights decision for the exact resource version;
the enabled-ID list alone is insufficient.

It likewise keeps `LDFREQ_REAL_WRITING_APPROVED=0`, which leaves the
synthetic/already-public-text-only banner visible. Changing that display gate is
the last reviewed release step after—not a substitute for—the acceptance and
institutional approvals in this document.

| Setting | Pilot value or gate |
|---|---|
| Region | `asia-northeast1` |
| Execution environment | Second generation |
| Ingress | `internal-and-cloud-load-balancing` |
| Default service URL | Disabled |
| Authentication | IAP required; no `allUsers` invoker |
| Runtime identity | Dedicated least-privilege service account from section 4 |
| Lexical volume | Dedicated Tokyo bucket, `read_only=true` |
| VPC egress | Direct VPC, all traffic, no Cloud NAT; restricted Google API path and VPC Service Controls perimeter |
| Concurrency | Start at `1`, then change only after isolation/load tests |
| Minimum instances | `0` |
| Maximum instances | `3`; load-test and cost-review before any increase |
| Request timeout | `1800` seconds; WebSocket/connection ceiling only, never treated as cancellation or deletion |
| Analysis deadline | `120` seconds by default (`LDFREQ_ANALYSIS_DEADLINE_SECONDS`); one-shot worker is killed and reaped |
| Temporary storage | Size-limited in-memory volume mounted at the single approved temp path |
| CDN/cache | CDN off; dynamic responses `Cache-Control: no-store` |

Cloud Run's filesystem is writable, in-memory, nonpersistent, and has no
platform size limit; it cannot be switched to a read-only root. Compensate by
running the container as a non-root UID/GID, making application directories and
files root-owned and non-writable to that UID, mounting a size-limited in-memory
volume over the approved application temporary path, directing `TMPDIR` and XDG
cache/configuration paths there, and removing ordinary write permission from
standard temporary directories in the image. Cloud Run can still expose other
writable memory-backed paths, so the release test must show that no request-
derived bytes appear outside the approved mount; it must not claim that the
platform provides an OS-level read-only root. See the
[container runtime contract](https://cloud.google.com/run/docs/container-contract)
and [in-memory volume documentation](https://cloud.google.com/run/docs/configuring/services/in-memory-volume-mounts).

## 8. Ingress, IAP, Cloud Armor, and Streamlit sessions

The regional backend has IAP enabled and permits only the named pilot group.
Load-balancer backend request logging is disabled, so Cloud Armor per-request
logging is also disabled; Cloud Armor verbose logging remains off. Its rules can
still reject or throttle an HTTP request, including a WebSocket upgrade, but do
not inspect frames after the connection upgrades. IAP likewise authenticates
the initial WebSocket upgrade rather than continuously reauthorizing every
frame. See the official [Cloud Armor logging](https://cloud.google.com/armor/docs/request-logging),
[load-balancer logging](https://cloud.google.com/load-balancing/docs/https/https-logging-monitoring),
[IAP session](https://cloud.google.com/iap/docs/sessions-howto), and
[Cloud Run WebSocket](https://cloud.google.com/run/docs/triggering/websockets)
guides.

Consequences for this Streamlit application:

- session ownership, 30-minute result expiry, deletion, reconnect behavior, and
  stale-WebSocket rejection must be enforced by the application;
- the current in-session query guard is bypassable by creating a new session and
  is not shared across workers;
- Cloud Armor IP/request rate limits are useful abuse controls but are not a
  strict authenticated account quota; and
- a durable account-wide budget plus anomaly policy remains a future production
  task. The limited IAP pilot can proceed only with an approved small cohort and
  monitored conservative limits; expansion cannot.

## 9. Timeout and cancellation gate

Cloud Run's request timeout is a connection limit, not a reliable cancellation
primitive. Google states that after a timeout response, the container can
continue processing. The application therefore imposes a shorter 120-second
default analysis deadline. Each call starts a new process group, sends source
only over a bounded stdin pipe, discards ordinary worker stdout/stderr, loads
rights-gated lexical resources in the child, accepts only bounded typed
aggregate frames, requires exit code zero, and terminates/reaps the worker on
deadline, callback abort, or error. The child disables core dumps before
reading source. See the
[Cloud Run request-timeout guide](https://cloud.google.com/run/docs/configuring/request-timeout).

Repository tests now cover worker/direct equivalence, canary non-disclosure,
input rejection before spawn, application deadline and reaping, malformed
result rejection, and a Streamlit-style callback abort. This is not a claim of
complete cancellation: Streamlit does not yet provide a verified immediate
signal for browser disconnect or an in-flight **Delete data** click while the
session thread is occupied. Real-browser tests must still cover platform
timeout, client disconnect, user deletion, WebSocket reconnect, TTL expiry,
process-group/FD cleanup, and container concurrency. Until those pass, the
120-second deadline is the final backstop and real learner writing remains
blocked.

## 10. Logging and deletion model

The pilot stores no application, access, request, security, or error logs. The
Streamlit parent process and analysis worker discard ordinary stdout and stderr;
no application logging client, error reporter, request tracer, analytics SDK,
or session-replay agent is installed.

Cloud Run nevertheless creates request-log entries automatically and offers no
service-level switch to prevent their generation. The pilot prevents retention:
the dedicated project inherits a disabled `_Default` sink, has no user-defined
or inherited aggregated sink for runtime logs, and creates no custom runtime
log bucket. Consequently Cloud Run request/container/system entries and
application errors are **not stored or queryable**. The load-balancer backend
has request logging disabled, which also disables Cloud Armor per-request
logging; Cloud Armor verbose logging remains off. IAP Data Access logging is not
enabled for end-user requests. These settings must be verified outside the
application because the Cloud Run service YAML cannot express them. See
[Cloud Run logging](https://cloud.google.com/run/docs/logging),
[Cloud Logging default settings](https://cloud.google.com/logging/docs/default-settings),
[load-balancer logging](https://cloud.google.com/load-balancing/docs/https/https-logging-monitoring),
and [Cloud Armor request logging](https://cloud.google.com/armor/docs/request-logging).

The only stored log class is Google Cloud's `_Required` control-plane bucket.
It retains required Admin Activity, System Event, and related audit records for
400 days; the retention and contents cannot be changed or excluded. Restrict
access and verify that it contains only administrative/configuration events—not
end-user HTTP requests, application errors, essay text, filenames, request/
response bodies, tokens, off-list items, or other derived vocabulary. Google's
[retention table](https://cloud.google.com/logging/docs/store-log-entries)
documents this platform behavior.

## 11. Reproducible build and immutable release gate

The repository now has a pilot scaffold in `deploy/cloud-run/`: the Dockerfile
pins the official Python 3.12.13 Alpine 3.23 `linux/amd64` child manifest,
forces that platform, rejects a non-digest-shaped override, runs as non-root,
and keeps application files root-owned. `base-image.json` separately records
the tag index, selected child manifest, image config, Python version/source
hash, and verification date. The 46-package production graph uses 45
musllinux/pure wheels plus the separately reviewed watchdog pure-Python wheel;
the 51-package CI graph targets Ubuntu 24.04. Both graphs permit one reviewed wheel SHA-256 per package and
install with `--no-deps`, `--only-binary=:all:`, and `--require-hashes`, without
dependency resolution or source builds. Watchdog's hash, purelib metadata,
paths, native-artifact absence, and `RECORD` are verified before its explicit
foreign-platform installation.

This closes package and base-image *input* identity, but it does not yet prove a
production release. The complete application image has not been built in a
clean hosted Linux job, assigned its own resulting digest, scanned, or used to
regenerate the golden outputs.

Before production approval, the release build must:

1. verifies the recorded base-image tag index, `linux/amd64` child manifest,
   config digest, and Python identity against Docker Registry HTTP API V2;
2. verifies that the complete direct/transitive dependency lock resolves to the
   one recorded wheel hash per package without source builds or silent
   dependency resolution;
3. copies only version-controlled application files and approved open assets;
4. runs as a fixed non-root UID/GID with root-owned, non-writable application
   files;
5. excludes ignored local TAALES/COCA/AntBNC and any learner/sample data from the
   build context;
6. records the source commit, dependency-lock hash, resource-manifest hashes,
   build provenance, image digest, and vulnerability-scan result; and
7. pushes to a Tokyo Artifact Registry repository and deploys the resulting
   image by digest, never by a floating tag.

The Nation object remains independently hash- and manifest-verified at startup.
A change to the base-image digest, dependency lock, lexical artifact, IAM,
network, logging filters, or privacy-sensitive runtime setting creates a new
reviewed release candidate.

## 12. What a Cloud Run service YAML does not prove

A Cloud Run service YAML can express the container image, service identity,
ingress annotation, resource limits, environment, and volume mount. It does not
create or fully evidence this architecture. In particular, the service YAML
does **not** contain the organization/folder Logging defaults, new-project
creation, regional load balancer, serverless NEG, certificate/DNS, Cloud Armor
policy, IAP policy and users, direct-URL reachability tests, load-balancer log
configuration, Logging sink inventory, bucket IAM, VPC firewall/DNS/Private Google Access, or
VPC Service Controls perimeter, or institutional approvals.

Those components require separately reviewed infrastructure-as-code modules and
external verification. A service YAML by itself must never be presented as proof
that the Tokyo/IAP/log-retention/privacy design has been deployed.

## 13. Pilot stages and release gates

### Stage 0 — institutional design approval

- Complete DPA/subprocessor/transfer, privacy, security, minors, ethics, notice,
  ownership, cost, incident, and pilot-cohort decisions.
- Configure inherited Logging defaults before creating the dedicated project.
- Approve the data-flow and identity/permission matrix.

### Stage 1 — reproducible application image

- Recheck the pinned base-image identity and hash-locked wheel set, then build
  the existing non-root Docker scaffold from a clean tracked checkout.
- Use the digest-pinned Dockerfile frontend, commit-derived
  `SOURCE_DATE_EPOCH`, timestamp rewriting, a pinned Buildx release, and a
  digest-pinned BuildKit container with a fixed compatibility mode. Require two
  independent no-cache OCI production builds to yield the same manifest,
  config, and complete layer-digest list.
- Recompute the public golden outputs offline in the non-deployable verification
  stage; the production stage must contain neither tests nor verification
  scripts.
- Generate the SBOM and vulnerability report directly from that OCI archive.
  Permit registry login only after the scan passes, then require the published
  rebuild to retain the scanned manifest digest.
- Record the resulting application-image digest, provenance, vulnerability
  scan, and golden-fixture reproduction; the pinned base-image digest alone is
  not the resulting application-image digest.
- Run unit/privacy tests without cloud access.
- Verify the image and build context contain none of the ignored local resources
  or test learner text.

### Stage 2 — synthetic Tokyo infrastructure pilot

- Provision all components with separately reviewed infrastructure as code.
- Use only synthetic high-entropy canaries.
- Verify region, IAP, direct-URL blocking, no-store, IAM, no-NAT egress, lexical
  reads, denied writes/out-of-perimeter access, disabled load-balancer logging,
  zero runtime-log destinations, nonpersistence, and cleanup.

### Stage 3 — limited real-writing pilot, only after release blockers pass

- Require the approved IAP group and prohibit anonymous access.
- Retain the passing application-deadline tests and additionally pass
  disconnect/delete cancellation, container cleanup, and cross-session tests.
- Monitor only aggregate platform health metrics and use conservative instance/
  input limits.
- Stop immediately on any source/derived-content canary outside the originating
  session.

### Stage 4 — production consideration, not implied by the pilot

- Implement and test a deployment-wide authenticated account quota that survives
  new sessions and multiple workers.
- Complete load, availability, accessibility, security, deletion, and incident
  exercises and repeat institutional approval.
- Reassess provider terms, product behavior, regions, subprocessors, and all
  official links at the decision date.

## 14. Minimum evidence package

Retain content-free evidence for review:

- organization/folder default settings captured before project creation;
- project/resource inventory showing Tokyo locations;
- image digest, base-image digest, dependency/resource hashes, provenance, and
  scan result;
- IAM policy showing the runtime's sole bucket-scoped read-only object role;
- tests showing bucket writes, other buckets, and public Internet egress fail;
- VPC Service Controls inventory and tests showing out-of-perimeter Google API
  resources are unreachable;
- tests showing IAP is required, `run.app` is disabled, and direct ingress fails;
- institutional acceptance of the default-URL feature's current launch stage,
  or evidence for an approved equivalent that cannot bypass IAP;
- browser/header evidence for `Cache-Control: no-store` and CDN disabled;
- inherited disabled `_Default` configuration, complete sink inventory,
  load-balancer/Cloud Armor logging-off configuration, plus `_Required` access
  policy and 400-day disclosure;
- canary searches across logs, traces, metrics, temp paths, object stores,
  snapshots, queues, and backups for every success/error/timeout/delete path;
- non-root and filesystem-write tests, including bounded temporary-volume use;
- WebSocket/reconnect, 30-minute expiry, deletion, timeout, and cancellation
  results; and
- signed institutional approvals and a completed incident exercise.

No commands in this template perform a deployment. Provisioning, enabling paid
services, assigning IAM, uploading resources, DNS changes, or admitting users
requires a separate approved implementation task.
