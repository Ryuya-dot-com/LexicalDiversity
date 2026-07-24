#!/usr/bin/env python3
"""Build a server-only Nation BNC/COCA 25,000 word-family index.

The source must be an existing local copy of the pinned official ZIP. This
command performs no network access and exposes no checksum override.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ldfreq.nation_bnc_coca import build_nation_bnc_coca_index  # noqa: E402


DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / ".streamlit" / "runtime_lists" / "nation_bnc_coca_25000"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Local BNC_COCA_25000.zip path; URLs are not accepted.",
    )
    parser.add_argument(
        "--acquired-on",
        required=True,
        help="Archive acquisition date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Server-private destination (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    manifest = build_nation_bnc_coca_index(
        args.source.resolve(),
        args.output_dir.resolve(),
        acquired_on=args.acquired_on,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
