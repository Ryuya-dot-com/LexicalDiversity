"""Deterministic aggregate builder for the MASC 3.0.0 data-only archive.

The builder accepts an existing local ZIP and reads UTF-8 ``.txt`` members
without extracting them.  It writes only lower-cased surface-form frequency
tables; corpus text and ZIP member names are not copied into the output.
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
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Iterable

from .tokenizer import ASCII_LEGACY_V1, tokenize


MASC_VERSION = "3.0.0"
MASC_SOURCE_ASSET = "masc_500k_texts.zip"
MASC_SOURCE_URL = "https://www.anc.org/MASC/download/masc_500k_texts.zip"
MASC_PROJECT_URL = "https://anc.org/data/masc/"
MASC_LICENSE = "Creative Commons Attribution 3.0 United States"
MASC_LICENSE_URL = "https://creativecommons.org/licenses/by/3.0/us/"

UNIGRAM_ARTIFACT = "masc_3_0_0_surface_unigrams.csv.gz"
BIGRAM_ARTIFACT = "masc_3_0_0_surface_bigrams.csv.gz"
TRIGRAM_ARTIFACT = "masc_3_0_0_surface_trigrams.csv.gz"
UNIGRAM_COLUMNS = ("surface", "frequency", "document_frequency")
BIGRAM_COLUMNS = ("token_1", "token_2", "frequency")
TRIGRAM_COLUMNS = ("token_1", "token_2", "token_3", "frequency")

DEFAULT_MAX_ARCHIVE_MEMBERS = 10_000
DEFAULT_MAX_DOCUMENTS = 5_000
DEFAULT_MAX_MEMBER_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_TEXT_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 300.0

_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return the SHA-256 digest of a file without loading it all at once."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def masc_notice_text() -> str:
    """Return the attribution notice shipped with derived MASC aggregates."""

    return f"""# MASC attribution notice

The aggregate tables in this directory are derived from the **Manually
Annotated Sub-Corpus (MASC) {MASC_VERSION} data-only corpus**, distributed by
the American National Corpus project.

- Project: {MASC_PROJECT_URL}
- Official data-only archive: {MASC_SOURCE_URL}
- License: [{MASC_LICENSE}]({MASC_LICENSE_URL})

Changes made by this project: each UTF-8 text document was tokenized with the
versioned `{ASCII_LEGACY_V1}` policy (ASCII letters with an optional internal
apostrophe), converted to lower case, and reduced to aggregate surface-form
unigram, document-frequency, bigram, and trigram counts. N-grams never cross
document boundaries. Source documents, document names, annotations, sentences,
and longer text spans are not included.

