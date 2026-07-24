"""Deterministic server-side index for Paul Nation's BNC/COCA lists.

The builder consumes the pinned official ``BNC_COCA_25000.zip`` from a local
path.  It does not perform network access or extract the archive.  Only
``basewrd1.txt`` through ``basewrd25.txt`` are parsed; the Range executable,
``range.txt``, and later ``basewrd`` lists are deliberately excluded.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterable


SOURCE_ASSET = "BNC_COCA_25000.zip"
SOURCE_URL = (
    "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/"
    "vocabulary-analysis-programs/range/BNC_COCA_25000.zip"
)
SOURCE_BYTES = 600_930
SOURCE_SHA256 = "ac81c7a60e5c76cd2bbf0c59b0501808f0d4fa026b2936919dd54329a9bb6a69"
SOURCE_IDENTITY_VERIFIED_ON = "2026-07-22"
PROJECT_URL = (
    "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/"
    "vocabulary-analysis-programs"
)
LICENSE = "Creative Commons Attribution-ShareAlike 4.0 International"
LICENSE_SPDX = "CC-BY-SA-4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"

ARTIFACT_NAME = "nation_bnc_coca_25000_family_index.csv.gz"
ARTIFACT_COLUMNS = ("form", "head", "rank", "band", "family_ordinal")
SELECTED_MEMBER_NAMES = tuple(f"basewrd{band}.txt" for band in range(1, 26))

# The production artifact is deterministic. Pinning its identity independently
# of the adjacent manifest prevents an attacker from replacing both files while
# preserving plausible official-source metadata.
PRODUCTION_ARTIFACT_BYTES = 471_046
PRODUCTION_ARTIFACT_SHA256 = (
    "20c1c5a2bcf832831c9ac09a395f584a1b1cc5106b094f0cf6f5ca84b5baf081"
)
PRODUCTION_ARTIFACT_ROWS = 75_679
PRODUCTION_FAMILIES = 25_000
PRODUCTION_BANDS = 25
PRODUCTION_FAMILIES_PER_BAND = 1_000
MAX_RUNTIME_ROWS = 100_000
MAX_RUNTIME_FIELD_CHARS = 256

DEFAULT_MAX_ARCHIVE_MEMBERS = 100
DEFAULT_MAX_MEMBER_BYTES = 2 * 1024 * 1024
DEFAULT_MAX_SELECTED_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 300.0

_SELECTED_RE = re.compile(r"basewrd([1-9]|1[0-9]|2[0-5])\.txt", re.IGNORECASE)
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_TRAILING_ZERO_RE = re.compile(r"\s+0\s*$")


@dataclass(frozen=True, slots=True)
class FamilyIndexRecord:
    """One normalized surface form and its first-listed word family."""

    form: str
    head: str
    rank: int
    band: int
    family_ordinal: int


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def nation_notice_text() -> str:
    """Return the attribution and modification notice for the derived index."""

    return f"""# Paul Nation BNC/COCA 25,000 attribution notice

The derived lookup table produced with this notice is based on **Paul Nation's
BNC/COCA 25,000 word-family lists**, distributed by Te Herenga Waka—Victoria
University of Wellington.

- Creator: Paul Nation
- Project page: {PROJECT_URL}
- Official source archive: {SOURCE_URL}
- License: [{LICENSE}]({LICENSE_URL})

Changes made by this project: only `basewrd1.txt` through `basewrd25.txt` were
read. An unindented line starts a family and a tab-prefixed line is treated as a
member of that family. The Range suffix ` 0` was removed and forms were changed
to lower case. Each form was reduced to its family head, 1,000-family band, and
within-band ordinal, then sorted and gzip-compressed deterministically. When a
form occurs more than once, its earliest occurrence in band/file order is kept.

The source ZIP, Range executable, `range.txt`, `basewrd26.txt` through
`basewrd34.txt`, and other archive members are not included in the derived
table. The application uses the table for server-side aggregate analysis and
does not offer it as a client download. That project delivery restriction does
not narrow the rights granted by the CC BY-SA 4.0 license.

