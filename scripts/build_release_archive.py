#!/usr/bin/env python3
"""Build a deterministic public source archive from an immutable release tag."""
from __future__ import annotations

import argparse
import binascii
import hashlib
import io
import json
import struct
import subprocess
import sys
import tarfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import check_version_contract as versions
from scripts.check_public_release import release_violations


REQUIRED_ARCHIVE_FILES = {
    "CHANGELOG.md",
    "LICENSE",
    "README.md",
    "ldfreq/release.json",
    "requirements-ci-linux-x86_64.lock",
    "deploy/cloud-run/base-image.json",
    "deploy/cloud-run/requirements-prod-linux-x86_64.lock",
    "deploy/cloud-run/requirements-watchdog-pure-linux-x86_64.lock",
    "scripts/check_pure_watchdog_wheel.py",
    "docs/v1-metric-scope.json",
    "tests/fixtures/v1_golden/manifest.json",
}


class ReleaseArchiveError(ValueError):
    """Raised when a source archive would be ambiguous, unsafe, or incomplete."""


def deterministic_gzip(payload: bytes) -> bytes:
    """Return a gzip stream with fixed header, raw DEFLATE, CRC, and size."""

    compressor = zlib.compressobj(
        level=9,
        method=zlib.DEFLATED,
        wbits=-zlib.MAX_WBITS,
        memLevel=9,
        strategy=zlib.Z_DEFAULT_STRATEGY,
    )
    body = compressor.compress(payload) + compressor.flush(zlib.Z_FINISH)
    # ID1/ID2, DEFLATE, no flags, MTIME=0, maximum-compression flag, OS=unknown.
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    trailer = struct.pack(
        "<II",
        binascii.crc32(payload) & 0xFFFFFFFF,
        len(payload) & 0xFFFFFFFF,
    )
    return header + body + trailer


def write_exclusive(path: Path, payload: bytes) -> None:
    """Create one immutable candidate artifact without an overwrite race."""

    try:
        with path.open("xb") as output:
            output.write(payload)
    except FileExistsError as exc:
        raise ReleaseArchiveError(
            f"refusing to overwrite existing output: {path}"
        ) from exc


def _git_bytes(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _git_text(*arguments: str) -> str:
    return _git_bytes(*arguments).decode("utf-8", errors="strict").strip()


def archived_paths(tar_payload: bytes, *, prefix: str) -> list[str]:
    """Validate archive members and return regular-file paths without prefix."""

    paths: list[str] = []
    with tarfile.open(fileobj=io.BytesIO(tar_payload), mode="r:") as archive:
        for member in archive.getmembers():
            pure = PurePosixPath(member.name)
            if pure.is_absolute() or ".." in pure.parts:
                raise ReleaseArchiveError(f"unsafe archive path: {member.name}")
            if not member.name.startswith(prefix):
                raise ReleaseArchiveError(f"archive member is outside prefix: {member.name}")
            relative = member.name[len(prefix) :].rstrip("/")
            if not relative:
                continue
            if member.issym() or member.islnk():
                raise ReleaseArchiveError(f"archive links are prohibited: {relative}")
            if member.isfile():
                paths.append(relative)
            elif not member.isdir():
                raise ReleaseArchiveError(f"unsupported archive member: {relative}")
    if len(paths) != len(set(paths)):
        raise ReleaseArchiveError("archive contains duplicate file paths")
    return sorted(paths)


def build_archive(identity: dict[str, Any]) -> bytes:
    version = str(identity["application_version"])
    tag = f"v{version}"
    prefix = f"LexicalDiversity-{version}/"
    tar_payload = _git_bytes(
        "archive",
        "--format=tar",
        f"--prefix={prefix}",
        tag,
    )
    actual = archived_paths(tar_payload, prefix=prefix)
    expected = sorted(
        line
        for line in _git_text("ls-tree", "-r", "--name-only", tag).splitlines()
        if line
    )
    if actual != expected:
        raise ReleaseArchiveError("git archive inventory differs from the tagged tree")
    missing = sorted(REQUIRED_ARCHIVE_FILES - set(actual))
    if missing:
        raise ReleaseArchiveError(
            "release archive lacks required files: " + ", ".join(missing)
        )

    registry = json.loads(
        (PROJECT_ROOT / "data" / "resource_registry.json").read_text(encoding="utf-8")
    )
    public_violations = release_violations(registry, actual)
    if public_violations:
        raise ReleaseArchiveError("; ".join(public_violations))
    return deterministic_gzip(tar_payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        violations, identity = versions.development_violations()
        violations.extend(versions.release_git_violations(identity))
        if violations:
            raise ReleaseArchiveError("; ".join(sorted(set(violations))))
        first = build_archive(identity)
        second = build_archive(identity)
        if first != second:
            raise ReleaseArchiveError("two archive builds did not produce identical bytes")
        write_exclusive(args.output, first)
    except Exception as exc:
        print(f"Release archive: BLOCKED\n- {exc}", file=sys.stderr)
        return 1

    print(
        "Release archive: PASS "
        f"({len(first)} bytes; sha256:{hashlib.sha256(first).hexdigest()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
