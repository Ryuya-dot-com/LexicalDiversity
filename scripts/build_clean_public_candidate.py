#!/usr/bin/env python3
"""Build a deterministic clean-history candidate from reviewed worktree files."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_release_archive import deterministic_gzip, write_exclusive
from scripts.check_public_release import (
    QUARANTINED_DERIVED_FAMILIES,
    release_violations,
    result_bundle_review,
)


PREFIX = "LexicalDiversity-clean-public/"
EXECUTABLE_PATHS = {"deploy/cloud-run/entrypoint.sh"}
REQUIRED_PATHS = {
    ".gitattributes",
    ".github/workflows/ci.yml",
    ".github/workflows/image-candidate.yml",
    ".github/workflows/release.yml",
    ".gitignore",
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "app.py",
    "data/resource_registry.json",
    "docs/public-history-migration.md",
    "docs/v1-metric-scope.json",
    "ldfreq/release.json",
    "requirements-ci-linux-x86_64.lock",
    "scripts/build_candidate_image_evidence.py",
    "scripts/build_oci_image_evidence.py",
    "scripts/check_git_history.py",
    "scripts/check_public_release.py",
    "scripts/check_staging_coherence.py",
    "tests/fixtures/v1_golden/manifest.json",
}
SELECTION_SCHEMA_VERSION = "1.0.0"
SELECTION_ROLES = {
    "documentation",
    "governance",
    "release-control",
    "resource",
    "source",
    "test",
    "workflow",
}
REQUIRED_SCAN_IDS = {
    "derived-result-rights",
    "public-release-inventory",
    "quarantined-derived-output",
}


class CleanCandidateError(ValueError):
    """Raised when a clean-history source candidate is unsafe or incomplete."""


@dataclass(frozen=True)
class CandidateInventory:
    paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    absent_index_paths: tuple[str, ...]
    file_payloads: dict[str, bytes] = field(default_factory=dict)
    file_identities: dict[str, dict[str, Any]] = field(default_factory=dict)
    roles: dict[str, str] = field(default_factory=dict)
    selection: dict[str, Any] | None = None
    registry_identity: dict[str, Any] | None = None
    result_bundles: tuple[dict[str, Any], ...] = ()
    excluded_derived_output_families: tuple[dict[str, Any], ...] = ()


def _git_bytes(*arguments: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        input=input_bytes,
    )
    if result.returncode != 0:
        raise CleanCandidateError(
            f"Git command failed ({result.returncode}): git {' '.join(arguments)}"
        )
    return result.stdout


def _git_text(*arguments: str) -> str:
    return _git_bytes(*arguments).decode("utf-8", errors="strict").strip()


def _decode_paths(payload: bytes) -> list[str]:
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in payload.split(b"\0")
        if item
    ]


def ignored_paths(paths: Iterable[str]) -> set[str]:
    encoded = b"\0".join(
        path.encode("utf-8", errors="surrogateescape") for path in paths
    )
    if encoded:
        encoded += b"\0"
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "-z", "--stdin"],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        input=encoded,
    )
    if result.returncode not in {0, 1}:
        raise CleanCandidateError(
            f"git check-ignore failed with status {result.returncode}"
        )
    return set(_decode_paths(result.stdout))


def _collision_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def _valid_review_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _external_path(path: Path, *, label: str) -> Path:
    expanded = path.expanduser()
    lexical = expanded.absolute()
    resolved = expanded.resolve()
    project = PROJECT_ROOT.resolve()
    if (
        lexical == project
        or project in lexical.parents
        or resolved == project
        or project in resolved.parents
    ):
        raise CleanCandidateError(f"{label} must be external to the source tree")
    return resolved


def _load_selection(selection_manifest: Path) -> tuple[dict[str, Any], bytes]:
    resolved = _external_path(selection_manifest, label="selection manifest")
    if resolved.is_symlink() or not resolved.is_file():
        raise CleanCandidateError("reviewed selection manifest is absent")
    payload = resolved.read_bytes()
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CleanCandidateError("selection manifest is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise CleanCandidateError("selection manifest must be a JSON object")
    required = {
        "schema_version",
        "candidate_id",
        "resource_registry",
        "review",
        "entries",
        "excluded_derived_output_families",
        "required_scans",
    }
    missing = sorted(required - set(document))
    unknown = sorted(set(document) - required)
    if missing or unknown:
        detail = []
        if missing:
            detail.append("missing=" + ",".join(missing))
        if unknown:
            detail.append("unknown=" + ",".join(unknown))
        raise CleanCandidateError(
            "selection manifest fields differ: " + "; ".join(detail)
        )
    if document["schema_version"] != SELECTION_SCHEMA_VERSION:
        raise CleanCandidateError("selection manifest schema version differs")
    if not isinstance(document["candidate_id"], str) or not document["candidate_id"]:
        raise CleanCandidateError("selection manifest candidate_id is missing")
    review = document["review"]
    if not isinstance(review, dict) or review.get("status") != "approved":
        raise CleanCandidateError("selection manifest review is not approved")
    if set(review) != {
        "status",
        "reviewer_role",
        "reviewed_on",
        "approval_reference",
    }:
        raise CleanCandidateError("selection manifest review fields differ")
    for key in ("reviewer_role", "approval_reference"):
        if not isinstance(review.get(key), str) or not review[key]:
            raise CleanCandidateError(f"selection manifest review lacks {key}")
    if not _valid_review_date(review.get("reviewed_on")):
        raise CleanCandidateError("selection manifest review date is invalid")
    scans = document["required_scans"]
    if not isinstance(scans, list):
        raise CleanCandidateError("selection manifest scan contract differs")
    scans_by_id: dict[str, dict[str, Any]] = {}
    for item in scans:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise CleanCandidateError("selection manifest scan result is malformed")
        scan_id = str(item["id"])
        if set(item) != {
            "id",
            "status",
            "finding_count",
            "performed_on",
            "reviewer_role",
            "evidence_reference",
        }:
            raise CleanCandidateError(
                f"selection scan result fields differ: {scan_id}"
            )
        if scan_id in scans_by_id:
            raise CleanCandidateError(f"duplicate selection scan result: {scan_id}")
        if item.get("status") != "pass" or item.get("finding_count") != 0:
            raise CleanCandidateError(f"selection scan did not pass: {scan_id}")
        if not _valid_review_date(item.get("performed_on")):
            raise CleanCandidateError(f"selection scan date is invalid: {scan_id}")
        for key in ("reviewer_role", "evidence_reference"):
            value = item.get(key)
            if not isinstance(value, str) or not value.strip():
                raise CleanCandidateError(
                    f"selection scan lacks {key}: {scan_id}"
                )
        scans_by_id[scan_id] = item
    if set(scans_by_id) != REQUIRED_SCAN_IDS:
        raise CleanCandidateError("selection manifest scan contract differs")
    excluded = document["excluded_derived_output_families"]
    if not isinstance(excluded, list):
        raise CleanCandidateError("selection manifest exclusion inventory is malformed")
    excluded_by_id: dict[str, dict[str, Any]] = {}
    for item in excluded:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise CleanCandidateError("selection manifest exclusion is malformed")
        family_id = str(item["id"])
        if set(item) != {"id", "decision", "detector"}:
            raise CleanCandidateError(
                f"selection manifest exclusion fields differ: {family_id}"
            )
        if family_id in excluded_by_id:
            raise CleanCandidateError(f"duplicate excluded family: {family_id}")
        if item.get("decision") not in {"blocked", "review-required"}:
            raise CleanCandidateError(f"excluded family lacks fail-closed decision: {family_id}")
        if not isinstance(item.get("detector"), str) or not item["detector"]:
            raise CleanCandidateError(f"excluded family lacks detector: {family_id}")
        excluded_by_id[family_id] = item
    missing_families = sorted(set(QUARANTINED_DERIVED_FAMILIES) - set(excluded_by_id))
    if missing_families:
        raise CleanCandidateError(
            "selection manifest omits quarantined families: "
            + ", ".join(missing_families)
        )
    return document, payload


def candidate_inventory(selection_manifest: Path) -> CandidateInventory:
    """Select exactly the externally reviewed worktree byte inventory."""

    selection, selection_payload = _load_selection(selection_manifest)

    listed = sorted(
        set(
            _decode_paths(
                _git_bytes(
                    "ls-files",
                    "--cached",
                    "--others",
                    "--exclude-standard",
                    "-z",
                )
            )
        )
    )
    ignored = ignored_paths(listed)
    discovered: list[str] = []
    absent: list[str] = []
    for raw_path in listed:
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise CleanCandidateError(f"unsafe candidate path: {raw_path}")
        path = PROJECT_ROOT / raw_path
        if path.is_symlink():
            raise CleanCandidateError(f"candidate symlink is prohibited: {raw_path}")
        if not path.exists():
            absent.append(raw_path)
            continue
        if not path.is_file():
            raise CleanCandidateError(f"candidate path is not a file: {raw_path}")
        discovered.append(raw_path)

    if absent:
        raise CleanCandidateError(
            "candidate index contains absent paths: " + ", ".join(absent)
        )

    entries = selection.get("entries")
    if not isinstance(entries, list) or not entries:
        raise CleanCandidateError("selection manifest entry inventory is empty")
    declared: dict[str, dict[str, Any]] = {}
    collision_keys: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise CleanCandidateError(f"selection entry {index} is not an object")
        required = {"path", "bytes", "sha256", "role"}
        missing = sorted(required - set(entry))
        unknown = sorted(set(entry) - required)
        if missing or unknown:
            detail = []
            if missing:
                detail.append("missing=" + ",".join(missing))
            if unknown:
                detail.append("unknown=" + ",".join(unknown))
            raise CleanCandidateError(
                f"selection entry {index} fields differ: " + "; ".join(detail)
            )
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            raise CleanCandidateError(f"selection entry {index} path is not text")
        pure = PurePosixPath(raw_path.replace("\\", "/"))
        normalized = pure.as_posix().rstrip("/")
        if (
            raw_path != normalized
            or pure.is_absolute()
            or ".." in pure.parts
            or not pure.parts
        ):
            raise CleanCandidateError(f"unsafe or noncanonical selection path: {raw_path}")
        if normalized in declared:
            raise CleanCandidateError(f"duplicate selection path: {normalized}")
        collision = _collision_key(normalized)
        if collision in collision_keys:
            raise CleanCandidateError(
                "case/Unicode-colliding selection paths: "
                f"{collision_keys[collision]}, {normalized}"
            )
        collision_keys[collision] = normalized
        if entry["role"] not in SELECTION_ROLES:
            raise CleanCandidateError(f"unknown selection role: {entry['role']}")
        if type(entry["bytes"]) is not int or entry["bytes"] < 0:
            raise CleanCandidateError(f"invalid byte identity: {normalized}")
        if (
            not isinstance(entry["sha256"], str)
            or len(entry["sha256"]) != 64
            or any(character not in "0123456789abcdef" for character in entry["sha256"])
        ):
            raise CleanCandidateError(f"invalid SHA-256 identity: {normalized}")
        declared[normalized] = entry

    discovered_set = set(discovered)
    declared_set = set(declared)
    if discovered_set != declared_set:
        extras = sorted(discovered_set - declared_set)
        missing = sorted(declared_set - discovered_set)
        detail: list[str] = []
        if extras:
            detail.append("unreviewed=" + ",".join(extras))
        if missing:
            detail.append("missing=" + ",".join(missing))
        raise CleanCandidateError("selection differs from worktree inventory: " + "; ".join(detail))

    payloads: dict[str, bytes] = {}
    identities: dict[str, dict[str, Any]] = {}
    roles: dict[str, str] = {}
    for path in sorted(discovered):
        payload = (PROJECT_ROOT / path).read_bytes()
        observed = {
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }
        entry = declared[path]
        if observed["bytes"] != entry["bytes"] or observed["sha256"] != entry["sha256"]:
            raise CleanCandidateError(f"selected file identity differs: {path}")
        payloads[path] = payload
        identities[path] = observed
        roles[path] = str(entry["role"])

    missing = sorted(REQUIRED_PATHS - discovered_set)
    if missing:
        raise CleanCandidateError(
            "candidate lacks required paths: " + ", ".join(missing)
        )
    registry_control = selection.get("resource_registry")
    if not isinstance(registry_control, dict):
        raise CleanCandidateError("selection registry identity is malformed")
    if set(registry_control) != {"path", "bytes", "sha256"}:
        raise CleanCandidateError("selection registry identity fields differ")
    if registry_control.get("path") != "data/resource_registry.json":
        raise CleanCandidateError("selection registry path differs")
    registry_payload = payloads["data/resource_registry.json"]
    registry_identity = {
        "path": "data/resource_registry.json",
        "bytes": len(registry_payload),
        "sha256": hashlib.sha256(registry_payload).hexdigest(),
    }
    if (
        registry_control.get("bytes") != registry_identity["bytes"]
        or registry_control.get("sha256") != registry_identity["sha256"]
    ):
        raise CleanCandidateError("selection registry identity differs")
    registry = json.loads(registry_payload.decode("utf-8"))
    violations = release_violations(
        registry,
        discovered,
        project_root=PROJECT_ROOT,
        file_payloads=payloads,
    )
    if violations:
        raise CleanCandidateError("; ".join(violations))
    result_violations, result_bundles = result_bundle_review(
        registry,
        discovered,
        project_root=PROJECT_ROOT,
        file_payloads=payloads,
    )
    if result_violations:
        raise CleanCandidateError("; ".join(result_violations))

    excluded = tuple(
        {
            "id": str(item["id"]),
            "decision": str(item["decision"]),
            "detector": str(item["detector"]),
            "selected_match_count": 0,
        }
        for item in sorted(
            selection["excluded_derived_output_families"],
            key=lambda value: value["id"],
        )
    )
    selection_identity = {
        "candidate_id": selection["candidate_id"],
        "schema_version": selection["schema_version"],
        "bytes": len(selection_payload),
        "sha256": hashlib.sha256(selection_payload).hexdigest(),
        "review": selection["review"],
        "required_scans": sorted(
            selection["required_scans"],
            key=lambda item: item["id"],
        ),
    }
    return CandidateInventory(
        tuple(sorted(discovered)),
        tuple(sorted(ignored)),
        tuple(absent),
        payloads,
        identities,
        roles,
        selection_identity,
        registry_identity,
        tuple(result_bundles),
        excluded,
    )


def file_identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def tar_payload(
    paths: Iterable[str],
    *,
    root: Path = PROJECT_ROOT,
    payloads: dict[str, bytes] | None = None,
) -> bytes:
    """Create a canonical uncompressed tar stream for a candidate inventory."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT) as output:
        for relative in sorted(paths):
            payload = (
                payloads[relative]
                if payloads is not None
                else (root / relative).read_bytes()
            )
            member = tarfile.TarInfo(PREFIX + relative)
            member.size = len(payload)
            member.mode = 0o755 if relative in EXECUTABLE_PATHS else 0o644
            member.mtime = 0
            member.uid = 0
            member.gid = 0
            member.uname = ""
            member.gname = ""
            output.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def evidence_document(
    inventory: CandidateInventory,
    archive_payload: bytes,
) -> dict[str, Any]:
    files = {}
    for path in inventory.paths:
        identity = inventory.file_identities.get(path)
        if identity is None:
            identity = file_identity(PROJECT_ROOT / path)
        files[path] = {
            **identity,
            "mode": "755" if path in EXECUTABLE_PATHS else "644",
            "role": inventory.roles.get(path, "unclassified-test-fixture"),
        }
    inventory_payload = json.dumps(
        files,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "candidate_schema_version": 2,
        "purpose": "clean-history-bootstrap-review",
        "source": {
            "head": _git_text("rev-parse", "HEAD"),
            "worktree_dirty": bool(
                _git_text("status", "--porcelain=v1", "--untracked-files=all")
            ),
        },
        "archive": {
            "prefix": PREFIX,
            "bytes": len(archive_payload),
            "sha256": hashlib.sha256(archive_payload).hexdigest(),
        },
        "release_policy": {
            "selection_manifest": inventory.selection,
            "resource_registry": inventory.registry_identity,
            "scans": list(
                (inventory.selection or {}).get("required_scans", [])
            ),
            "derived_result_bundles": list(inventory.result_bundles),
            "excluded_derived_output_families": list(
                inventory.excluded_derived_output_families
            ),
        },
        "inventory": {
            "file_count": len(inventory.paths),
            "total_file_bytes": sum(item["bytes"] for item in files.values()),
            "sha256": hashlib.sha256(inventory_payload).hexdigest(),
            "ignored_path_count": len(inventory.ignored_paths),
            "absent_index_path_count": len(inventory.absent_index_paths),
            "files": files,
        },
    }


