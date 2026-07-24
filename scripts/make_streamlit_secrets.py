#!/usr/bin/env python3
"""Print Streamlit Secrets TOML entries for server-side word-list data.

The output is intended for Streamlit Community Cloud's Secrets field or for a
local .streamlit/secrets.toml file. Do not commit the generated TOML.
"""
from __future__ import annotations

import argparse
import base64
import io
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _b64_file(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def _b64_zip_dir(path: Path) -> str:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(p for p in path.rglob("*") if p.is_file()):
            zf.write(fp, fp.relative_to(path).as_posix())
    return base64.b64encode(payload.getvalue()).decode("ascii")


def _b64_zip_headword_files(path: Path) -> str:
    payload = io.BytesIO()
    files = sorted(path.glob("headwords*1000.txt"))
    if not files:
        raise ValueError("No BNC/COCA headword TXT files were found")
    with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for fp in files:
            zf.write(fp, fp.name)
    return base64.b64encode(payload.getvalue()).decode("ascii")


def _toml_multiline(key: str, value: str) -> str:
    return f'{key} = """{value}"""'


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate Streamlit Secrets entries for ldfreq word lists."
    )
    parser.add_argument(
        "--only",
        choices=("nj8", "antbnc", "bnc-coca", "nation-family", "range"),
        action="append",
        help="Generate only the selected entry. Can be passed more than once.",
    )
    parser.add_argument(
        "--nj8",
        type=Path,
        default=PROJECT_ROOT / "data" / "NJ8" / "NJ8.csv",
        help="Path to New JACET8000 CSV.",
    )
    parser.add_argument(
        "--antbnc",
        type=Path,
        default=PROJECT_ROOT / "data" / "antbnc" / "antbnc_lemmas_ver_004.txt",
        help="Path to AntBNC lemma list.",
    )
    parser.add_argument(
        "--bnc-coca",
        type=Path,
        default=PROJECT_ROOT / "data" / "bnc_coca",
        help="Directory containing BNC/COCA headword files.",
    )
    parser.add_argument(
        "--nation-family",
        type=Path,
        default=(
            PROJECT_ROOT
            / ".streamlit"
            / "runtime_lists"
            / "nation_bnc_coca_25000"
        ),
        help="Directory containing the official Nation server artifact, manifest, and NOTICE.",
    )
    parser.add_argument(
        "--range",
        type=Path,
        default=PROJECT_ROOT / "data" / "range",
        help="Directory containing AntWordProfiler/Range baseword level-list files.",
    )
    args = parser.parse_args()
    selected = set(args.only or ("nj8", "nation-family"))

    entries: list[tuple[str, str]] = []
    if "nj8" in selected and args.nj8.exists():
        entries.append(("LDFREQ_NJ8_CSV_B64", _b64_file(args.nj8)))
    if "antbnc" in selected and args.antbnc.exists():
        entries.append(("LDFREQ_ANTBNC_TXT_B64", _b64_file(args.antbnc)))
    if "bnc-coca" in selected and args.bnc_coca.exists():
        entries.append(("LDFREQ_BNCCOCA_ZIP_B64", _b64_zip_headword_files(args.bnc_coca)))
    if "nation-family" in selected and args.nation_family.exists():
        entries.append((
            "LDFREQ_NATION_BNCCOCA_RUNTIME_ZIP_B64",
            _b64_zip_dir(args.nation_family),
        ))
    if "range" in selected and args.range.exists():
        entries.append(("LDFREQ_RANGE_ZIP_B64", _b64_zip_dir(args.range)))

    if not entries:
        raise SystemExit(
            "No selected input files found. Pass --nj8, --antbnc, --bnc-coca, "
            "--nation-family, --range, "
            "or adjust --only."
        )

    print("[ldfreq]")
    for key, value in entries:
        print(_toml_multiline(key, value))


if __name__ == "__main__":
    main()
