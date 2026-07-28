# COCA-derived output publication gate

Decision date: 2026-07-27
Decision owner: Maintainer and Research Lead
State: **QUARANTINED / PUBLICATION NO-GO**
Scope: legacy TAALES 2.8.1 tables labelled as COCA-derived and every result
computed from them

## 1. Decision

The legacy TAALES–COCA comparison is an internal calibration exercise, not
public validation evidence. Until the exit gates below are satisfied, none of
its plans, wrappers, measurements, correlations, figures, table fingerprints,
or other derived outputs may enter a public Git history, source archive,
package, website, CI artifact, release-evidence bundle, or manuscript
supplement.

This is a conservative publication and product decision, not a legal opinion.
The absence of lexical rows or document identifiers from an aggregate result
does not itself establish a right to publish that result. The TAALES software
license also does not by itself establish redistribution or derivative-output
rights for third-party corpus tables bundled with a local installation.

The decision does **not** block the `ldfreq` R package, its open-resource
loaders, CRAN preparation, or the COCA-independent ELLIPSE human-rating
analysis. COCA receives zero public-evidence credit while this gate is open.

## 2. Current evidence classification

| Item | Current use | Public status | Reason |
|---|---|---|---|
| Local legacy TAALES 2.8.1 COCA-labelled tables | private calibration input | blocked | exact COCA edition and derivative-output rights are unverified |
| Comparison plan and execution wrappers | private reproducibility record | blocked | they expose a workflow whose required input cannot be independently obtained under a documented public route |
| Aggregate correlations, coverage, intervals, and figure | private descriptive result | blocked | aggregate-result publication is `review-required`, not approved |
| Table or bundle hashes/fingerprints | private identity record | blocked | identity evidence can identify an unapproved proprietary input and is not a substitute for permission |
| ELLIPSE human-rating analysis using TUBELEX | separate conditional evidence | separately reviewable | it does not depend on the COCA tables, but its uncertainty and collinearity limits still constrain claims |

The internal COCA comparison used token-weighted summaries, whereas the
ELLIPSE primary human-rating analysis used type-weighted TUBELEX variables.
Consequently, the two analyses do not form a single triangular validity claim.

## 3. Exit gates

All applicable gates must pass. A partial permission narrows the publishable
scope; it does not imply passage of the remaining gates.

| Gate | Required evidence | Failure action |
|---|---|---|
| **C0 — quarantine** | tracked, untracked, ignored, archive, build-context, CI-artifact, and reachable-history scans show no COCA-derived plan, code, result, figure, or fingerprint in the public candidate | stop the candidate; remove the item through a reviewed, recoverable change and rescan |
| **C1 — rights** | dated written evidence identifies the exact table/bundle and explicitly covers the intended publication of each artifact class: code, aggregate coefficients, intervals/coverage, figures, and fingerprints | retain privately; after a two-week rights timebox, defer indefinitely rather than delaying `ldfreq` |
| **C2 — provenance** | each result manifest records upstream registry IDs, exact source identity, transformation chain, software/runtime identities, and the publication decision for every upstream resource | do not publish the result; strengthen the derived-output gate rather than relying on path names or `.gitignore` |
| **C3 — independent reproducibility** | an authorized third party can obtain the exact input or an approved snapshot and reproduce the result from a clean environment | describe the exercise only as a private legacy calibration; do not call it public reproducible evidence |
| **C4 — construct alignment** | the weighting unit, lookup unit, controls, and target construct match the public validation claim, with sensitivity analyses fixed before outcome review | keep the analyses separate; do not infer that a COCA-convergent metric explains human ratings |
| **C5 — claim review** | the report says exactly which gates passed, reports failed thresholds and uncertainty, and avoids equivalence, interchangeability, causality, proficiency prediction, and independent-effect claims | withhold the report until corrected and independently reviewed |

Written permission limited to aggregate reporting can at most open the named
aggregate artifacts. It does not authorize publishing source tables, hashes,
wrappers, or a reproducibility bundle unless those classes are explicitly
covered. If C1 cannot be closed, the terminal state is **private archived
calibration**, not an unresolved blocker.

## 4. Enforced implementation and operational closeout

The code-level P0 controls below are enforced as of 2026-07-27. They are not a
COCA publication clearance: C1--C5 remain closed. Each actual public candidate
still needs its own external approval and evidence-v2 closeout.

1. **`DER-01` — enforced:** positive Git/CI allow-listing was removed; exact
   COCA paths and both current result directories are quarantined from source
   and container build contexts.
2. **`DER-02` — enforced for future publication:** every publishable analysis
   bundle must carry a byte-pinned manifest enumerating upstream registry IDs,
   artifact classes, provenance, aggregation boundaries, attribution, and a
   dated review with an approval reference. Neither current bundle has one.
3. **`DER-03` — enforced:** the release checker uses a fixed decision map and
   rejects any upstream resource that is not green, license-verified, and
   approved for aggregate publication under the requested review scope. A
   manifest cannot redefine the controlled root, schema, classes, or statuses.
4. **`DER-04` — enforced:** clean-candidate construction requires an externally
   stored, approved exact-byte selection manifest. Tracked or non-ignored
   status alone never admits a file, and builder outputs cannot be written into
   the reviewed source tree.
5. **`DER-05` — enforced by regression tests:** current/history quarantine,
   omitted and unknown upstreams, blocked/review-required decisions, contract
   tampering, extra files, disguised archives, unreviewed worktree additions,
   and post-review worktree mutation all fail closed.
6. **`DER-06` — enforced in evidence schema v2:** evidence binds the reviewer,
   decision date, approval reference, selection and registry hashes, externally
   attested scan results, reviewed result bundles, and excluded output families.

Operational closeout is deliberately not reusable: an approver must inspect
the exact bytes of each new candidate, supply the external selection and scan
attestations, run the builder, and accept its evidence-v2 record. The current
mixed legacy checkout is not itself an approved candidate.

## 5. Scientific continuation without COCA

The public validation path uses only inputs with a documented acquisition and
publication route:

1. finish production R loaders for NGSL, TUBELEX-EN, and Open English WordNet;
2. establish hand-calculated validity and R/Python agreement on CC0 fixtures;
3. rerun the ELLIPSE feature pipeline through the released R implementation,
   keeping learner text local and publishing only reviewed aggregate outputs;
4. treat the completed ELLIPSE interval as conditional on the 44 observed LOPO
   fold deltas, not as unconditional new-prompt uncertainty;
5. externally timestamp a new validation plan before a new outcome run,
   including type/token weighting, suppression/collinearity diagnostics, and
   uncertainty that reruns the full fitting pipeline where feasible; and
6. require genuinely new data or a separately held-out corpus before using
   “confirmatory replication” or a broad external-validity claim.

An open-corpus comparator can replace the scientific role originally imagined
for COCA, but it is a new analysis with its own construct, plan, and evidence.
It must not be presented as a numerical substitute for the quarantined tables.

## 6. Review triggers

Reopen this decision only when one of the following occurs:

- dated written permission covering at least one named artifact class arrives;
- an exact, legally documented public acquisition route for the input appears;
- a fully open comparator makes the COCA exercise unnecessary; or
- a public-candidate scan detects a COCA-derived item, which triggers immediate
  containment rather than publication review.

Until then, omit the COCA comparison from completion scores, abstracts,
validation reports, README claims, release notes, and Methods Showcase evidence.