def canonical_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--selection-manifest", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        output_path = _external_path(args.output, label="candidate archive output")
        evidence_path = _external_path(
            args.evidence_output,
            label="candidate evidence output",
        )
        if output_path == evidence_path:
            raise CleanCandidateError("archive and evidence outputs must differ")
        if output_path.exists() or output_path.is_symlink():
            raise CleanCandidateError(
                f"refusing to overwrite existing output: {output_path}"
            )
        if evidence_path.exists() or evidence_path.is_symlink():
            raise CleanCandidateError(
                f"refusing to overwrite existing output: {evidence_path}"
            )
        inventory = candidate_inventory(args.selection_manifest)
        first = deterministic_gzip(
            tar_payload(inventory.paths, payloads=inventory.file_payloads)
        )
        second = deterministic_gzip(
            tar_payload(inventory.paths, payloads=inventory.file_payloads)
        )
        if first != second:
            raise CleanCandidateError("two candidate builds produced different bytes")
        evidence = canonical_json(evidence_document(inventory, first))
        write_exclusive(output_path, first)
        write_exclusive(evidence_path, evidence)
    except Exception as exc:
        print(f"Clean public candidate: BLOCKED\n- {exc}", file=sys.stderr)
        return 1

    print(
        "Clean public candidate: PASS "
        f"({len(inventory.paths)} files; {len(first)} archive bytes; "
        f"sha256:{hashlib.sha256(first).hexdigest()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
