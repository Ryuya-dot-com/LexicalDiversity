# Public Git history migration gate

Status: **decision required before any release tag**
Audit date: 2026-07-24

## Why the current-tree gate is not enough

The GitHub origin is already public. Its current `main` is commit `f369fae`,
with 22 reachable commits. Removing a payload in a later commit prevents it
from entering that later tree and its source archive, but does not remove the
blob from earlier commits or from existing clones.

The reviewed working tree is moving permission-pending and server-only payloads
out of the release inventory. However, the new
[`check_git_history.py`](../scripts/check_git_history.py) gate finds 44 policy
findings across 62 unique paths in the 22 reachable commit trees. Three are
explicitly permission-pending registry artifacts: the AntBNC lemma list and the
EAPFoundation BNC/COCA PDF and XLSX. Other findings include previously public
but now server-only or legacy paths. Those are product-boundary and repository-
hygiene findings; their presence is not, by itself, a new claim that every one
has the same rights status as the three yellow artifacts.

The local index is also unsafe to commit as-is. It currently has 15 `AD`, 13
`AM`, and 6 `MM` paths. In particular, `AD` means a file is staged for addition
but absent from the current worktree, so committing the index would resurrect
deleted Quarto intermediate files. Run
`python scripts/check_staging_coherence.py` before constructing any commit.

Finally, the local object store contains 948 loose objects using approximately
793 MiB, including unreachable objects much larger than any reviewed release
artifact. Unreachable objects are not transferred by an ordinary push and are
not included by `git archive`, but they can be retained by filesystem backups
or deliberately inclusive Git bundles. Their contents were not opened during
this audit. Do not run an irreversible prune until recovery requirements and an
offline backup have been decided.

## Stop rules

- Do not create a release tag from the current reachable history.
- Do not commit the current index while the staging-coherence gate is blocked.
- Do not treat a deletion commit as removal from already published history.
- Do not force-push rewritten history, delete the remote, change repository
  visibility, or prune local objects without explicit authorization.
- Do not copy `.git/` when constructing a clean public release repository.

## Migration choices

| Choice | Advantages | Costs and limits | Recommendation |
|---|---|---|---|
| New clean public repository from a reviewed source tree | Clear provenance boundary; no legacy blobs; no force rewrite; simplest release proof | New repository identity; issues/links and stars do not move automatically; old public origin still needs a visibility/archive decision | **Preferred** |
| Rewrite the current public repository | Keeps repository URL and issue location | Invalidates commit IDs and clones; requires coordinated force push; cannot retract existing clones, caches, or forks; conflicts with the normal immutable-history rule | Only after an explicit incident/migration decision |

The preferred sequence is:

1. Decide whether the existing public origin should be made private temporarily
   while the three yellow-resource cases are reviewed.
2. Preserve one controlled offline archival copy of the current repository.
3. Resolve the working tree into reviewed logical changes without using the
   current mixed index as a commit source.
4. Create a new repository without copying `.git/`, admit only the exact paths
   accepted by `check_public_release.py`, and recreate coherent commits.
5. Require the current-tree gate, history gate, fixed-runtime tests, golden
   outputs, deterministic source archive, and image evidence to pass there.
6. Only then create an annotated release-candidate tag and decide how to label,
   archive, or restrict the old origin.

## Deterministic clean-tree construction

Do not use `git add -A`, the current index, or a filesystem copy of the project
root as the bootstrap source. Build a review artifact with:

```bash
python3 scripts/build_clean_public_candidate.py \
  --output /tmp/ldfreq-clean-source.tar.gz \
  --evidence-output /tmp/ldfreq-clean-source-evidence.json
```

The builder starts from Git's cached-plus-untracked inventory, then applies the
current ignore rules even to tracked or staged paths. It admits only existing
regular files, rejects symlinks and unsafe paths, requires the release-contract
files, and applies `check_public_release.py` before writing anything. The tar
has a fixed prefix, sorted paths, zero timestamps and owner IDs, normalized
modes, deterministic gzip bytes, and exclusive outputs. The canonical evidence
records the source `HEAD`, whether the source worktree was dirty, all admitted
file hashes and modes, and ignored/absent counts. A dirty source is therefore
reviewable but must not be mistaken for an immutable commit.

Extract into a new empty directory, initialize a new Git repository there, and
verify before any remote write:

```bash
git init -b main
git add --all
python scripts/check_staging_coherence.py
python scripts/check_public_release.py
git diff --cached --check
git commit -m "Initial public release candidate"
python scripts/check_git_history.py
python scripts/check_runtime_environment.py
python -m pip check
python -m pytest -p no:cacheprovider
git status --short
```

The default suite is independent of locally installed permission-pending or
server-only payloads. The server-only Streamlit integration is a separate
operator check after provisioning the verified Nation artifact:

```bash
LDFREQ_RUN_SERVER_INTEGRATION=1 \
  python -m pytest tests/test_app_query_guard.py
```

Before accepting the bootstrap commit, compare every committed Git blob's
content SHA-256 with the evidence manifest. The repository-wide
`*.csv binary` attribute is part of this identity contract: it prevents Git EOL
normalization from changing reviewed CSV bytes between the archive and commit.
No push, visibility change, tag, or deletion of the old origin is implied by a
successful local bootstrap verification.

This migration is a release-integrity task, not a statistical-analysis task.
Synthetic generation and ELLIPSE outcome analysis remain downstream.
