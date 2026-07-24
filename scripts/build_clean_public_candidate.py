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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_release_archive import deterministic_gzip, write_exclusive
from scripts.check_public_release import release_violations


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
    "scripts/check_git_history.py",
    "scripts/check_public_release.py",
    "scripts/build_candidate_image_evidence.py",
    "scripts/check_staging_coherence.py",
    "tests/fixtures/v1_golden/manifest.json",
}


class CleanCandidateError(ValueError):
    """Raised when a clean-history source candidate is unsafe or incomplete."""


@dataclass(frozen=True)
class CandidateInventory:
    paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    absent_index_paths: tuple[str, ...]


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


def candidate_inventory() -> CandidateInventory:
    """Select existing, non-ignored worktree files without copying Git metadata."""

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
    paths: list[str] = []
    absent: list[str] = []
    for raw_path in listed:
        pure = PurePosixPath(raw_path)
        if pure.is_absolute() or ".." in pure.parts or not pure.parts:
            raise CleanCandidateError(f"unsafe candidate path: {raw_path}")
        if raw_path in ignored:
            continue
        path = PROJECT_ROOT / raw_path
        if path.is_symlink():
            raise CleanCandidateError(f"candidate symlink is prohibited: {raw_path}")
        if not path.exists():
            absent.append(raw_path)
            continue
        if not path.is_file():
            raise CleanCandidateError(f"candidate path is not a file: {raw_path}")
        paths.append(raw_path)

    missing = sorted(REQUIRED_PATHS - set(paths))
    if missing:
        raise CleanCandidateError(
            "candidate lacks required paths: " + ", ".join(missing)
        )
    registry = json.loads(
        (PROJECT_ROOT / "data" / "resource_registry.json").read_text(
            encoding="utf-8"
        )
    )
    violations = release_violations(registry, paths)
    if violations:
        raise CleanCandidateError("; ".join(violations))
    return CandidateInventory(tuple(paths), tuple(sorted(ignored)), tuple(absent))


def file_identity(path: Path) -> dict[str, Any]:
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def tar_payload(paths: Iterable[str], *, root: Path = PROJECT_ROOT) -> bytes:
    """Create a canonical uncompressed tar stream for a candidate inventory."""

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:", format=tarfile.PAX_FORMAT) as output:
        for relative in sorted(paths):
            payload = (root / relative).read_bytes()
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
    files = {
        path: {
            **file_identity(PROJECT_ROOT / path),
            "mode": "755" if path in EXECUTABLE_PATHS else "644",
        }
        for path in inventory.paths
    }
    inventory_payload = json.dumps(
        files,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        "candidate_schema_version": 1,
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
    args = parser.parse_args(argv)

    try:
        if args.output == args.evidence_output:
            raise CleanCandidateError("archive and evidence outputs must differ")
        if args.output.exists() or args.output.is_symlink():
            raise CleanCandidateError(
                f"refusing to overwrite existing output: {args.output}"
            )
        if args.evidence_output.exists() or args.evidence_output.is_symlink():
            raise CleanCandidateError(
                f"refusing to overwrite existing output: {args.evidence_output}"
            )
        inventory = candidate_inventory()
        first = deterministic_gzip(tar_payload(inventory.paths))
        second = deterministic_gzip(tar_payload(inventory.paths))
        if first != second:
            raise CleanCandidateError("two candidate builds produced different bytes")
        evidence = canonical_json(evidence_document(inventory, first))
        write_exclusive(args.output, first)
        write_exclusive(args.evidence_output, evidence)
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
