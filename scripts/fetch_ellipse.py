#!/usr/bin/env python3
"""Fetch or verify the pinned ELLIPSE benchmark without publishing its text.

The script is deliberately separate from the public application runtime.  It
accepts only an archive whose complete byte length and SHA-256 are recorded in
``benchmarks/ellipse/manifest.json``.  It then checks an exact ZIP inventory,
all member hashes, the encrypted final-test member, and the complete aggregate
CSV contract before it can create a private output directory.

Acquisition and content processing are intentionally separate. ``--download``
only downloads and verifies the opaque outer archive; it never opens a member.
After network access has been disabled, ``--source`` performs the full content
verification. Add ``--provision`` to that offline step to write the two final
CSVs and an aggregate-only verification record under the ignored ``.research``
root. The raw-rater archive is hash-checked as an opaque member but is never
decrypted, extracted, or written.
"""
from __future__ import annotations

import argparse
import csv
import getpass
import hashlib
import io
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import urllib.request
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = PROJECT_ROOT / "benchmarks" / "ellipse" / "manifest.json"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / ".research" / "benchmarks" / "ellipse" / "dc3b8f0b"
)
DEFAULT_DOWNLOAD_PATH = (
    PROJECT_ROOT
    / ".research"
    / "benchmarks"
    / "ellipse"
    / "sources"
    / "ELLIPSE-Corpus-dc3b8f0b.zip"
)
PASSWORD_ENVIRONMENT_VARIABLE = "ELLIPSE_TEST_ZIP_PASSWORD"
DOWNLOAD_USER_AGENT = "LexicalDiversity-ELLIPSE-verifier/1.0"
CHUNK_BYTES = 1024 * 1024
MAX_PASSWORD_FILE_BYTES = 4096
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:")


class EllipseVerificationError(ValueError):
    """A content-safe failure raised by the ELLIPSE verifier."""


@dataclass(frozen=True)
class _CsvAudit:
    public: dict[str, object]
    ids: frozenset[str] = field(repr=False)
    prompts: frozenset[str] = field(repr=False)
    text_hashes: tuple[bytes, ...] = field(repr=False)


