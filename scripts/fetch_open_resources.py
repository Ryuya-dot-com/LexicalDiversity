#!/usr/bin/env python3
"""Fetch and reproducibly build approved open lexical resources.

Currently this script builds the compact Open English WordNet 2025 lemma table.
The verified source archive is temporary and is not copied into the repository.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
import urllib.request
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ldfreq.semantic_network import (  # noqa: E402
    OEWN_LICENSE,
    OEWN_LICENSE_URL,
    OEWN_RELEASE_COMMIT,
    OEWN_RELEASE_PUBLISHED_AT,
    OEWN_RELEASE_TAG,
    OEWN_RELEASE_URL,
    OEWN_SOURCE_NAME,
    OEWN_SOURCE_SHA256,
    OEWN_SOURCE_SIZE,
    OEWN_SOURCE_URL,
    OEWN_VERSION,
    PRODUCTION_ARTIFACT_BYTES,
    PRODUCTION_ARTIFACT_ROWS,
    PRODUCTION_ARTIFACT_SHA256,
    build_oewn_lemma_artifact,
)

DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "data" / "open" / "open_english_wordnet" / OEWN_VERSION
)
ARTIFACT_NAME = "open_english_wordnet_2025_lemma_metrics.csv.gz"


def _download_verified_source(destination: Path) -> None:
    request = urllib.request.Request(
        OEWN_SOURCE_URL,
        headers={"User-Agent": "LexicalDiversity-open-resource-builder/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        declared_size = response.headers.get("Content-Length")
        if declared_size is not None and int(declared_size) != OEWN_SOURCE_SIZE:
            raise ValueError(
                f"Unexpected Content-Length: expected {OEWN_SOURCE_SIZE}, got {declared_size}"
            )
        with destination.open("wb") as output:
            copied = 0
            while chunk := response.read(1024 * 1024):
                copied += len(chunk)
                if copied > OEWN_SOURCE_SIZE:
                    raise ValueError("Download exceeded the pinned source size")
                output.write(chunk)
    if copied != OEWN_SOURCE_SIZE:
        raise ValueError(f"Incomplete download: expected {OEWN_SOURCE_SIZE}, got {copied}")


def _notice_text() -> str:
    return f"""# Open English WordNet attribution notice

This directory contains a derived data table based on **Open English WordNet
{OEWN_VERSION}**, created by the Open English WordNet Community and derived
from Princeton WordNet.

- Source project: https://github.com/globalwordnet/english-wordnet
- Pinned release: {OEWN_RELEASE_URL}
- Source asset: {OEWN_SOURCE_URL}
- License: [{OEWN_LICENSE}]({OEWN_LICENSE_URL})

Changes made by this project: lexical entries were normalized with Unicode NFKC
and case-folding, grouped by lemma and part of speech, and reduced to sense count
(polysemy) plus minimum/mean/maximum hypernym depth. Hypernym depth is the
longest path through `hypernym` and `instance_hypernym` relations to a root,
where root depth is zero. Definitions, examples, sense keys, and the source XML
are not included in the derived table. The CSV is sorted and gzip-compressed
deterministically.

No endorsement by the Open English WordNet Community is implied.
"""


def _manifest(stats: dict[str, int | str]) -> dict[str, object]:
    return {
        "id": "open_english_wordnet_2025_lemma_metrics",
        "name": "Open English WordNet 2025 lemma network metrics",
        "creator": "Open English WordNet Community",
        "version": OEWN_VERSION,
        "release_tag": OEWN_RELEASE_TAG,
        "release_commit": OEWN_RELEASE_COMMIT,
        "release_published_at": OEWN_RELEASE_PUBLISHED_AT,
        "source_project_url": "https://github.com/globalwordnet/english-wordnet",
        "release_url": OEWN_RELEASE_URL,
        "license": OEWN_LICENSE,
        "license_url": OEWN_LICENSE_URL,
        "license_evidence_url": "https://en-word.net/",
        "source_embedded_license": OEWN_LICENSE_URL,
        "redistributable": True,
        "web_service_usable": True,
        "attribution_file": "NOTICE.md",
        "source": {
            "asset": OEWN_SOURCE_NAME,
            "url": OEWN_SOURCE_URL,
            "bytes": OEWN_SOURCE_SIZE,
            "sha256": OEWN_SOURCE_SHA256,
            "bundled": False,
        },
        "artifact": {
            "file": ARTIFACT_NAME,
            "bytes": stats["artifact_bytes"],
            "sha256": stats["artifact_sha256"],
            "format": "deterministic gzip-compressed UTF-8 CSV",
            "rows": stats["lemma_pos_rows"],
        },
        "build": {
            "script": "scripts/fetch_open_resources.py",
            "algorithm_version": 1,
            "synsets": stats["synsets"],
            "source_lexical_entries": stats["source_lexical_entries"],
            "lemma_sense_links": stats["lemma_sense_links"],
            "hypernym_edges": stats["hypernym_edges"],
            "depth_rows": stats["depth_rows"],
            "normalization": "Unicode NFKC, casefold, underscores to spaces, whitespace collapse",
            "depth_definition": (
                "longest hypernym/instance_hypernym path to a root; root depth 0; "
                "noun and verb senses only"
            ),
        },
        "changes": [
            "Removed definitions, examples, sense keys, and all non-hypernym relations.",
            "Grouped distinct synsets by normalized lemma and part of speech.",
            "Calculated polysemy and aggregate hypernym depth.",
        ],
    }


def build(source: Path, output_dir: Path) -> dict[str, object]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats = build_oewn_lemma_artifact(
        source,
        output_dir / ARTIFACT_NAME,
        expected_sha256=OEWN_SOURCE_SHA256,
        expected_size=OEWN_SOURCE_SIZE,
    )
    if (
        stats["artifact_bytes"] != PRODUCTION_ARTIFACT_BYTES
        or stats["artifact_sha256"] != PRODUCTION_ARTIFACT_SHA256
        or stats["lemma_pos_rows"] != PRODUCTION_ARTIFACT_ROWS
    ):
        raise RuntimeError(
            "Official OEWN source did not reproduce the pinned production artifact"
        )
    (output_dir / "NOTICE.md").write_text(_notice_text(), encoding="utf-8")
    manifest = _manifest(stats)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        help="Existing official XML.gz asset; SHA-256 and size are still verified.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Destination directory (default: {DEFAULT_OUTPUT_DIR})",
    )
    args = parser.parse_args()

    if args.source is not None:
        source = args.source.resolve()
        manifest = build(source, args.output_dir.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="oewn-2025-") as temporary:
            source = Path(temporary) / OEWN_SOURCE_NAME
            _download_verified_source(source)
            manifest = build(source, args.output_dir.resolve())

    print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