The derived lookup table is distributed under CC BY-SA 4.0. The repository's
software license does not replace this data license. No endorsement by Paul
Nation or Victoria University of Wellington is implied.
"""


def _validated_sha256(value: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("Expected source SHA-256 must be exactly 64 hexadecimal characters")
    return normalized


def _validated_acquisition_date(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        parsed = date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("Acquisition date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError("Acquisition date must use canonical YYYY-MM-DD format")
    return value


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise ValueError("ZIP contains an unsafe member path")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("ZIP contains a non-canonical member path")
    return name


def _selected_members(
    archive: zipfile.ZipFile,
    *,
    max_archive_members: int,
    max_member_bytes: int,
    max_selected_bytes: int,
    max_compression_ratio: float,
) -> tuple[list[tuple[int, zipfile.ZipInfo]], int, int]:
    infos = archive.infolist()
    if len(infos) > max_archive_members:
        raise ValueError(
            f"ZIP member limit exceeded: {len(infos)} > {max_archive_members}"
        )

    selected: dict[int, zipfile.ZipInfo] = {}
    ignored_members = 0
    selected_bytes = 0
    seen_paths: set[str] = set()

    for info in infos:
        name = _safe_member_name(info)
        if info.is_dir():
            continue
        folded_path = name.casefold()
        if folded_path in seen_paths:
            raise ValueError("ZIP contains duplicate member paths")
        seen_paths.add(folded_path)
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted ZIP members are not supported")

        match = _SELECTED_RE.fullmatch(PurePosixPath(name).name)
        if match is None:
            ignored_members += 1
            continue

        band = int(match.group(1))
        if band in selected:
            raise ValueError(f"ZIP contains duplicate basewrd list for band {band}")
        if info.file_size > max_member_bytes:
            raise ValueError(
                f"ZIP selected-member size limit exceeded: {info.file_size} > {max_member_bytes}"
            )
        if info.file_size and info.compress_size == 0:
            raise ValueError("ZIP selected member has an invalid compressed size")
        if info.compress_size:
            ratio = info.file_size / info.compress_size
            if ratio > max_compression_ratio:
                raise ValueError(
                    "ZIP selected-member compression-ratio limit exceeded: "
                    f"{ratio:.1f} > {max_compression_ratio:.1f}"
                )
        selected_bytes += info.file_size
        if selected_bytes > max_selected_bytes:
            raise ValueError(
                f"ZIP total selected-size limit exceeded: {selected_bytes} > {max_selected_bytes}"
            )
        selected[band] = info

    missing = [band for band in range(1, 26) if band not in selected]
    if missing:
        rendered = ", ".join(str(band) for band in missing)
        raise ValueError(f"ZIP is missing required basewrd bands: {rendered}")
    return sorted(selected.items()), ignored_members, selected_bytes


def _normalized_entry(raw_line: str, *, is_member: bool) -> str:
    value = raw_line[1:] if is_member else raw_line
    value = _TRAILING_ZERO_RE.sub("", value).strip().lower()
    if "\t" in value or "\x00" in value:
        raise ValueError("A basewrd entry contains an unexpected control character")
    return value


def _parse_archive(
    source_zip: Path,
    *,
    max_archive_members: int,
    max_member_bytes: int,
    max_selected_bytes: int,
    max_compression_ratio: float,
) -> tuple[dict[str, FamilyIndexRecord], dict[str, object]]:
    records: dict[str, FamilyIndexRecord] = {}
    family_counts: dict[str, int] = {}
    member_lines = 0
    collisions = 0
    duplicate_rows = 0

    try:
        with zipfile.ZipFile(source_zip) as archive:
            members, ignored_members, selected_bytes = _selected_members(
                archive,
                max_archive_members=max_archive_members,
                max_member_bytes=max_member_bytes,
                max_selected_bytes=max_selected_bytes,
                max_compression_ratio=max_compression_ratio,
            )
            for band, info in members:
                current_head: str | None = None
                family_ordinal = 0
                with archive.open(info, "r") as binary:
                    with io.TextIOWrapper(
                        binary,
                        encoding="utf-8-sig",
                        errors="strict",
                        newline=None,
                    ) as text:
                        for raw_line in text:
                            line = raw_line.rstrip("\r\n")
                            is_member = line.startswith("\t")
                            form = _normalized_entry(line, is_member=is_member)
                            if not form:
                                continue
                            if is_member:
                                if current_head is None:
                                    raise ValueError(
                                        f"basewrd{band}.txt starts a member before a family head"
                                    )
                                member_lines += 1
                            else:
                                current_head = form
                                family_ordinal += 1

                            assert current_head is not None
                            record = FamilyIndexRecord(
                                form=form,
                                head=current_head,
                                rank=(band - 1) * 1000 + family_ordinal,
                                band=band,
                                family_ordinal=family_ordinal,
                            )
                            previous = records.get(form)
                            if previous is not None:
                                if previous == record:
                                    duplicate_rows += 1
                                else:
                                    collisions += 1
                                continue
                            records[form] = record

                if family_ordinal == 0:
                    raise ValueError(f"basewrd{band}.txt contains no family heads")
                family_counts[str(band)] = family_ordinal
    except zipfile.BadZipFile as exc:
        raise ValueError("Source is not a valid ZIP archive") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("A selected basewrd member is not valid UTF-8") from exc

    family_total = sum(family_counts.values())
    return records, {
        "selected_members": len(SELECTED_MEMBER_NAMES),
        "ignored_members": ignored_members,
        "selected_uncompressed_bytes": selected_bytes,
        "families": family_total,
        "family_counts_by_band": family_counts,
        "forms": len(records),
        "accepted_member_lines": member_lines,
        "collisions_first_occurrence_kept": collisions,
        "duplicate_rows_ignored": duplicate_rows,
    }


def _atomic_write_bytes(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
        destination.chmod(0o640)
    finally:
        temporary.unlink(missing_ok=True)


def _write_artifact(
    destination: Path,
    records: Iterable[FamilyIndexRecord],
) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    row_count = 0
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                mode="wb",
                compresslevel=9,
                fileobj=raw,
                mtime=0,
            ) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                    writer = csv.writer(text, lineterminator="\n")
                    writer.writerow(ARTIFACT_COLUMNS)
                    for record in records:
                        writer.writerow(
                            (
                                record.form,
                                record.head,
                                record.rank,
                                record.band,
                                record.family_ordinal,
                            )
                        )
                        row_count += 1
        os.replace(temporary, destination)
        destination.chmod(0o640)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "file": destination.name,
        "format": "deterministic gzip-compressed UTF-8 CSV",
        "columns": list(ARTIFACT_COLUMNS),
        "rows": row_count,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def make_nation_manifest(
    *,
    source_bytes: int,
    source_sha256: str,
    expected_source_bytes: int,
    expected_source_sha256: str,
    acquired_on: str | None,
    build_stats: dict[str, object],
    artifact: dict[str, object],
) -> dict[str, object]:
    """Create the server-only artifact manifest without a local source path."""

    official_match = source_bytes == SOURCE_BYTES and source_sha256 == SOURCE_SHA256
    return {
        "id": "nation-bnc-coca-25000-server-family-index",
        "name": "Paul Nation BNC/COCA 25,000 server word-family index",
        "creator": "Paul Nation",
        "source_project_url": PROJECT_URL,
        "license": LICENSE,
        "license_spdx": LICENSE_SPDX,
        "license_url": LICENSE_URL,
        "redistributable_under_license": True,
        "web_service_usable": True,
        "server_only": True,
        "client_download": False,
        "attribution_file": "NOTICE.md",
        "source": {
            "asset": SOURCE_ASSET,
            "url": SOURCE_URL,
            "acquired_on": acquired_on,
            "identity_verified_on": SOURCE_IDENTITY_VERIFIED_ON,
            "bytes": source_bytes,
            "sha256": source_sha256,
            "expected_bytes": expected_source_bytes,
            "expected_sha256": expected_source_sha256,
            "checksum_check": "matched-pinned-size-and-sha256",
            "matches_official_pinned_asset": official_match,
            "bundled": False,
            "retrieved_by_builder": False,
        },
        "artifact": artifact,
        "build": {
            "script": "scripts/build_nation_bnc_coca_index.py",
            "algorithm_version": 1,
            **build_stats,
            "selected_archive_members": list(SELECTED_MEMBER_NAMES),
            "archive_selection": (
                "basewrd1.txt through basewrd25.txt by exact case-insensitive basename"
            ),
            "explicit_exclusions": [
                "Range executable files",
                "range.txt",
                "basewrd26.txt through basewrd34.txt",
                "all other ZIP members",
            ],
            "line_grammar": (
                "non-tab line starts a family; tab-prefixed line is a member; "
                "trailing ' 0' removed; lower-cased"
            ),
            "collision_policy": "first occurrence in band and source-line order wins",
            "artifact_row_order": "ascending Unicode code-point order by form",
            "gzip_mtime": 0,
        },
        "changes": [
            "Selected only the first 25 basewrd frequency-band lists.",
            "Mapped family members to their head, band, and within-band ordinal.",
            "Removed the Range trailing-zero marker and changed forms to lower case.",
            "Excluded the source archive, executables, documentation, and bands 26-34.",
        ],
        "delivery_policy": {
            "storage": "operator-controlled server filesystem outside static assets",
            "runtime_output": "aggregate measures and resource/version metadata only",
            "list_rows_or_token_mappings_returned": False,
            "download_endpoint": False,
        },
    }


def build_nation_bnc_coca_index(
    source_zip: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    expected_source_sha256: str = SOURCE_SHA256,
    expected_source_bytes: int = SOURCE_BYTES,
    acquired_on: str | None = None,
    max_archive_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_selected_bytes: int = DEFAULT_MAX_SELECTED_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> dict[str, object]:
    """Build the deterministic lookup from a verified, local source ZIP.

    The defaults accept exactly the official asset pinned by this project. The
    expected identity parameters exist so tests can use small synthetic ZIPs;
    the production command-line wrapper does not expose overrides.
    """

    source = Path(source_zip)
    if not source.is_file():
        raise FileNotFoundError(f"Nation BNC/COCA source ZIP does not exist: {source}")
    expected_digest = _validated_sha256(expected_source_sha256)
    acquisition_date = _validated_acquisition_date(acquired_on)
    if expected_source_bytes <= 0:
        raise ValueError("Expected source size must be positive")
    for label, value in (
        ("max_archive_members", max_archive_members),
        ("max_member_bytes", max_member_bytes),
        ("max_selected_bytes", max_selected_bytes),
    ):
        if value <= 0:
            raise ValueError(f"{label} must be positive")
    if max_compression_ratio <= 0:
        raise ValueError("max_compression_ratio must be positive")

    source_bytes = source.stat().st_size
    source_digest = sha256_file(source)
    if source_bytes != expected_source_bytes:
        raise ValueError(
            f"Source size mismatch: expected {expected_source_bytes}, got {source_bytes}"
        )
    if source_digest != expected_digest:
        raise ValueError(
            f"Source SHA-256 mismatch: expected {expected_digest}, got {source_digest}"
        )

    records, build_stats = _parse_archive(
        source,
        max_archive_members=max_archive_members,
        max_member_bytes=max_member_bytes,
        max_selected_bytes=max_selected_bytes,
        max_compression_ratio=max_compression_ratio,
    )

    output = Path(output_dir)
    artifact = _write_artifact(
        output / ARTIFACT_NAME,
        (records[form] for form in sorted(records)),
    )
    if source_bytes == SOURCE_BYTES and source_digest == SOURCE_SHA256 and (
        artifact.get("bytes") != PRODUCTION_ARTIFACT_BYTES
        or artifact.get("sha256") != PRODUCTION_ARTIFACT_SHA256
        or artifact.get("rows") != PRODUCTION_ARTIFACT_ROWS
        or build_stats.get("families") != PRODUCTION_FAMILIES
    ):
        raise RuntimeError(
            "Official Nation source did not reproduce the pinned production artifact"
        )
    manifest = make_nation_manifest(
        source_bytes=source_bytes,
        source_sha256=source_digest,
        expected_source_bytes=expected_source_bytes,
        expected_source_sha256=expected_digest,
        acquired_on=acquisition_date,
        build_stats=build_stats,
        artifact=artifact,
    )
    _atomic_write_bytes(output / "NOTICE.md", nation_notice_text().encode("utf-8"))
    _atomic_write_bytes(
        output / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return manifest


def load_nation_bnc_coca_index(
    artifact_path: str | os.PathLike[str],
    *,
    _allow_unpinned_source_for_tests: bool = False,
) -> tuple[dict[str, dict[str, int | str]], dict[str, object]]:
    """Verify and load a server artifact into the Panel-B rank-map shape.

    ``manifest.json`` must be adjacent to the artifact. The artifact byte size
    and SHA-256 are always checked before decompression. By default, the
    manifest must also identify the exact official source ZIP pinned by this
    module. ``_allow_unpinned_source_for_tests`` exists only for synthetic test
    fixtures and must not be enabled by a deployment.
    """

    path = Path(artifact_path)
    manifest_path = path.parent / "manifest.json"
    if not path.is_file():
        raise FileNotFoundError(f"Nation family-index artifact does not exist: {path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Nation family-index manifest does not exist beside the artifact: {manifest_path}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Nation family-index manifest must be a JSON object")
    if manifest.get("id") != "nation-bnc-coca-25000-server-family-index":
        raise ValueError("Unexpected Nation family-index manifest ID")
    if manifest.get("license_spdx") != LICENSE_SPDX:
        raise ValueError("Unexpected Nation family-index data license")
    if manifest.get("server_only") is not True or manifest.get("client_download") is not False:
        raise ValueError("Nation family-index manifest does not enforce server-only delivery")

    source_meta = manifest.get("source")
    if not isinstance(source_meta, dict):
        raise ValueError("Nation family-index manifest is missing source metadata")
    official_source_match = (
        source_meta.get("asset") == SOURCE_ASSET
        and source_meta.get("url") == SOURCE_URL
        and source_meta.get("bytes") == SOURCE_BYTES
        and source_meta.get("sha256") == SOURCE_SHA256
        and source_meta.get("expected_bytes") == SOURCE_BYTES
        and source_meta.get("expected_sha256") == SOURCE_SHA256
        and source_meta.get("matches_official_pinned_asset") is True
        and source_meta.get("checksum_check") == "matched-pinned-size-and-sha256"
    )
    if not official_source_match and not _allow_unpinned_source_for_tests:
        raise ValueError(
            "Nation family-index manifest does not match the pinned official source"
        )

    artifact_meta = manifest.get("artifact")
    if not isinstance(artifact_meta, dict):
        raise ValueError("Nation family-index manifest is missing artifact metadata")
    recorded_file = artifact_meta.get("file")
    if not isinstance(recorded_file, str) or Path(recorded_file).name != recorded_file:
        raise ValueError("Nation family-index manifest has an unsafe artifact filename")
    if recorded_file != path.name:
        raise ValueError("Nation family-index artifact filename does not match its manifest")
    recorded_bytes = artifact_meta.get("bytes")
    if not isinstance(recorded_bytes, int) or recorded_bytes < 0:
        raise ValueError("Nation family-index manifest has an invalid artifact byte size")
    recorded_sha256 = str(artifact_meta.get("sha256", "")).lower()
    if not _SHA256_RE.fullmatch(recorded_sha256):
        raise ValueError("Nation family-index manifest has an invalid artifact SHA-256")
    if official_source_match and (
        recorded_bytes != PRODUCTION_ARTIFACT_BYTES
        or recorded_sha256 != PRODUCTION_ARTIFACT_SHA256
        or artifact_meta.get("rows") != PRODUCTION_ARTIFACT_ROWS
    ):
        raise ValueError(
            "Nation family-index manifest does not match the pinned production artifact"
        )
    sha256_mismatch = sha256_file(path) != recorded_sha256
    size_mismatch = path.stat().st_size != recorded_bytes
    if sha256_mismatch and size_mismatch:
        raise ValueError(
            "Nation family-index artifact SHA-256 mismatch; "
            "size does not match its manifest"
        )
    if sha256_mismatch:
        raise ValueError("Nation family-index artifact SHA-256 mismatch")
    if size_mismatch:
        raise ValueError("Nation family-index artifact size does not match its manifest")

    rank_map: dict[str, dict[str, int | str]] = {}
    families: set[tuple[int, int]] = set()
    family_heads: dict[tuple[int, int], str] = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source)
        if tuple(reader.fieldnames or ()) != ARTIFACT_COLUMNS:
            raise ValueError(f"Unexpected Nation family-index columns: {reader.fieldnames!r}")
        for row in reader:
            if len(rank_map) >= MAX_RUNTIME_ROWS:
                raise ValueError("Nation family-index exceeds the runtime row limit")
            form = row["form"]
            head = row["head"]
            if len(form) > MAX_RUNTIME_FIELD_CHARS or len(head) > MAX_RUNTIME_FIELD_CHARS:
                raise ValueError("Nation family-index form or head is too long")
            rank = int(row["rank"])
            band = int(row["band"])
            ordinal = int(row["family_ordinal"])
            if not form or not head or form != form.lower() or head != head.lower():
                raise ValueError("Nation family-index forms must be non-empty and lower-case")
            if not 1 <= band <= PRODUCTION_BANDS or not 1 <= ordinal <= 1_000:
                raise ValueError("Nation family-index band or ordinal is out of range")
            if rank != (band - 1) * 1000 + ordinal:
                raise ValueError("Nation family-index rank is inconsistent with its band")
            if form in rank_map:
                raise ValueError(f"Duplicate form in Nation family index: {form!r}")
            rank_map[form] = {
                "head": head,
                "rank": rank,
                "level": band,
            }
            family_key = (band, ordinal)
            existing_head = family_heads.setdefault(family_key, head)
            if existing_head != head:
                raise ValueError("Nation family-index family has inconsistent heads")
            families.add(family_key)

    recorded_rows = artifact_meta.get("rows")
    if not isinstance(recorded_rows, int) or recorded_rows != len(rank_map):
        raise ValueError("Nation family-index row count does not match its manifest")

    for (band, ordinal), head in family_heads.items():
        head_entry = rank_map.get(head)
        expected_rank = (band - 1) * 1000 + ordinal
        if not head_entry or head_entry.get("rank") != expected_rank:
            raise ValueError("Nation family-index family head is missing or inconsistent")

    if official_source_match:
        counts_by_band = {
            band: sum(1 for family_band, _ in families if family_band == band)
            for band in range(1, PRODUCTION_BANDS + 1)
        }
        if (
            len(rank_map) != PRODUCTION_ARTIFACT_ROWS
            or len(families) != PRODUCTION_FAMILIES
            or any(
                count != PRODUCTION_FAMILIES_PER_BAND
                for count in counts_by_band.values()
            )
        ):
            raise ValueError(
                "Nation family-index production row/family invariants do not match"
            )

    n_levels = max((entry["level"] for entry in rank_map.values()), default=0)
    return rank_map, {
        "entries": len(families),
        "keys": len(rank_map),
        "variants": sum(
            1 for form, entry in rank_map.items() if form != entry["head"]
        ),
        "max_rank": n_levels * 1000,
        "n_levels": n_levels,
        "lookup_unit": "word_family",
        "source_format": "nation_bnc_coca_25000_server_index",
    }


def load_verified_nation_bnc_coca_index(
    artifact_path: str | os.PathLike[str],
    *,
    manifest_path: str | os.PathLike[str] | None = None,
    require_official_source: bool = True,
) -> tuple[dict[str, dict[str, int | str]], dict[str, object]]:
    """Compatibility wrapper around the fail-closed runtime loader."""

    artifact = Path(artifact_path)
    adjacent_manifest = artifact.with_name("manifest.json")
    if manifest_path is not None and Path(manifest_path) != adjacent_manifest:
        raise ValueError("Nation family-index manifest must be adjacent to the artifact")
    return load_nation_bnc_coca_index(
        artifact,
        _allow_unpinned_source_for_tests=not require_official_source,
    )