@dataclass(frozen=True)
class VerifiedEllipse:
    """Verified payload plus a summary that contains no row-level material."""

    summary: dict[str, object]
    train_csv: bytes = field(repr=False)
    test_csv: bytes = field(repr=False)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, object]:
    """Load the public benchmark contract with path-safe errors."""

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise EllipseVerificationError(
            "The ELLIPSE manifest could not be loaded"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "1.0.0":
        raise EllipseVerificationError("Unsupported ELLIPSE manifest schema")
    return manifest


def _sha256_stream(source: BinaryIO, *, maximum_bytes: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    total = 0
    while chunk := source.read(CHUNK_BYTES):
        total += len(chunk)
        if total > maximum_bytes:
            raise EllipseVerificationError("Payload exceeds its reviewed size limit")
        digest.update(chunk)
    return total, digest.hexdigest()


def _select_source_variant(
    source: Path,
    manifest: Mapping[str, object],
) -> tuple[dict[str, object], int, str]:
    archive = manifest.get("outer_archive")
    if not isinstance(archive, dict):
        raise EllipseVerificationError("Manifest outer-archive contract is invalid")
    variants = archive.get("accepted_variants")
    if not isinstance(variants, list) or not variants:
        raise EllipseVerificationError("Manifest has no accepted archive variant")

    reviewed: list[dict[str, object]] = []
    for item in variants:
        if not isinstance(item, dict):
            raise EllipseVerificationError("Manifest archive variant is invalid")
        try:
            byte_count = int(item["bytes"])
            digest = str(item["sha256"])
            root = str(item["root"])
        except (KeyError, TypeError, ValueError) as exc:
            raise EllipseVerificationError(
                "Manifest archive variant is incomplete"
            ) from exc
        if byte_count <= 0 or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise EllipseVerificationError("Manifest archive identity is invalid")
        if not root.endswith("/"):
            raise EllipseVerificationError("Manifest archive root is invalid")
        reviewed.append(item)

    try:
        file_stat = source.lstat()
    except OSError as exc:
        raise EllipseVerificationError("ELLIPSE source archive is unavailable") from exc
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise EllipseVerificationError("ELLIPSE source must be a regular non-symlink file")

    size_matches = [item for item in reviewed if int(item["bytes"]) == file_stat.st_size]
    if not size_matches:
        raise EllipseVerificationError("ELLIPSE source byte length is not reviewed")
    maximum = max(int(item["bytes"]) for item in reviewed)
    try:
        with source.open("rb") as stream:
            byte_count, digest = _sha256_stream(stream, maximum_bytes=maximum)
    except OSError as exc:
        raise EllipseVerificationError("ELLIPSE source archive could not be read") from exc
    matches = [
        item
        for item in size_matches
        if str(item["sha256"]) == digest
    ]
    if len(matches) != 1:
        raise EllipseVerificationError("ELLIPSE source SHA-256 is not reviewed")
    return matches[0], byte_count, digest


def _safe_zip_name(info: zipfile.ZipInfo) -> str:
    raw_name = info.filename
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise EllipseVerificationError("ZIP contains an unsafe member path")
    path = PurePosixPath(raw_name)
    if (
        path.is_absolute()
        or not path.parts
        or any(part in {"", ".", ".."} for part in path.parts)
        or _WINDOWS_DRIVE_RE.match(path.parts[0])
    ):
        raise EllipseVerificationError("ZIP contains an unsafe member path")
    if path.as_posix() != raw_name.rstrip("/"):
        raise EllipseVerificationError("ZIP contains a non-canonical member path")

    unix_mode = (info.external_attr >> 16) & 0xFFFF
    file_type = stat.S_IFMT(unix_mode)
    if file_type == stat.S_IFLNK:
        raise EllipseVerificationError("ZIP contains a symbolic-link member")
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        raise EllipseVerificationError("ZIP contains a non-regular member")
    return raw_name


def _checked_inventory(
    archive: zipfile.ZipFile,
    *,
    expected_names: set[str],
    maximum_members: int,
    maximum_total_bytes: int,
    maximum_member_bytes: int,
    maximum_compression_ratio: float,
    encryption: str,
) -> dict[str, zipfile.ZipInfo]:
    infos = archive.infolist()
    if len(infos) > maximum_members:
        raise EllipseVerificationError("ZIP member count exceeds the reviewed limit")

    by_name: dict[str, zipfile.ZipInfo] = {}
    casefold_names: set[str] = set()
    total_bytes = 0
    for info in infos:
        name = _safe_zip_name(info)
        if name in by_name or name.casefold() in casefold_names:
            raise EllipseVerificationError("ZIP contains duplicate member names")
        by_name[name] = info
        casefold_names.add(name.casefold())

        if info.is_dir():
            continue
        if encryption == "forbidden" and info.flag_bits & 0x1:
            raise EllipseVerificationError("Outer ZIP contains an encrypted member")
        if encryption == "required" and not info.flag_bits & 0x1:
            raise EllipseVerificationError("Nested test member is not encrypted")
        if info.file_size < 0 or info.file_size > maximum_member_bytes:
            raise EllipseVerificationError("ZIP member size exceeds the reviewed limit")
        if info.file_size and info.compress_size <= 0:
            raise EllipseVerificationError("ZIP member has an invalid compressed size")
        if info.compress_size:
            ratio = info.file_size / info.compress_size
            if ratio > maximum_compression_ratio:
                raise EllipseVerificationError(
                    "ZIP member compression ratio exceeds the reviewed limit"
                )
        total_bytes += info.file_size
        if total_bytes > maximum_total_bytes:
            raise EllipseVerificationError("ZIP contents exceed the reviewed size limit")

    if set(by_name) != expected_names:
        raise EllipseVerificationError("ZIP inventory differs from the reviewed manifest")
    return by_name


def _read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    expected_bytes: int,
    expected_sha256: str,
    capture: bool,
    password: bytes | None = None,
) -> bytes | None:
    if info.file_size != expected_bytes:
        raise EllipseVerificationError("ZIP member byte length differs from the manifest")
    digest = hashlib.sha256()
    total = 0
    chunks: list[bytes] | None = [] if capture else None
    try:
        with archive.open(info, "r", pwd=password) as source:
            while chunk := source.read(CHUNK_BYTES):
                total += len(chunk)
                if total > expected_bytes:
                    raise EllipseVerificationError(
                        "ZIP member exceeds its reviewed byte length"
                    )
                digest.update(chunk)
                if chunks is not None:
                    chunks.append(chunk)
    except EllipseVerificationError:
        raise
    except (RuntimeError, NotImplementedError, OSError, EOFError, zipfile.BadZipFile) as exc:
        raise EllipseVerificationError(
            "ZIP member could not be verified; check the documented test password"
        ) from exc
    if total != expected_bytes or digest.hexdigest() != expected_sha256:
        raise EllipseVerificationError("ZIP member identity differs from the manifest")
    return b"".join(chunks) if chunks is not None else None


def _outer_members(
    source: Path,
    manifest: Mapping[str, object],
    variant: Mapping[str, object],
) -> tuple[bytes, bytes]:
    archive_contract = manifest["outer_archive"]
    members = manifest.get("members")
    if not isinstance(archive_contract, dict) or not isinstance(members, list):
        raise EllipseVerificationError("Manifest member contract is invalid")
    root = str(variant["root"])
    expected_names = {root}
    member_contracts: dict[str, dict[str, object]] = {}
    for item in members:
        if not isinstance(item, dict):
            raise EllipseVerificationError("Manifest member contract is invalid")
        relative = str(item.get("relative_path", ""))
        if not relative or "/" in relative or "\\" in relative:
            raise EllipseVerificationError("Manifest member path is invalid")
        full_name = f"{root}{relative}"
        expected_names.add(full_name)
        member_contracts[full_name] = item

    try:
        with zipfile.ZipFile(source, "r") as archive:
            expected_comment = variant.get("zip_comment")
            if expected_comment is not None:
                try:
                    comment = archive.comment.decode("ascii", errors="strict")
                except UnicodeDecodeError as exc:
                    raise EllipseVerificationError(
                        "Outer ZIP commit comment is invalid"
                    ) from exc
                if comment != expected_comment:
                    raise EllipseVerificationError(
                        "Outer ZIP commit comment differs from the manifest"
                    )
            inventory = _checked_inventory(
                archive,
                expected_names=expected_names,
                maximum_members=int(archive_contract["maximum_members"]),
                maximum_total_bytes=int(
                    archive_contract["maximum_total_uncompressed_bytes"]
                ),
                maximum_member_bytes=int(archive_contract["maximum_member_bytes"]),
                maximum_compression_ratio=float(
                    archive_contract["maximum_compression_ratio"]
                ),
                encryption="forbidden",
            )
            root_info = inventory[root]
            if not root_info.is_dir():
                raise EllipseVerificationError("Outer ZIP root is not a directory")

            train_bytes: bytes | None = None
            test_zip_bytes: bytes | None = None
            for full_name in sorted(member_contracts):
                contract = member_contracts[full_name]
                info = inventory[full_name]
                if info.is_dir():
                    raise EllipseVerificationError("Expected ZIP file is a directory")
                kind = str(contract.get("kind", ""))
                capture = kind in {"final-train-csv", "encrypted-final-test-archive"}
                payload = _read_zip_member(
                    archive,
                    info,
                    expected_bytes=int(contract["bytes"]),
                    expected_sha256=str(contract["sha256"]),
                    capture=capture,
                )
                if kind == "final-train-csv":
                    train_bytes = payload
                elif kind == "encrypted-final-test-archive":
                    test_zip_bytes = payload
    except EllipseVerificationError:
        raise
    except (
        OSError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise EllipseVerificationError("Outer ELLIPSE ZIP could not be verified") from exc

    if train_bytes is None or test_zip_bytes is None:
        raise EllipseVerificationError("Manifest does not identify both final data members")
    return train_bytes, test_zip_bytes


def _nested_test_csv(
    test_zip_bytes: bytes,
    manifest: Mapping[str, object],
    password: bytes,
) -> bytes:
    contract = manifest.get("nested_test_archive")
    if not isinstance(contract, dict) or not isinstance(contract.get("member"), dict):
        raise EllipseVerificationError("Manifest nested-test contract is invalid")
    member_contract = contract["member"]
    member_name = str(member_contract["path"])
    if "/" in member_name or "\\" in member_name:
        raise EllipseVerificationError("Manifest nested member path is invalid")
    encryption = "required" if contract.get("encrypted_member_required") is True else "optional"
    expected_names = {member_name}
    maximum_bytes = int(member_contract["bytes"])
    try:
        with zipfile.ZipFile(io.BytesIO(test_zip_bytes), "r") as archive:
            inventory = _checked_inventory(
                archive,
                expected_names=expected_names,
                maximum_members=1,
                maximum_total_bytes=maximum_bytes,
                maximum_member_bytes=maximum_bytes,
                maximum_compression_ratio=float(contract["maximum_compression_ratio"]),
                encryption=encryption,
            )
            info = inventory[member_name]
            if info.is_dir():
                raise EllipseVerificationError("Nested test member is not a file")
            payload = _read_zip_member(
                archive,
                info,
                expected_bytes=maximum_bytes,
                expected_sha256=str(member_contract["sha256"]),
                capture=True,
                password=password,
            )
    except EllipseVerificationError:
        raise
    except (OSError, zipfile.BadZipFile, KeyError, TypeError, ValueError) as exc:
        raise EllipseVerificationError("Nested ELLIPSE test ZIP could not be verified") from exc
    if payload is None:
        raise EllipseVerificationError("Nested ELLIPSE test payload is unavailable")
    return payload


def _audit_csv(
    payload: bytes,
    *,
    split_name: str,
    contract: Mapping[str, object],
) -> _CsvAudit:
    columns = contract.get("columns_in_order")
    split_contracts = contract.get("splits")
    if not isinstance(columns, list) or not isinstance(split_contracts, dict):
        raise EllipseVerificationError("Manifest CSV contract is invalid")
    if len(columns) != len(set(columns)) or not all(isinstance(item, str) for item in columns):
        raise EllipseVerificationError("Manifest CSV columns are invalid")
    split = split_contracts.get(split_name)
    if not isinstance(split, dict):
        raise EllipseVerificationError("Manifest split contract is invalid")

    try:
        decoded = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError as exc:
        raise EllipseVerificationError("Final CSV is not valid UTF-8") from exc

    positions = {name: index for index, name in enumerate(columns)}
    required_contract_columns = [
        contract.get("id_column"),
        contract.get("text_column"),
        contract.get("prompt_column"),
        (contract.get("task") or {}).get("column"),
        "set",
    ]
    score_columns = contract.get("score_columns")
    if not isinstance(score_columns, list):
        raise EllipseVerificationError("Manifest score-column contract is invalid")
    required_contract_columns.extend(score_columns)
    if any(column not in positions for column in required_contract_columns):
        raise EllipseVerificationError("Manifest references an absent CSV column")

    score_scale = contract.get("score_scale")
    task_contract = contract.get("task")
    if not isinstance(score_scale, dict) or not isinstance(task_contract, dict):
        raise EllipseVerificationError("Manifest score or task contract is invalid")
    try:
        score_minimum = Decimal(str(score_scale["minimum"]))
        score_maximum = Decimal(str(score_scale["maximum"]))
        score_step = Decimal(str(score_scale["step"]))
    except (KeyError, InvalidOperation) as exc:
        raise EllipseVerificationError("Manifest score scale is invalid") from exc
    allowed_tasks = set(task_contract.get("allowed_values", []))
    expected_missing = {
        str(name): int(count)
        for name, count in (split.get("allowed_missing_cells") or {}).items()
    }

    row_count = 0
    identifiers: set[str] = set()
    prompts: set[str] = set()
    text_hashes: list[bytes] = []
    missing: Counter[str] = Counter()
    observed_score_minimum: Decimal | None = None
    observed_score_maximum: Decimal | None = None

    previous_field_limit = csv.field_size_limit()
    try:
        csv.field_size_limit(2 * 1024 * 1024)
        reader = csv.reader(io.StringIO(decoded, newline=""), strict=True)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise EllipseVerificationError("Final CSV is empty") from exc
        if header != columns:
            raise EllipseVerificationError("Final CSV header differs from the manifest")

        for row in reader:
            if len(row) != len(columns):
                raise EllipseVerificationError("Final CSV row width differs from the manifest")
            row_count += 1
            for column, value in zip(columns, row):
                if value == "":
                    missing[column] += 1

            identifier = row[positions[str(contract["id_column"])]]
            full_text = row[positions[str(contract["text_column"])]]
            prompt = row[positions[str(contract["prompt_column"])]]
            if not identifier or not full_text or not prompt:
                raise EllipseVerificationError("Final CSV has a missing required value")
            if identifier in identifiers:
                raise EllipseVerificationError("Final CSV contains a duplicate identifier")
            identifiers.add(identifier)
            prompts.add(prompt)
            text_hashes.append(hashlib.sha256(full_text.encode("utf-8")).digest())

            task_value = row[positions[str(task_contract["column"])]]
            if task_value not in allowed_tasks:
                raise EllipseVerificationError("Final CSV task value differs from the manifest")
            if row[positions["set"]] != str(split["set_value"]):
                raise EllipseVerificationError("Final CSV split label differs from the manifest")

            for score_column in score_columns:
                raw_score = row[positions[score_column]]
                try:
                    score = Decimal(raw_score)
                except InvalidOperation as exc:
                    raise EllipseVerificationError("Final CSV score is not numeric") from exc
                if (
                    not score.is_finite()
                    or score < score_minimum
                    or score > score_maximum
                    or (score - score_minimum) % score_step != 0
                ):
                    raise EllipseVerificationError("Final CSV score is outside its fixed scale")
                observed_score_minimum = (
                    score
                    if observed_score_minimum is None
                    else min(observed_score_minimum, score)
                )
                observed_score_maximum = (
                    score
                    if observed_score_maximum is None
                    else max(observed_score_maximum, score)
                )
    except EllipseVerificationError:
        raise
    except (csv.Error, OverflowError, KeyError, TypeError, ValueError) as exc:
        raise EllipseVerificationError("Final CSV could not be audited") from exc
    finally:
        csv.field_size_limit(previous_field_limit)

    if row_count != int(split["rows"]):
        raise EllipseVerificationError("Final CSV row count differs from the manifest")
    if len(prompts) != int(split["prompts"]):
        raise EllipseVerificationError("Final CSV prompt count differs from the manifest")
    actual_missing = {name: count for name, count in missing.items() if count}
    if actual_missing != expected_missing:
        raise EllipseVerificationError("Final CSV missingness differs from the manifest")
    if observed_score_minimum is None or observed_score_maximum is None:
        raise EllipseVerificationError("Final CSV has no score observations")

    return _CsvAudit(
        public={
            "rows": row_count,
            "columns": len(columns),
            "prompts": len(prompts),
            "missing_cells": dict(sorted(actual_missing.items())),
            "score_minimum": float(observed_score_minimum),
            "score_maximum": float(observed_score_maximum),
        },
        ids=frozenset(identifiers),
        prompts=frozenset(prompts),
        text_hashes=tuple(text_hashes),
    )


def _combined_audit(
    train: _CsvAudit,
    test: _CsvAudit,
    contract: Mapping[str, object],
) -> dict[str, object]:
    combined = contract.get("combined")
    if not isinstance(combined, dict):
        raise EllipseVerificationError("Manifest combined CSV contract is invalid")

    overlap = len(train.ids & test.ids)
    unique_ids = len(train.ids | test.ids)
    all_text_hashes = train.text_hashes + test.text_hashes
    exact_duplicates = len(all_text_hashes) - len(set(all_text_hashes))
    prompt_sets_identical = train.prompts == test.prompts
    total_rows = int(train.public["rows"]) + int(test.public["rows"])
    prompt_count = len(train.prompts | test.prompts)

    observed = {
        "rows": total_rows,
        "columns": int(train.public["columns"]),
        "prompts": prompt_count,
        "unique_ids": unique_ids,
        "id_overlap_between_splits": overlap,
        "exact_text_duplicates": exact_duplicates,
        "prompt_sets_identical_between_splits": prompt_sets_identical,
    }
    for key, value in observed.items():
        if combined.get(key) != value:
            raise EllipseVerificationError(
                "Combined final-data contract differs from the manifest"
            )
    if train.public["columns"] != test.public["columns"]:
        raise EllipseVerificationError("Train and test CSV widths differ")
    return observed


def verify_source(
    source: Path,
    *,
    manifest: Mapping[str, object],
    test_password: bytes,
) -> VerifiedEllipse:
    """Verify one accepted source and return private bytes plus aggregate facts."""

    if not test_password:
        raise EllipseVerificationError("The documented test password is required")
    variant, outer_bytes, outer_sha256 = _select_source_variant(source, manifest)
    train_bytes, test_zip_bytes = _outer_members(source, manifest, variant)
    test_bytes = _nested_test_csv(test_zip_bytes, manifest, test_password)

    csv_contract = manifest.get("final_csv_contract")
    if not isinstance(csv_contract, dict):
        raise EllipseVerificationError("Manifest final CSV contract is invalid")
    train_audit = _audit_csv(
        train_bytes,
        split_name="train",
        contract=csv_contract,
    )
    test_audit = _audit_csv(
        test_bytes,
        split_name="test",
        contract=csv_contract,
    )
    combined = _combined_audit(train_audit, test_audit, csv_contract)

    nested = manifest["nested_test_archive"]
    train_contract = next(
        item for item in manifest["members"] if item["kind"] == "final-train-csv"
    )
    test_contract = nested["member"]
    summary: dict[str, object] = {
        "schema_version": "1.0.0",
        "resource_id": manifest["resource_id"],
        "upstream_commit": manifest["upstream"]["commit"],
        "source_archive": {
            "accepted_variant": variant["id"],
            "bytes": outer_bytes,
            "sha256": outer_sha256,
        },
        "final_artifacts": {
            "train": {
                "bytes": len(train_bytes),
                "sha256": train_contract["sha256"],
            },
            "test": {
                "bytes": len(test_bytes),
                "sha256": test_contract["sha256"],
            },
        },
        "data_contract": {
            "train": train_audit.public,
            "test": test_audit.public,
            "combined": combined,
        },
        "privacy": {
            "contains_row_level_values": False,
            "raw_rater_archive_decrypted": False,
            "external_api_processing": False,
            "public_release_allowed": False,
        },
    }
    return VerifiedEllipse(summary=summary, train_csv=train_bytes, test_csv=test_bytes)


def _private_output_path(output_dir: Path) -> Path:
    try:
        resolved = output_dir.resolve(strict=False)
        project = PROJECT_ROOT.resolve(strict=True)
    except OSError as exc:
        raise EllipseVerificationError("Private output path could not be resolved") from exc
    try:
        relative = resolved.relative_to(project)
    except ValueError:
        return resolved
    if not relative.parts or relative.parts[0] not in {".research", "data"}:
        raise EllipseVerificationError(
            "In-repository ELLIPSE output is allowed only under .research or data/raw"
        )
    if relative.parts[0] == "data" and (
        len(relative.parts) < 2 or relative.parts[1] != "raw"
    ):
        raise EllipseVerificationError(
            "In-repository ELLIPSE output is allowed only under .research or data/raw"
        )
    return resolved


def _write_private_file(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        path.chmod(0o600)
    except OSError as exc:
        raise EllipseVerificationError("Private ELLIPSE output could not be written") from exc


def provision_verified(
    verified: VerifiedEllipse,
    output_dir: Path,
    *,
    manifest: Mapping[str, object],
) -> None:
    """Atomically write only the two final CSVs and aggregate verification."""

    destination = _private_output_path(output_dir)
    if destination.exists() or destination.is_symlink():
        raise EllipseVerificationError("Private ELLIPSE output already exists")
    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise EllipseVerificationError("Private ELLIPSE output parent is invalid")
        staging = Path(tempfile.mkdtemp(prefix=".ellipse-verified-", dir=parent))
        staging.chmod(0o700)
    except EllipseVerificationError:
        raise
    except OSError as exc:
        raise EllipseVerificationError("Private ELLIPSE staging could not be created") from exc

    try:
        train_contract = next(
            item for item in manifest["members"] if item["kind"] == "final-train-csv"
        )
        test_contract = manifest["nested_test_archive"]["member"]
        train_name = str(train_contract["relative_path"])
        test_name = str(test_contract["path"])
        if (
            Path(train_name).name != train_name
            or Path(test_name).name != test_name
            or train_name == test_name
        ):
            raise EllipseVerificationError("Private output filenames are invalid")

        _write_private_file(staging / train_name, verified.train_csv)
        _write_private_file(staging / test_name, verified.test_csv)
        verification_payload = (
            json.dumps(
                verified.summary,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode("utf-8")
        _write_private_file(staging / "verification.json", verification_payload)

        expected = {
            train_name: (
                int(train_contract["bytes"]),
                str(train_contract["sha256"]),
            ),
            test_name: (
                int(test_contract["bytes"]),
                str(test_contract["sha256"]),
            ),
        }
        for name, (expected_bytes, expected_sha256) in expected.items():
            with (staging / name).open("rb") as source:
                actual_bytes, actual_sha256 = _sha256_stream(
                    source,
                    maximum_bytes=expected_bytes,
                )
            if actual_bytes != expected_bytes or actual_sha256 != expected_sha256:
                raise EllipseVerificationError("Private staged output failed verification")
        os.replace(staging, destination)
        destination.chmod(0o700)
    except EllipseVerificationError:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise
    except (OSError, KeyError, StopIteration, TypeError, ValueError) as exc:
        if staging.exists() and not staging.is_symlink():
            shutil.rmtree(staging)
        raise EllipseVerificationError("Private ELLIPSE output could not be promoted") from exc


def _download_pinned_archive(
    destination: Path,
    manifest: Mapping[str, object],
) -> None:
    archive = manifest.get("outer_archive")
    if not isinstance(archive, dict):
        raise EllipseVerificationError("Manifest download contract is invalid")
    url = str(archive.get("pinned_download_url", ""))
    variants = archive.get("accepted_variants")
    if not url.startswith("https://") or not isinstance(variants, list):
        raise EllipseVerificationError("Manifest download URL is invalid")
    pinned = next(
        (item for item in variants if item.get("id") == "github-pinned-commit"),
        None,
    )
    if not isinstance(pinned, dict):
        raise EllipseVerificationError("Pinned download identity is missing")
    expected_bytes = int(pinned["bytes"])
    request = urllib.request.Request(url, headers={"User-Agent": DOWNLOAD_USER_AGENT})
    copied = 0
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) != expected_bytes:
                raise EllipseVerificationError(
                    "Pinned download Content-Length differs from the manifest"
                )
            with destination.open("xb") as output:
                while chunk := response.read(CHUNK_BYTES):
                    copied += len(chunk)
                    if copied > expected_bytes:
                        raise EllipseVerificationError(
                            "Pinned download exceeds the reviewed byte length"
                        )
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
        destination.chmod(0o600)
    except EllipseVerificationError:
        destination.unlink(missing_ok=True)
        raise
    except (OSError, ValueError) as exc:
        destination.unlink(missing_ok=True)
        raise EllipseVerificationError("Pinned ELLIPSE download failed") from exc
    if copied != expected_bytes:
        destination.unlink(missing_ok=True)
        raise EllipseVerificationError("Pinned ELLIPSE download is incomplete")


def download_pinned_archive(
    destination: Path,
    *,
    manifest: Mapping[str, object],
) -> dict[str, object]:
    """Download and hash the opaque outer archive without opening any member."""

    final_path = _private_output_path(destination)
    if final_path.exists() or final_path.is_symlink():
        raise EllipseVerificationError("Pinned ELLIPSE download destination exists")
    parent = final_path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if parent.is_symlink() or not parent.is_dir():
            raise EllipseVerificationError("Pinned download parent is invalid")
    except EllipseVerificationError:
        raise
    except OSError as exc:
        raise EllipseVerificationError("Pinned download staging could not be created") from exc

    try:
        with tempfile.TemporaryDirectory(
            prefix=".ellipse-download-",
            dir=parent,
        ) as temporary:
            staging = Path(temporary)
            staging.chmod(0o700)
            source = staging / "source.zip"
            _download_pinned_archive(source, manifest)
            variant, byte_count, digest = _select_source_variant(source, manifest)
            if variant.get("id") != "github-pinned-commit":
                raise EllipseVerificationError(
                    "Downloaded archive did not match the pinned commit variant"
                )
            os.replace(source, final_path)
            final_path.chmod(0o600)
    except EllipseVerificationError:
        raise
    except OSError as exc:
        raise EllipseVerificationError("Pinned download could not be promoted") from exc

    return {
        "schema_version": "1.0.0",
        "resource_id": manifest["resource_id"],
        "upstream_commit": manifest["upstream"]["commit"],
        "source_archive": {
            "accepted_variant": variant["id"],
            "bytes": byte_count,
            "sha256": digest,
        },
        "content_opened": False,
        "provisioned": False,
        "next_step": "Disable network access, then use --source for content verification.",
    }


def _password_from_args(password_file: Path | None) -> bytes:
    if password_file is not None:
        try:
            file_stat = password_file.lstat()
            if (
                stat.S_ISLNK(file_stat.st_mode)
                or not stat.S_ISREG(file_stat.st_mode)
                or file_stat.st_size > MAX_PASSWORD_FILE_BYTES
            ):
                raise EllipseVerificationError("Password file is not a safe regular file")
            value = password_file.read_text(encoding="utf-8").rstrip("\r\n")
        except EllipseVerificationError:
            raise
        except (OSError, UnicodeError) as exc:
            raise EllipseVerificationError("Password file could not be read") from exc
    else:
        value = os.environ.get(PASSWORD_ENVIRONMENT_VARIABLE, "")
        if not value and sys.stdin.isatty():
            value = getpass.getpass("ELLIPSE test ZIP password (see pinned README): ")
    if not value:
        raise EllipseVerificationError(
            f"Provide the documented test password through {PASSWORD_ENVIRONMENT_VARIABLE}, "
            "--test-password-file, or the interactive prompt"
        )
    return value.encode("utf-8")


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--source",
        type=Path,
        help="Existing accepted ELLIPSE outer ZIP (verified before opening).",
    )
    source.add_argument(
        "--download",
        action="store_true",
        help="Download and hash the opaque pinned archive without opening it.",
    )
    parser.add_argument(
        "--download-path",
        type=Path,
        default=DEFAULT_DOWNLOAD_PATH,
        help=f"Private path used only with --download (default: {DEFAULT_DOWNLOAD_PATH})",
    )
    parser.add_argument(
        "--test-password-file",
        type=Path,
        help="UTF-8 file containing the public password documented upstream.",
    )
    parser.add_argument(
        "--provision",
        action="store_true",
        help="Write the two verified final CSVs and aggregate verification locally.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Private destination used only with --provision (default: {DEFAULT_OUTPUT_DIR})",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _argument_parser()
    args = parser.parse_args(argv)
    if not args.provision and args.output_dir != DEFAULT_OUTPUT_DIR:
        parser.error("--output-dir requires --provision")
    if not args.download and args.download_path != DEFAULT_DOWNLOAD_PATH:
        parser.error("--download-path requires --download")
    if args.download and args.provision:
        parser.error("--download and --provision are separate network/offline steps")
    if args.download and args.test_password_file is not None:
        parser.error("--download does not open encrypted members or need a password")
    try:
        manifest = load_manifest()
        if args.download:
            summary = download_pinned_archive(
                args.download_path,
                manifest=manifest,
            )
            print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
            return 0

        password = _password_from_args(args.test_password_file)
        verified = verify_source(
            args.source,
            manifest=manifest,
            test_password=password,
        )
        if args.provision:
            provision_verified(verified, args.output_dir, manifest=manifest)
        stdout_summary = dict(verified.summary)
        stdout_summary["provisioned"] = bool(args.provision)
        print(json.dumps(stdout_summary, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except EllipseVerificationError as exc:
        print(f"ELLIPSE verification failed: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Fail closed without echoing paths, CSV values, passwords, or library
        # exception details that could contain row-level source material.
        print("ELLIPSE verification failed without exposing source details", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
