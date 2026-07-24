#!/usr/bin/env python3
"""Fetch and reproducibly build the approved TUBELEX-EN Treebank index.

Only the already-published aggregate frequency table is downloaded.  The
underlying YouTube subtitles are never requested or copied.  A local source can
be supplied for offline/repeated builds; its fixed size and SHA-256 are still
verified before any output is created.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ldfreq import tubelex as TUBELEX  # noqa: E402


DEFAULT_OUTPUT_DIR = TUBELEX.DEFAULT_ARTIFACT_PATH.parent
PINNED_ACQUISITION_DATE = "2026-07-22"


def _download_verified_source(destination: Path) -> None:
    request = urllib.request.Request(
        TUBELEX.TUBELEX_EN_SOURCE_URL,
        headers={"User-Agent": "LexicalDiversity-open-resource-builder/1.0"},
    )
    copied = 0
    with urllib.request.urlopen(request, timeout=60) as response:
        declared_size = response.headers.get("Content-Length")
        if (
            declared_size is not None
            and int(declared_size) != TUBELEX.TUBELEX_EN_SOURCE_BYTES
        ):
            raise ValueError(
                "Unexpected Content-Length for the pinned TUBELEX source"
            )
        with destination.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                copied += len(chunk)
                if copied > TUBELEX.TUBELEX_EN_SOURCE_BYTES:
                    raise ValueError("TUBELEX download exceeded its pinned size")
                output.write(chunk)
    if copied != TUBELEX.TUBELEX_EN_SOURCE_BYTES:
        raise ValueError(
            "Incomplete TUBELEX download: "
            f"expected {TUBELEX.TUBELEX_EN_SOURCE_BYTES}, got {copied}"
        )


_PROMOTION_ORDER = (TUBELEX.ARTIFACT_NAME, "NOTICE.md", "manifest.json")


def _validate_production_manifest(manifest: dict[str, object]) -> None:
    """Fail before promotion when a build differs from the reviewed artifact."""

    artifact = manifest["artifact"]
    lookup_filter = manifest["build"]["lookup_filter"]
    if (
        artifact["file"] != TUBELEX.ARTIFACT_NAME
        or artifact["bytes"] != TUBELEX.PRODUCTION_ARTIFACT_BYTES
        or artifact["sha256"] != TUBELEX.PRODUCTION_ARTIFACT_SHA256
        or artifact["rows"] != TUBELEX.PRODUCTION_ARTIFACT_ROWS
        or lookup_filter["source_vocabulary_size"]
        != TUBELEX.TUBELEX_EN_SOURCE_VOCABULARY_SIZE
        or lookup_filter["retained_token_mass"]
        != TUBELEX.TUBELEX_EN_RETAINED_TOKEN_MASS
    ):
        raise RuntimeError(
            "The pinned TUBELEX source did not reproduce the production artifact"
        )


def _promote_verified_build(staging: Path, output_dir: Path) -> None:
    """Replace known outputs only after all staged files have been verified.

    Each replacement is atomic on one filesystem, the manifest is committed
    last, and existing files are rolled back if any promotion step fails.
    Unknown files in an existing output directory are left untouched.
    """

    if output_dir.is_symlink() or (output_dir.exists() and not output_dir.is_dir()):
        raise RuntimeError("TUBELEX output must be a real directory")
    for name in _PROMOTION_ORDER:
        source = staging / name
        if source.is_symlink() or not source.is_file():
            raise RuntimeError("Verified TUBELEX build is incomplete")

    output_dir.mkdir(parents=True, exist_ok=True)
    backup = staging / ".previous"
    backup.mkdir()
    promoted: list[str] = []
    try:
        for name in _PROMOTION_ORDER:
            source = staging / name
            destination = output_dir / name
            previous = backup / name
            if destination.is_symlink() or (
                destination.exists() and not destination.is_file()
            ):
                raise RuntimeError("TUBELEX output contains a non-file target")
            if destination.exists():
                os.replace(destination, previous)
            try:
                os.replace(source, destination)
            except BaseException:
                if previous.exists():
                    os.replace(previous, destination)
                raise
            promoted.append(name)
    except BaseException:
        for name in reversed(promoted):
            source = staging / name
            destination = output_dir / name
            previous = backup / name
            if destination.exists():
                os.replace(destination, source)
            if previous.exists():
                os.replace(previous, destination)
        raise


def build(source: Path, output_dir: Path) -> dict[str, object]:
    """Build in a sibling staging directory, verify, then promote."""

    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}.verified-",
        dir=output_dir.parent,
    ) as temporary:
        staging = Path(temporary)
        manifest = TUBELEX.build_tubelex_aggregates(
            source,
            staging,
            expected_source_sha256=TUBELEX.TUBELEX_EN_SOURCE_SHA256,
            expected_source_bytes=TUBELEX.TUBELEX_EN_SOURCE_BYTES,
            acquired_on=PINNED_ACQUISITION_DATE,
        )
        _validate_production_manifest(manifest)
        _promote_verified_build(staging, output_dir)

    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Existing official TSV.xz; fixed size and SHA-256 remain mandatory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if args.source is not None:
        manifest = build(args.source.resolve(), args.output_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="tubelex-en-treebank-") as temporary:
            source = Path(temporary) / TUBELEX.TUBELEX_EN_SOURCE_ASSET
            _download_verified_source(source)
            manifest = build(source, args.output_dir.resolve())

    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
