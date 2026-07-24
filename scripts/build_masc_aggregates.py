#!/usr/bin/env python3
"""Build MASC 3.0.0 aggregate tables from a user-supplied local ZIP.

This script performs no network access. It never downloads MASC, bypasses TLS
validation, extracts the archive, or copies corpus text into the output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ldfreq.masc import MASC_SOURCE_URL, build_masc_aggregates  # noqa: E402


DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "open" / "masc" / "3.0.0"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Local masc_500k_texts.zip path; URLs are not accepted.",
    )
    parser.add_argument(
        "--expected-source-sha256",
        required=True,
        help=(
            "Pinned SHA-256 obtained through the approved provenance process. "
            "A locally calculated value alone does not prove archive origin."
        ),
    )
    parser.add_argument(
        "--expected-source-bytes",
        type=int,
        help="Optional pinned archive byte size.",
    )
    parser.add_argument(
        "--acquired-on",
        required=True,
        help="Archive acquisition date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--source-url",
        default=MASC_SOURCE_URL,
        help="Provenance URL recorded in the manifest; it is not fetched.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    manifest = build_masc_aggregates(
        args.source.resolve(),
        args.output_dir.resolve(),
        expected_source_sha256=args.expected_source_sha256,
        expected_source_bytes=args.expected_source_bytes,
        acquired_on=args.acquired_on,
        source_url=args.source_url,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