The repository's software license does not replace the MASC data license. No
endorsement by the American National Corpus project is implied.
"""


def _validated_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
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
    normalized = parsed.isoformat()
    if normalized != value:
        raise ValueError("Acquisition date must use canonical YYYY-MM-DD format")
    return normalized


def _safe_member_name(info: zipfile.ZipInfo) -> str:
    name = info.filename.replace("\\", "/")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("ZIP contains an unsafe member path")
    if any(part in {"", "."} for part in path.parts):
        raise ValueError("ZIP contains a non-canonical member path")
    return name


def _text_members(
    archive: zipfile.ZipFile,
    *,
    max_archive_members: int,
    max_documents: int,
    max_member_bytes: int,
    max_text_bytes: int,
    max_compression_ratio: float,
) -> tuple[list[zipfile.ZipInfo], int, int]:
    infos = archive.infolist()
    if len(infos) > max_archive_members:
        raise ValueError(
            f"ZIP member limit exceeded: {len(infos)} > {max_archive_members}"
        )

    documents: list[tuple[str, zipfile.ZipInfo]] = []
    seen_names: set[str] = set()
    ignored_members = 0
    total_text_bytes = 0
    for info in infos:
        name = _safe_member_name(info)
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted ZIP members are not supported")
        if name in seen_names:
            raise ValueError("ZIP contains duplicate member names")
        seen_names.add(name)

        path = PurePosixPath(name)
        is_metadata = "__MACOSX" in path.parts or any(
            part.startswith("._") for part in path.parts
        )
        if path.suffix.casefold() != ".txt" or is_metadata:
            ignored_members += 1
            continue

        if info.file_size > max_member_bytes:
            raise ValueError(
                f"ZIP text-member size limit exceeded: {info.file_size} > {max_member_bytes}"
            )
        if info.file_size and not info.compress_size:
            raise ValueError("ZIP text member has an invalid compressed size")
        if info.compress_size:
            ratio = info.file_size / info.compress_size
            if ratio > max_compression_ratio:
                raise ValueError(
                    "ZIP text-member compression-ratio limit exceeded: "
                    f"{ratio:.1f} > {max_compression_ratio:.1f}"
                )

        total_text_bytes += info.file_size
        if total_text_bytes > max_text_bytes:
            raise ValueError(
                f"ZIP total text-size limit exceeded: {total_text_bytes} > {max_text_bytes}"
            )
        documents.append((name, info))

    if not documents:
        raise ValueError("ZIP contains no UTF-8 .txt corpus documents")
    if len(documents) > max_documents:
        raise ValueError(
            f"ZIP document limit exceeded: {len(documents)} > {max_documents}"
        )
    documents.sort(key=lambda item: item[0])
    return [info for _name, info in documents], ignored_members, total_text_bytes


def _ngrams(tokens: list[str], width: int) -> Iterable[tuple[str, ...]]:
    return zip(*(tokens[offset:] for offset in range(width)))


def _aggregate_archive(
    source_zip: Path,
    *,
    max_archive_members: int,
    max_documents: int,
    max_member_bytes: int,
    max_text_bytes: int,
    max_compression_ratio: float,
) -> tuple[
    Counter[str],
    Counter[str],
    Counter[tuple[str, str]],
    Counter[tuple[str, str, str]],
    dict[str, int],
]:
    unigrams: Counter[str] = Counter()
    document_frequency: Counter[str] = Counter()
    bigrams: Counter[tuple[str, str]] = Counter()
    trigrams: Counter[tuple[str, str, str]] = Counter()
    token_count = 0
    empty_documents = 0

    try:
        with zipfile.ZipFile(source_zip) as archive:
            members, ignored_members, text_bytes = _text_members(
                archive,
                max_archive_members=max_archive_members,
                max_documents=max_documents,
                max_member_bytes=max_member_bytes,
                max_text_bytes=max_text_bytes,
                max_compression_ratio=max_compression_ratio,
            )
            for info in members:
                with archive.open(info, "r") as binary:
                    with io.TextIOWrapper(
                        binary,
                        encoding="utf-8-sig",
                        errors="strict",
                        newline=None,
                    ) as text:
                        tokens = tokenize(
                            text.read(),
                            lower=True,
                            policy=ASCII_LEGACY_V1,
                        )
                if not tokens:
                    empty_documents += 1
                token_count += len(tokens)
                unigrams.update(tokens)
                document_frequency.update(set(tokens))
                bigrams.update(_ngrams(tokens, 2))
                trigrams.update(_ngrams(tokens, 3))
    except zipfile.BadZipFile as exc:
        raise ValueError("Source is not a valid ZIP archive") from exc
    except UnicodeDecodeError as exc:
        raise ValueError("A corpus .txt member is not valid UTF-8") from exc

    return (
        unigrams,
        document_frequency,
        bigrams,
        trigrams,
        {
            "documents": len(members),
            "empty_documents": empty_documents,
            "ignored_members": ignored_members,
            "source_text_bytes": text_bytes,
            "tokens": token_count,
        },
    )


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
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)


def _write_csv_gzip(
    destination: Path,
    columns: tuple[str, ...],
    rows: Iterable[tuple[object, ...]],
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
                    writer.writerow(columns)
                    for row in rows:
                        writer.writerow(row)
                        row_count += 1
        os.replace(temporary, destination)
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "file": destination.name,
        "format": "deterministic gzip-compressed UTF-8 CSV",
        "columns": list(columns),
        "rows": row_count,
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
    }


def make_masc_manifest(
    *,
    source_bytes: int,
    source_sha256: str,
    expected_source_sha256: str | None,
    source_url: str,
    acquired_on: str | None,
    corpus_stats: dict[str, int],
    artifacts: list[dict[str, object]],
) -> dict[str, object]:
    """Create provenance metadata without recording a local source path."""

    return {
        "id": "masc-3.0.0-surface-frequency-ngrams",
        "name": "MASC 3.0.0 surface frequency and n-gram aggregates",
        "creator": "American National Corpus project",
        "version": MASC_VERSION,
        "source_project_url": MASC_PROJECT_URL,
        "license": MASC_LICENSE,
        "license_url": MASC_LICENSE_URL,
        "redistributable": True,
        "web_service_usable": True,
        "attribution_file": "NOTICE.md",
        "source": {
            "asset": MASC_SOURCE_ASSET,
            "provenance_url": source_url,
            "acquired_on": acquired_on,
            "bytes": source_bytes,
            "sha256": source_sha256,
            "checksum_check": (
                "matched-caller-supplied-expected-sha256"
                if expected_source_sha256 is not None
                else "not-requested"
            ),
            "origin_verified_by_builder": False,
            "bundled": False,
            "retrieved_by_builder": False,
        },
        "artifacts": artifacts,
        "build": {
            "script": "scripts/build_masc_aggregates.py",
            "algorithm_version": 1,
            **corpus_stats,
            "surface_types": artifacts[0]["rows"],
            "bigram_types": artifacts[1]["rows"],
            "trigram_types": artifacts[2]["rows"],
            "document_unit": "one non-directory .txt ZIP member",
            "text_encoding": "UTF-8 (optional leading BOM accepted)",
            "tokenizer_policy": ASCII_LEGACY_V1,
            "tokenization": (
                "ASCII letters with one optional internal apostrophe; lower-cased; "
                "no lemmatization"
            ),
            "ngram_boundary": "reset at every document boundary",
            "row_order": "ascending Unicode code-point order by surface token tuple",
            "gzip_mtime": 0,
        },
        "changes": [
            "Discarded source text and ZIP member names after aggregation.",
            "Counted lower-cased surface unigrams and document frequency.",
            "Counted contiguous bigrams and trigrams within each document only.",
            "Excluded all non-.txt members and macOS metadata members.",
        ],
        "privacy": {
            "source_text_bundled": False,
            "document_names_bundled": False,
            "maximum_ngram_width": 3,
        },
    }


def build_masc_aggregates(
    source_zip: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    expected_source_sha256: str | None = None,
    expected_source_bytes: int | None = None,
    acquired_on: str | None = None,
    source_url: str = MASC_SOURCE_URL,
    max_archive_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_documents: int = DEFAULT_MAX_DOCUMENTS,
    max_member_bytes: int = DEFAULT_MAX_MEMBER_BYTES,
    max_text_bytes: int = DEFAULT_MAX_TEXT_BYTES,
    max_compression_ratio: float = DEFAULT_MAX_COMPRESSION_RATIO,
) -> dict[str, object]:
    """Build deterministic MASC aggregate artifacts from a local ZIP.

    ``expected_source_sha256`` verifies identity when supplied.  The function
    never downloads a source, disables TLS checks, extracts members, or writes
    corpus text.  The command-line wrapper requires a checksum so production
    builds cannot accidentally proceed from an unpinned input.
    """

    source = Path(source_zip)
    if not source.is_file():
        raise FileNotFoundError(f"MASC source ZIP does not exist: {source}")
    expected_digest = _validated_sha256(expected_source_sha256)
    acquisition_date = _validated_acquisition_date(acquired_on)
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError("Source provenance URL must not be empty")
    if expected_source_bytes is not None and expected_source_bytes < 0:
        raise ValueError("Expected source size must not be negative")
    for label, value in (
        ("max_archive_members", max_archive_members),
        ("max_documents", max_documents),
        ("max_member_bytes", max_member_bytes),
        ("max_text_bytes", max_text_bytes),
    ):
        if value <= 0:
            raise ValueError(f"{label} must be positive")
    if max_compression_ratio <= 0:
        raise ValueError("max_compression_ratio must be positive")

    source_bytes = source.stat().st_size
    source_digest = sha256_file(source)
    if expected_source_bytes is not None and source_bytes != expected_source_bytes:
        raise ValueError(
            f"Source size mismatch: expected {expected_source_bytes}, got {source_bytes}"
        )
    if expected_digest is not None and source_digest != expected_digest:
        raise ValueError(
            f"Source SHA-256 mismatch: expected {expected_digest}, got {source_digest}"
        )

    (
        unigrams,
        document_frequency,
        bigrams,
        trigrams,
        corpus_stats,
    ) = _aggregate_archive(
        source,
        max_archive_members=max_archive_members,
        max_documents=max_documents,
        max_member_bytes=max_member_bytes,
        max_text_bytes=max_text_bytes,
        max_compression_ratio=max_compression_ratio,
    )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    unigram_artifact = _write_csv_gzip(
        output / UNIGRAM_ARTIFACT,
        UNIGRAM_COLUMNS,
        (
            (surface, unigrams[surface], document_frequency[surface])
            for surface in sorted(unigrams)
        ),
    )
    bigram_artifact = _write_csv_gzip(
        output / BIGRAM_ARTIFACT,
        BIGRAM_COLUMNS,
        ((*tokens, bigrams[tokens]) for tokens in sorted(bigrams)),
    )
    trigram_artifact = _write_csv_gzip(
        output / TRIGRAM_ARTIFACT,
        TRIGRAM_COLUMNS,
        ((*tokens, trigrams[tokens]) for tokens in sorted(trigrams)),
    )
    artifacts = [unigram_artifact, bigram_artifact, trigram_artifact]
    manifest = make_masc_manifest(
        source_bytes=source_bytes,
        source_sha256=source_digest,
        expected_source_sha256=expected_digest,
        source_url=source_url.strip(),
        acquired_on=acquisition_date,
        corpus_stats=corpus_stats,
        artifacts=artifacts,
    )
    _atomic_write_bytes(output / "NOTICE.md", masc_notice_text().encode("utf-8"))
    _atomic_write_bytes(
        output / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return manifest
