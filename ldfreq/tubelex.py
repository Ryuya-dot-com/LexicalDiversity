"""Safe TUBELEX frequency-table ingestion and runtime metrics.

This module consumes an *already published* TUBELEX TSV frequency table.  It
never retrieves or parses subtitles, video metadata, video IDs, channel IDs, or
document names.  The builder validates the official ``word``, ``count``,
``videos``, ``channels``, ``count:*`` schema and emits a deterministic compact
CSV.gz lookup table plus provenance metadata.

TUBELEX TSV deliberately has no CSV-style quoting.  In particular, a word may
contain an unmatched double quote.  The source reader therefore uses
``csv.QUOTE_NONE``; changing that setting can silently join many physical rows.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import lzma
import math
import os
import re
import tempfile
import unicodedata
import zlib
from array import array
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePath
from typing import Iterable, Iterator, Sequence


TUBELEX_PROJECT_URL = "https://github.com/naist-nlp/tubelex"
TUBELEX_EN_SOURCE_COMMIT = "7cb5fb36add76b83a266d1967536e1a1d3faa513"
TUBELEX_EN_SOURCE_ASSET = "tubelex-en-treebank.tsv.xz"
TUBELEX_EN_SOURCE_URL = (
    "https://raw.githubusercontent.com/naist-nlp/tubelex/"
    f"{TUBELEX_EN_SOURCE_COMMIT}/frequencies/{TUBELEX_EN_SOURCE_ASSET}"
)
TUBELEX_EN_SOURCE_BYTES = 4_152_940
TUBELEX_EN_SOURCE_SHA256 = (
    "4096022259d5eaa7261c3bf22c3b0af9fd58ae8eebe17894c0b34a163954f936"
)
TUBELEX_EN_SOURCE_DECOMPRESSED_SHA256 = (
    "5ccfde4184698c1fa8049ba7c761d253d039fa5ad4e93e15239644fe6034b5c1"
)
TUBELEX_EN_SOURCE_TOTAL_TOKENS = 171_805_865
TUBELEX_EN_SOURCE_VOCABULARY_SIZE = 613_309
TUBELEX_EN_SOURCE_TOTAL_VIDEOS = 105_733
TUBELEX_EN_SOURCE_TOTAL_CHANNELS = 68_405
TUBELEX_EN_RETAINED_TOKEN_MASS = 169_889_910
TUBELEX_SOURCE_NLTK_VERSION = "3.8.1"
TUBELEX_PRODUCTION_NLTK_VERSION = "3.10.0"
# The word-tokenization implementation (as distinct from the detokenizer) was
# audited against the official NLTK 3.8.1 source lineage.  Runtime execution is
# deliberately fail-closed on the current production pin even though the
# project's pre-upgrade environment verified the same rules under 3.9.3.
TUBELEX_AUDITED_NLTK_VERSIONS = frozenset({TUBELEX_PRODUCTION_NLTK_VERSION})
TUBELEX_EN_METHOD_ID = (
    "tubelex-en-treebank-7cb5fb36-detseg-apostrophe-laplace-beta11-v2"
)
TUBELEX_EN_FREQUENCY_UNSEEN_ZIPF = math.log10(
    1_000_000_000
    / (TUBELEX_EN_SOURCE_TOTAL_TOKENS + TUBELEX_EN_SOURCE_VOCABULARY_SIZE)
)
TUBELEX_EN_VIDEO_UNSEEN_LOG10_PREVALENCE = math.log10(
    1 / (TUBELEX_EN_SOURCE_TOTAL_VIDEOS + 2)
)
TUBELEX_EN_CHANNEL_UNSEEN_LOG10_PREVALENCE = math.log10(
    1 / (TUBELEX_EN_SOURCE_TOTAL_CHANNELS + 2)
)
TUBELEX_REPOSITORY_LICENSE = "BSD 3-Clause License"
TUBELEX_REPOSITORY_LICENSE_SPDX = "BSD-3-Clause"
TUBELEX_REPOSITORY_LICENSE_URL = (
    "https://github.com/naist-nlp/tubelex/blob/"
    f"{TUBELEX_EN_SOURCE_COMMIT}/LICENSE"
)
TUBELEX_BSD_LICENSE_TEXT = """BSD 3-Clause License

Copyright (c) 2022-4, Adam Nohejl
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""

ARTIFACT_NAME = "tubelex_en_treebank_7cb5fb36_frequency_index.csv.gz"
PRODUCTION_RESOURCE_ID = "tubelex-en-treebank-7cb5fb36-frequency-index"
DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "open"
    / "tubelex"
    / "en"
    / "2025-04-24-7cb5fb36"
    / ARTIFACT_NAME
)
# Independently pin the deterministic runtime artifact, not only its manifest.
PRODUCTION_ARTIFACT_BYTES = 4_572_297
PRODUCTION_ARTIFACT_SHA256 = (
    "3731f23f3385ed630777ff56b5edbed5db46eee256ededceb0ac213016f31675"
)
PRODUCTION_ARTIFACT_ROWS = 515_292
BASE_COLUMNS = ("word", "count", "videos", "channels")
TOTAL_WORD = "[TOTAL]"

DEFAULT_MAX_SOURCE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
DEFAULT_MAX_LINE_BYTES = 64 * 1024
DEFAULT_MAX_ROWS = 1_000_000
DEFAULT_MAX_COLUMNS = 128
DEFAULT_MAX_WORD_CHARS = 4_096
DEFAULT_MAX_INTEGER = (1 << 32) - 1
DEFAULT_MAX_MANIFEST_BYTES = 1024 * 1024

_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_NONNEGATIVE_INTEGER_RE = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_TREEBANK_COMPONENT_SPLIT_RE = re.compile(r"['-]")
_TREEBANK_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.?!])\s+|(?:\r?\n)+")
_TYPOGRAPHIC_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "\u02bc": "'",  # modifier letter apostrophe
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u201b": "'",  # single high-reversed-9 quotation mark
    }
)
TUBELEX_RUNTIME_NORMALIZATION = (
    "Unicode NFKC; U+02BC/U+2018/U+2019/U+201B apostrophes mapped to ASCII; "
    "then str.lower()"
)
LOOKUP_FILTER_PATTERN = (
    "len(token)<=64; optional leading ASCII apostrophe; every remaining "
    "apostrophe/hyphen-separated component satisfies str.isalpha()"
)
LOOKUP_FILTER_POLICY = (
    "TUBELEX English Penn Treebank tokens after NFKC and lower, restricted to "
    "Unicode-alphabetic lexical components and at most 64 code points"
)


@dataclass(frozen=True, slots=True)
class TubelexRecord:
    """Frequency and dispersion counts for one TUBELEX word."""

    word: str
    count: int
    videos: int
    channels: int
    category_counts: tuple[int, ...]


def _manifest_source_vocabulary_size(metadata: dict[str, object]) -> int | None:
    build = metadata.get("build")
    if not isinstance(build, dict):
        return None
    lookup_filter = build.get("lookup_filter")
    if not isinstance(lookup_filter, dict):
        return None
    value = lookup_filter.get("source_vocabulary_size")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


class TubelexIndex:
    """Memory-conscious runtime index over a validated TUBELEX artifact.

    Numeric columns are held in packed unsigned-integer arrays instead of one
    Python dictionary per row.  A :class:`TubelexRecord` is created only when a
    word is looked up.
    """

    __slots__ = (
        "_categories",
        "_word_to_row",
        "_words",
        "_counts",
        "_videos",
        "_channels",
        "_category_counts",
        "_totals",
        "_source_vocabulary_size",
        "metadata",
    )

    def __init__(
        self,
        records: Iterable[TubelexRecord],
        *,
        categories: Sequence[str],
        totals: TubelexRecord,
        source_vocabulary_size: int | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        normalized_categories = tuple(str(value) for value in categories)
        if len(set(normalized_categories)) != len(normalized_categories):
            raise ValueError("TUBELEX index categories must be unique")
        if len(totals.category_counts) != len(normalized_categories):
            raise ValueError("TUBELEX total row does not match the category schema")

        self._categories = normalized_categories
        self._word_to_row: dict[str, int] = {}
        words: list[str] = []
        counts = array("I")
        videos = array("I")
        channels = array("I")
        category_arrays = tuple(array("I") for _ in normalized_categories)

        for record in records:
            if len(record.category_counts) != len(normalized_categories):
                raise ValueError("A TUBELEX record does not match the category schema")
            if record.word in self._word_to_row:
                raise ValueError("Duplicate word in TUBELEX index")
            if any(
                value < 0 or value > DEFAULT_MAX_INTEGER
                for value in (
                    record.count,
                    record.videos,
                    record.channels,
                    *record.category_counts,
                )
            ):
                raise ValueError("A TUBELEX record exceeds the packed integer range")
            row_number = len(words)
            self._word_to_row[record.word] = row_number
            words.append(record.word)
            counts.append(record.count)
            videos.append(record.videos)
            channels.append(record.channels)
            for values, value in zip(category_arrays, record.category_counts):
                values.append(value)

        self._words = tuple(words)
        self._counts = counts
        self._videos = videos
        self._channels = channels
        self._category_counts = category_arrays
        self._totals = totals
        self.metadata = dict(metadata or {})
        if source_vocabulary_size is None:
            source_vocabulary_size = _manifest_source_vocabulary_size(self.metadata)
        if source_vocabulary_size is None:
            source_vocabulary_size = len(words)
        if (
            isinstance(source_vocabulary_size, bool)
            or not isinstance(source_vocabulary_size, int)
            or source_vocabulary_size < len(words)
        ):
            raise ValueError(
                "TUBELEX source vocabulary size must be an integer no smaller than the index"
            )
        self._source_vocabulary_size = source_vocabulary_size

    def __len__(self) -> int:
        return len(self._words)

    @property
    def categories(self) -> tuple[str, ...]:
        """Return category names without their ``count:`` prefix."""

        return self._categories

    @property
    def totals(self) -> TubelexRecord:
        """Return the validated corpus-total row."""

        return self._totals

    @property
    def retained_token_mass(self) -> int:
        """Return the token mass represented by retained lookup rows."""

        return sum(self._counts)

    @property
    def source_vocabulary_size(self) -> int:
        """Return the pre-filter source vocabulary size used for smoothing."""

        return self._source_vocabulary_size

    @property
    def words(self) -> frozenset[str]:
        """Return an immutable vocabulary snapshot."""

        return frozenset(self._word_to_row)

    def lookup(self, word: str) -> TubelexRecord | None:
        """Look up a word using exact form, then the runtime normalization.

        The fallback matches the pinned TUBELEX English Treebank frequency
        variant while preserving exact lookup for an already-normalized token.
        """

        value = str(word)
        row = self._word_to_row.get(value)
        if row is None:
            normalized = normalize_tubelex_input_token(value)
            row = self._word_to_row.get(normalized)
        if row is None:
            return None
        return TubelexRecord(
            word=self._words[row],
            count=self._counts[row],
            videos=self._videos[row],
            channels=self._channels[row],
            category_counts=tuple(values[row] for values in self._category_counts),
        )


def normalize_tubelex_token(value: str) -> str:
    """Normalize a published TUBELEX source key with NFKC + lower."""

    return unicodedata.normalize("NFKC", str(value)).strip().lower()


def normalize_tubelex_input_text(value: str) -> str:
    """Normalize submitted text, including common typographic apostrophes."""

    return (
        unicodedata.normalize("NFKC", str(value))
        .translate(_TYPOGRAPHIC_APOSTROPHE_TRANSLATION)
        .lower()
    )


def normalize_tubelex_input_token(value: str) -> str:
    """Apply runtime text normalization to a single lookup token."""

    return normalize_tubelex_input_text(value).strip()


def is_lookup_compatible_word(value: str) -> bool:
    """Return whether a source row can be emitted by the safe Treebank adapter."""

    word = str(value)
    if not (0 < len(word) <= 64) or word != normalize_tubelex_token(word):
        return False
    lexical = word[1:] if word.startswith("'") else word
    if not lexical:
        return False
    return all(
        component and component.isalpha()
        for component in _TREEBANK_COMPONENT_SPLIT_RE.split(lexical)
    )


def adapt_tubelex_tokens(tokens: Iterable[str]) -> list[str]:
    """Retokenize application tokens with the pinned Treebank adapter."""

    adapted: list[str] = []
    for raw_token in tokens:
        adapted.extend(tokenize_tubelex_text(str(raw_token)))
    return adapted


def tokenize_tubelex_text(text: str) -> list[str]:
    """Tokenize with audited, model-free NLTK Treebank rules.

    Input is NFKC-normalized, common typographic apostrophes are mapped to ASCII,
    and text is lower-cased before deterministic splitting at line boundaries
    and whitespace following ``.?!``.  Treebank tokenization is called directly
    per segment and therefore downloads no Punkt model.  A
    versioned lexical predicate removes punctuation, numbers, malformed keys,
    and tokens over 64 code points. Productive Treebank units such as
    ``do + n't``, ``ca + n't``, ``wo + n't``, ``i + 'm``, hyphenated words, and
    alphabetic apostrophe compounds remain eligible.  Coverage exposes any
    conservative-filter loss. This small pre-segmenter can split abbreviations;
    it is an explicit, versioned engineering approximation rather than Punkt.
    """

    try:
        import nltk
        from nltk.tokenize import TreebankWordTokenizer
    except ImportError as exc:  # pragma: no cover - deployment contract test covers it
        raise RuntimeError(
            "TUBELEX Treebank analysis requires the pinned nltk dependency"
        ) from exc

    runtime_version = str(getattr(nltk, "__version__", ""))
    if runtime_version not in TUBELEX_AUDITED_NLTK_VERSIONS:
        raise RuntimeError(
            "TUBELEX Treebank analysis requires an audited nltk version"
        )

    normalized = normalize_tubelex_input_text(text or "")
    # TreebankWordTokenizer expects sentence-sized input.  A deliberately small,
    # pinned pre-segmenter prevents ordinary mid-document sentence-final forms
    # such as "I'm." from being returned as one punctuation-bearing token.  It
    # is not a linguistic sentence model: abbreviations may be split, which is
    # documented and reflected in coverage.
    segments = _TREEBANK_SENTENCE_BOUNDARY_RE.split(normalized)
    tokenizer = TreebankWordTokenizer()
    tokens = [
        token
        for segment in segments
        if segment
        for token in tokenizer.tokenize(segment, convert_parentheses=False)
    ]
    return [token for token in tokens if is_lookup_compatible_word(token)]


def sha256_file(path: str | os.PathLike[str]) -> str:
    """Return a streaming SHA-256 digest."""

    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().casefold()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError("Expected SHA-256 must be exactly 64 hexadecimal characters")
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


def _validate_positive_limits(**limits: int) -> None:
    for label, value in limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{label} must be a positive integer")


def _validate_metadata_value(label: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")
    normalized = value.strip()
    if any(character in normalized for character in ("\x00", "\r", "\n")):
        raise ValueError(f"{label} contains an unsupported control character")
    return normalized


def _source_compression(path: Path) -> str:
    lowered = path.name.casefold()
    if lowered.endswith(".tsv.xz") or lowered.endswith(".xz"):
        return "xz"
    if lowered.endswith(".tsv"):
        return "plain"
    raise ValueError("TUBELEX source must be a .tsv or .tsv.xz file")


def _iter_bounded_binary_lines(
    path: Path,
    *,
    compression: str,
    max_decompressed_bytes: int,
    max_line_bytes: int,
) -> Iterator[bytes]:
    total_bytes = 0
    try:
        binary = lzma.open(path, "rb") if compression == "xz" else path.open("rb")
        with binary:
            while True:
                line = binary.readline(max_line_bytes + 1)
                if not line:
                    break
                if len(line) > max_line_bytes:
                    raise ValueError("TUBELEX input line-size limit exceeded")
                total_bytes += len(line)
                if total_bytes > max_decompressed_bytes:
                    raise ValueError("TUBELEX decompressed-size limit exceeded")
                yield line
    except (EOFError, lzma.LZMAError, OSError) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("TUBELEX source could not be decompressed safely") from exc


def _iter_table_rows(
    path: Path,
    *,
    delimiter: str,
    compression: str,
    quoting: int,
    max_decompressed_bytes: int,
    max_line_bytes: int,
) -> Iterator[tuple[int, list[str]]]:
    for line_number, raw_line in enumerate(
        _iter_bounded_binary_lines(
            path,
            compression=compression,
            max_decompressed_bytes=max_decompressed_bytes,
            max_line_bytes=max_line_bytes,
        ),
        start=1,
    ):
        try:
            encoding = "utf-8-sig" if line_number == 1 else "utf-8"
            decoded = raw_line.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"TUBELEX input is not valid UTF-8 at row {line_number}"
            ) from exc
        decoded = decoded.removesuffix("\n").removesuffix("\r")
        try:
            rows = list(
                csv.reader(
                    [decoded],
                    delimiter=delimiter,
                    quoting=quoting,
                    strict=True,
                )
            )
        except csv.Error as exc:
            raise ValueError(f"Malformed TUBELEX row {line_number}") from exc
        if len(rows) != 1 or not rows[0] or rows[0] == []:
            raise ValueError(f"Empty TUBELEX row at row {line_number}")
        yield line_number, rows[0]


def _validate_header(
    fields: Sequence[str], *, max_columns: int
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if len(fields) > max_columns:
        raise ValueError("TUBELEX column limit exceeded")
    if tuple(fields[: len(BASE_COLUMNS)]) != BASE_COLUMNS:
        raise ValueError(
            "Unexpected TUBELEX columns; expected word,count,videos,channels first"
        )
    if len(fields) == len(BASE_COLUMNS):
        raise ValueError("TUBELEX table must contain at least one count:* category")
    if len(set(fields)) != len(fields):
        raise ValueError("TUBELEX header contains duplicate columns")

    category_columns = tuple(fields[len(BASE_COLUMNS) :])
    if any(not name.startswith("count:") or not name.removeprefix("count:") for name in category_columns):
        raise ValueError("Every TUBELEX category column must use a non-empty count:* name")
    categories = tuple(name.removeprefix("count:") for name in category_columns)
    return tuple(fields), categories


def _parse_nonnegative_integer(
    raw_value: str,
    *,
    row_number: int,
    max_integer: int,
) -> int:
    if not _NONNEGATIVE_INTEGER_RE.fullmatch(raw_value):
        raise ValueError(f"TUBELEX row {row_number} contains a non-canonical count")
    value = int(raw_value)
    if value > max_integer:
        raise ValueError(f"TUBELEX row {row_number} exceeds the integer limit")
    return value


def _parse_frequency_table(
    path: Path,
    *,
    delimiter: str,
    compression: str,
    quoting: int,
    max_decompressed_bytes: int,
    max_line_bytes: int,
    max_rows: int,
    max_columns: int,
    max_word_chars: int,
    max_integer: int,
    require_sorted: bool,
    require_complete_totals: bool,
) -> tuple[list[TubelexRecord], tuple[str, ...], TubelexRecord, dict[str, int]]:
    row_iterator = _iter_table_rows(
        path,
        delimiter=delimiter,
        compression=compression,
        quoting=quoting,
        max_decompressed_bytes=max_decompressed_bytes,
        max_line_bytes=max_line_bytes,
    )
    try:
        header_row_number, header_fields = next(row_iterator)
    except StopIteration as exc:
        raise ValueError("TUBELEX table is empty") from exc
    if header_row_number != 1:
        raise AssertionError("The first parsed table row must be row one")
    columns, categories = _validate_header(header_fields, max_columns=max_columns)

    records: list[TubelexRecord] = []
    seen_words: set[str] = set()
    total_record: TubelexRecord | None = None
    included_count = 0
    included_categories = [0] * len(categories)
    previous_word: str | None = None
    physical_rows = 1

    for row_number, fields in row_iterator:
        physical_rows = row_number
        if len(fields) != len(columns):
            raise ValueError(f"TUBELEX row {row_number} has an unexpected field count")
        word = fields[0]
        if not word:
            raise ValueError(f"TUBELEX row {row_number} has an empty word")
        if len(word) > max_word_chars:
            raise ValueError(f"TUBELEX row {row_number} exceeds the word-length limit")
        if any(character in word for character in ("\x00", "\t", "\r", "\n")):
            raise ValueError(f"TUBELEX row {row_number} has a control character in word")

        numeric = tuple(
            _parse_nonnegative_integer(
                value,
                row_number=row_number,
                max_integer=max_integer,
            )
            for value in fields[1:]
        )
        count, videos, channels, *category_counts_list = numeric
        category_counts = tuple(category_counts_list)
        if channels > videos or videos > count:
            raise ValueError(
                f"TUBELEX row {row_number} violates channels <= videos <= count"
            )
        if sum(category_counts) != count:
            raise ValueError(
                f"TUBELEX row {row_number} category counts do not sum to count"
            )
        record = TubelexRecord(word, count, videos, channels, category_counts)

        if word == TOTAL_WORD:
            if total_record is not None:
                raise ValueError("TUBELEX table contains more than one [TOTAL] row")
            total_record = record
            continue
        if len(records) >= max_rows:
            raise ValueError("TUBELEX row limit exceeded")
        if word in seen_words:
            raise ValueError(f"Duplicate word in TUBELEX table at row {row_number}")
        seen_words.add(word)
        if require_sorted and previous_word is not None and word <= previous_word:
            raise ValueError("TUBELEX artifact words are not in canonical sorted order")
        previous_word = word
        records.append(record)
        included_count += count
        for index, value in enumerate(category_counts):
            included_categories[index] += value

    if total_record is None:
        raise ValueError("TUBELEX table is missing its [TOTAL] row")
    if not records:
        raise ValueError("TUBELEX table contains no word rows")
    if included_count > total_record.count:
        raise ValueError("TUBELEX word counts exceed the declared total")
    if any(
        subtotal > total
        for subtotal, total in zip(included_categories, total_record.category_counts)
    ):
        raise ValueError("TUBELEX category counts exceed the declared totals")
    if require_complete_totals and included_count != total_record.count:
        raise ValueError("TUBELEX word counts do not match the declared total")
    if require_complete_totals and tuple(included_categories) != total_record.category_counts:
        raise ValueError("TUBELEX category counts do not match the declared totals")

    return records, categories, total_record, {
        "physical_rows": physical_rows,
        "word_rows": len(records),
        "columns": len(columns),
        "included_token_mass": included_count,
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
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)


def _write_artifact(
    destination: Path,
    *,
    records: Sequence[TubelexRecord],
    categories: Sequence[str],
    totals: TubelexRecord,
) -> dict[str, object]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    columns = (*BASE_COLUMNS, *(f"count:{category}" for category in categories))
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
                    for record in sorted(records, key=lambda value: value.word):
                        writer.writerow(
                            (
                                record.word,
                                record.count,
                                record.videos,
                                record.channels,
                                *record.category_counts,
                            )
                        )
                    writer.writerow(
                        (
                            totals.word,
                            totals.count,
                            totals.videos,
                            totals.channels,
                            *totals.category_counts,
                        )
                    )
        os.replace(temporary, destination)
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "file": destination.name,
        "format": "deterministic gzip-compressed UTF-8 CSV",
        "bytes": destination.stat().st_size,
        "sha256": sha256_file(destination),
        "rows": len(records),
        "columns": list(columns),
    }


def tubelex_notice_text(
    *,
    source_version: str,
    source_url: str,
    license_name: str,
    license_url: str,
) -> str:
    """Return attribution and transformation notes for a derived index."""

    return f"""# TUBELEX attribution notice

The derived frequency index in this directory is based solely on an already
published **TUBELEX** frequency table from the NAIST Natural Language Processing
Laboratory project.

- Project: {TUBELEX_PROJECT_URL}
- Source version: `{source_version}`
- Published frequency table: {source_url}
- Source repository license: [{license_name}]({license_url})
- Citation: Adam Nohejl et al. (2025), *Beyond Film Subtitles: Is YouTube the
  Best Approximation of Spoken Vocabulary?*, COLING 2025,
  https://aclanthology.org/2025.coling-main.641/

Changes made by this project: the published TSV table was schema-checked,
integer and total invariants were verified, and only keys that can be emitted by
the pinned Unicode-alphabetic Treebank adapter were retained ({LOOKUP_FILTER_POLICY}).
Retained rows were sorted by the exact word field, converted to CSV, and
gzip-compressed deterministically. Counts on retained rows and the original
corpus total row were preserved.

Submitted text is matched with a separate runtime adapter: Unicode NFKC,
normalization of common typographic apostrophes to ASCII, lower-casing,
deterministic sentence pre-segmentation, and model-free Treebank word-tokenizer
rules. TUBELEX used NLTK {TUBELEX_SOURCE_NLTK_VERSION} for the source variant;
this service pins NLTK {TUBELEX_PRODUCTION_NLTK_VERSION}, whose word-tokenizer
rules were audited as compatible, and downloads no NLTK data model.

No source subtitle document, contiguous subtitle passage, subtitle filename,
video ID, channel ID, video title, source document name, or local input path is
present in the derived index or its manifest. Retained published frequency keys
are limited by the lexical predicate described above. The repository's software
license does not replace the license of the TUBELEX source. No endorsement by
the TUBELEX authors or NAIST is implied.

## Upstream BSD 3-Clause License

{TUBELEX_BSD_LICENSE_TEXT}"""


def make_tubelex_manifest(
    *,
    resource_id: str,
    source_version: str,
    source_asset: str,
    source_url: str,
    license_name: str,
    license_spdx: str | None,
    license_url: str,
    acquired_on: str | None,
    source_bytes: int,
    source_sha256: str,
    expected_source_sha256: str | None,
    artifact: dict[str, object],
    categories: Sequence[str],
    totals: TubelexRecord,
    parse_stats: dict[str, int],
    filter_stats: dict[str, int | float | str],
    require_complete_totals: bool,
) -> dict[str, object]:
    """Create path-free provenance metadata for a TUBELEX build."""

    return {
        "id": resource_id,
        "name": "TUBELEX English Treebank-variant frequency and contextual-diversity index",
        "version": source_version,
        "source_project_url": TUBELEX_PROJECT_URL,
        "license": license_name,
        "license_spdx": license_spdx,
        "license_url": license_url,
        "attribution_file": "NOTICE.md",
        "source": {
            "asset": source_asset,
            "url": source_url,
            "acquired_on": acquired_on,
            "bytes": source_bytes,
            "sha256": source_sha256,
            "checksum_check": (
                "matched-caller-supplied-expected-sha256"
                if expected_source_sha256 is not None
                else "not-requested"
            ),
            "bundled": False,
            "retrieved_by_builder": False,
            "local_path_recorded": False,
        },
        "artifact": artifact,
        "schema": {
            "base_columns": list(BASE_COLUMNS),
            "categories": list(categories),
            "category_columns": [f"count:{category}" for category in categories],
            "total_row": TOTAL_WORD,
            "source_variant": "English Penn Treebank tokenizer",
        },
        "totals": {
            "count": totals.count,
            "videos": totals.videos,
            "channels": totals.channels,
            "category_counts": dict(zip(categories, totals.category_counts)),
        },
        "analysis_method": {
            "id": TUBELEX_EN_METHOD_ID,
            "tokenizer": (
                "deterministic .?!/line pre-segmentation followed by audited "
                "NLTK TreebankWordTokenizer rules; no Punkt model"
            ),
            "source_treebank_nltk_version": TUBELEX_SOURCE_NLTK_VERSION,
            "production_runtime_nltk_version": TUBELEX_PRODUCTION_NLTK_VERSION,
            "audited_runtime_nltk_versions": sorted(TUBELEX_AUDITED_NLTK_VERSIONS),
            "normalization": TUBELEX_RUNTIME_NORMALIZATION,
            "frequency_zipf": "log10(1e9 * (count + 1) / (N + source_V))",
            "video_log10_prevalence": "log10((video_df + 1) / (D_video + 2))",
            "channel_log10_prevalence": "log10((channel_df + 1) / (D_channel + 2))",
            "unseen_in_means": True,
            "category_entropy_public": False,
        },
        "build": {
            "callable": "ldfreq.tubelex.build_tubelex_aggregates",
            "algorithm_version": 1,
            **parse_stats,
            "source_parser": "physical UTF-8 lines; tab delimiter; csv.QUOTE_NONE",
            "source_compression": "plain TSV or LZMA/xz",
            "source_tokenization": (
                "TUBELEX English treebank variant built upstream with NLTK 3.8.1; "
                "runtime adapter uses word-tokenizer rules audited through the "
                "production NLTK 3.10.0 pin, with deterministic .?!/line "
                "pre-segmentation and without Punkt"
            ),
            "row_order": "ascending Unicode code-point order by exact word field",
            "totals_policy": (
                "included rows must exactly equal declared totals"
                if require_complete_totals
                else "included rows must not exceed declared totals"
            ),
            "lookup_filter": {
                "policy": LOOKUP_FILTER_POLICY,
                "fullmatch_regex": LOOKUP_FILTER_PATTERN,
                **filter_stats,
            },
            "gzip_mtime": 0,
        },
        "privacy": {
            "raw_subtitles_bundled": False,
            "contiguous_subtitle_passages_bundled": False,
            "published_frequency_keys_bundled": True,
            "video_ids_bundled": False,
            "channel_ids_bundled": False,
            "document_names_bundled": False,
            "local_source_path_recorded": False,
        },
    }


def build_tubelex_aggregates(
    source_tsv: str | os.PathLike[str],
    output_dir: str | os.PathLike[str],
    *,
    artifact_name: str = ARTIFACT_NAME,
    resource_id: str = PRODUCTION_RESOURCE_ID,
    source_version: str = TUBELEX_EN_SOURCE_COMMIT,
    source_asset: str = TUBELEX_EN_SOURCE_ASSET,
    source_url: str = TUBELEX_EN_SOURCE_URL,
    license_name: str = TUBELEX_REPOSITORY_LICENSE,
    license_spdx: str | None = TUBELEX_REPOSITORY_LICENSE_SPDX,
    license_url: str = TUBELEX_REPOSITORY_LICENSE_URL,
    expected_source_sha256: str | None = None,
    expected_source_bytes: int | None = None,
    acquired_on: str | None = None,
    require_complete_totals: bool = True,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_columns: int = DEFAULT_MAX_COLUMNS,
    max_word_chars: int = DEFAULT_MAX_WORD_CHARS,
    max_integer: int = DEFAULT_MAX_INTEGER,
) -> dict[str, object]:
    """Validate a local TUBELEX frequency table and build server-side artifacts.

    The function performs no network access.  Production callers should pass
    ``expected_source_sha256`` and ``expected_source_bytes`` (the pinned English
    values are exposed as module constants).  Local source paths are never
    copied into output metadata.
    """

    source = Path(source_tsv)
    if not source.is_file():
        raise FileNotFoundError(f"TUBELEX frequency table does not exist: {source}")
    compression = _source_compression(source)
    _validate_positive_limits(
        max_source_bytes=max_source_bytes,
        max_decompressed_bytes=max_decompressed_bytes,
        max_line_bytes=max_line_bytes,
        max_rows=max_rows,
        max_columns=max_columns,
        max_word_chars=max_word_chars,
        max_integer=max_integer,
    )
    expected_digest = _validated_sha256(expected_source_sha256)
    acquisition_date = _validated_acquisition_date(acquired_on)
    if expected_source_bytes is not None and (
        isinstance(expected_source_bytes, bool)
        or not isinstance(expected_source_bytes, int)
        or expected_source_bytes < 0
    ):
        raise ValueError("Expected source size must be a non-negative integer")

    resource_id = _validate_metadata_value("Resource ID", resource_id)
    source_version = _validate_metadata_value("Source version", source_version)
    source_asset = _validate_metadata_value("Source asset", source_asset)
    source_url = _validate_metadata_value("Source URL", source_url)
    license_name = _validate_metadata_value("License name", license_name)
    license_url = _validate_metadata_value("License URL", license_url)
    if license_spdx is not None:
        license_spdx = _validate_metadata_value("License SPDX ID", license_spdx)
    artifact_name = _validate_metadata_value("Artifact name", artifact_name)
    if (
        PurePath(artifact_name).name != artifact_name
        or "/" in artifact_name
        or "\\" in artifact_name
        or not artifact_name.endswith(".csv.gz")
    ):
        raise ValueError("Artifact name must be a path-free .csv.gz filename")

    source_bytes = source.stat().st_size
    if source_bytes > max_source_bytes:
        raise ValueError("TUBELEX compressed source-size limit exceeded")
    source_digest = sha256_file(source)
    if expected_source_bytes is not None and source_bytes != expected_source_bytes:
        raise ValueError(
            f"Source size mismatch: expected {expected_source_bytes}, got {source_bytes}"
        )
    if expected_digest is not None and source_digest != expected_digest:
        raise ValueError(
            f"Source SHA-256 mismatch: expected {expected_digest}, got {source_digest}"
        )

    source_records, categories, totals, parse_stats = _parse_frequency_table(
        source,
        delimiter="\t",
        compression=compression,
        quoting=csv.QUOTE_NONE,
        max_decompressed_bytes=max_decompressed_bytes,
        max_line_bytes=max_line_bytes,
        max_rows=max_rows,
        max_columns=max_columns,
        max_word_chars=max_word_chars,
        max_integer=max_integer,
        require_sorted=False,
        require_complete_totals=require_complete_totals,
    )
    records = [record for record in source_records if is_lookup_compatible_word(record.word)]
    if not records:
        raise ValueError("TUBELEX lookup filter retained no word rows")
    retained_token_mass = sum(record.count for record in records)
    filter_stats: dict[str, int | float | str] = {
        "source_vocabulary_size": len(source_records),
        "source_rows": len(source_records),
        "retained_rows": len(records),
        "excluded_rows": len(source_records) - len(records),
        "retained_token_mass": retained_token_mass,
        "retained_token_mass_ratio": (
            retained_token_mass / totals.count if totals.count else 0.0
        ),
    }

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    artifact = _write_artifact(
        output / artifact_name,
        records=records,
        categories=categories,
        totals=totals,
    )
    manifest = make_tubelex_manifest(
        resource_id=resource_id,
        source_version=source_version,
        source_asset=source_asset,
        source_url=source_url,
        license_name=license_name,
        license_spdx=license_spdx,
        license_url=license_url,
        acquired_on=acquisition_date,
        source_bytes=source_bytes,
        source_sha256=source_digest,
        expected_source_sha256=expected_digest,
        artifact=artifact,
        categories=categories,
        totals=totals,
        parse_stats=parse_stats,
        filter_stats=filter_stats,
        require_complete_totals=require_complete_totals,
    )
    notice = tubelex_notice_text(
        source_version=source_version,
        source_url=source_url,
        license_name=license_name,
        license_url=license_url,
    )
    _atomic_write_bytes(output / "NOTICE.md", notice.encode("utf-8"))
    _atomic_write_bytes(
        output / "manifest.json",
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    return manifest


# Singular alias for callers that regard the output as one lookup artifact.
build_tubelex_artifact = build_tubelex_aggregates


def load_tubelex_index(
    artifact_path: str | os.PathLike[str],
    *,
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
    max_decompressed_bytes: int = DEFAULT_MAX_DECOMPRESSED_BYTES,
    max_line_bytes: int = DEFAULT_MAX_LINE_BYTES,
    max_rows: int = DEFAULT_MAX_ROWS,
    max_columns: int = DEFAULT_MAX_COLUMNS,
    max_word_chars: int = DEFAULT_MAX_WORD_CHARS,
    max_integer: int = DEFAULT_MAX_INTEGER,
    source_vocabulary_size: int | None = None,
    metadata: dict[str, object] | None = None,
) -> TubelexIndex:
    """Load a validated deterministic CSV.gz artifact into a runtime index."""

    artifact = Path(artifact_path)
    if not artifact.is_file():
        raise FileNotFoundError(f"TUBELEX artifact does not exist: {artifact}")
    if not artifact.name.casefold().endswith(".csv.gz"):
        raise ValueError("TUBELEX runtime artifact must be a .csv.gz file")
    _validate_positive_limits(
        max_source_bytes=max_source_bytes,
        max_decompressed_bytes=max_decompressed_bytes,
        max_line_bytes=max_line_bytes,
        max_rows=max_rows,
        max_columns=max_columns,
        max_word_chars=max_word_chars,
        max_integer=max_integer,
    )
    if artifact.stat().st_size > max_source_bytes:
        raise ValueError("TUBELEX artifact compressed-size limit exceeded")

    # gzip.GzipFile is selected inside a small adapter because the bounded line
    # reader otherwise only needs to distinguish xz from plain input.
    records, categories, totals, _stats = _parse_gzip_artifact(
        artifact,
        max_decompressed_bytes=max_decompressed_bytes,
        max_line_bytes=max_line_bytes,
        max_rows=max_rows,
        max_columns=max_columns,
        max_word_chars=max_word_chars,
        max_integer=max_integer,
    )
    return TubelexIndex(
        records,
        categories=categories,
        totals=totals,
        source_vocabulary_size=source_vocabulary_size,
        metadata=metadata,
    )


def _iter_bounded_gzip_lines(
    path: Path,
    *,
    max_decompressed_bytes: int,
    max_line_bytes: int,
) -> Iterator[bytes]:
    total_bytes = 0
    try:
        with gzip.open(path, "rb") as binary:
            while True:
                line = binary.readline(max_line_bytes + 1)
                if not line:
                    break
                if len(line) > max_line_bytes:
                    raise ValueError("TUBELEX artifact line-size limit exceeded")
                total_bytes += len(line)
                if total_bytes > max_decompressed_bytes:
                    raise ValueError("TUBELEX artifact decompressed-size limit exceeded")
                yield line
    except (EOFError, gzip.BadGzipFile, OSError, zlib.error) as exc:
        if isinstance(exc, ValueError):
            raise
        raise ValueError("TUBELEX artifact is not a valid gzip file") from exc


def _parse_gzip_artifact(
    path: Path,
    *,
    max_decompressed_bytes: int,
    max_line_bytes: int,
    max_rows: int,
    max_columns: int,
    max_word_chars: int,
    max_integer: int,
) -> tuple[list[TubelexRecord], tuple[str, ...], TubelexRecord, dict[str, int]]:
    # Reuse the central validator by presenting a temporary iterator-compatible
    # path adapter would complicate error handling.  This compact local parser
    # swaps in the gzip line source while retaining identical row semantics.
    rows = _iter_rows_from_binary_iterator(
        _iter_bounded_gzip_lines(
            path,
            max_decompressed_bytes=max_decompressed_bytes,
            max_line_bytes=max_line_bytes,
        ),
        delimiter=",",
        quoting=csv.QUOTE_MINIMAL,
    )
    return _parse_rows(
        rows,
        max_rows=max_rows,
        max_columns=max_columns,
        max_word_chars=max_word_chars,
        max_integer=max_integer,
        require_sorted=True,
        require_complete_totals=False,
        enforce_lookup_filter=True,
    )


def _iter_rows_from_binary_iterator(
    binary_lines: Iterable[bytes],
    *,
    delimiter: str,
    quoting: int,
) -> Iterator[tuple[int, list[str]]]:
    for line_number, raw_line in enumerate(binary_lines, start=1):
        try:
            encoding = "utf-8-sig" if line_number == 1 else "utf-8"
            decoded = raw_line.decode(encoding, errors="strict")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"TUBELEX artifact is not valid UTF-8 at row {line_number}"
            ) from exc
        decoded = decoded.removesuffix("\n").removesuffix("\r")
        try:
            parsed = list(
                csv.reader(
                    [decoded],
                    delimiter=delimiter,
                    quoting=quoting,
                    strict=True,
                )
            )
        except csv.Error as exc:
            raise ValueError(f"Malformed TUBELEX artifact row {line_number}") from exc
        if len(parsed) != 1 or not parsed[0]:
            raise ValueError(f"Empty TUBELEX artifact row at row {line_number}")
        yield line_number, parsed[0]


def _parse_rows(
    row_iterator: Iterator[tuple[int, list[str]]],
    *,
    max_rows: int,
    max_columns: int,
    max_word_chars: int,
    max_integer: int,
    require_sorted: bool,
    require_complete_totals: bool,
    enforce_lookup_filter: bool,
) -> tuple[list[TubelexRecord], tuple[str, ...], TubelexRecord, dict[str, int]]:
    """Validate already-decoded rows (used for the generated CSV artifact)."""

    try:
        header_row, header = next(row_iterator)
    except StopIteration as exc:
        raise ValueError("TUBELEX artifact is empty") from exc
    if header_row != 1:
        raise AssertionError("The first parsed artifact row must be row one")
    columns, categories = _validate_header(header, max_columns=max_columns)

    records: list[TubelexRecord] = []
    seen_words: set[str] = set()
    total_record: TubelexRecord | None = None
    included_count = 0
    included_categories = [0] * len(categories)
    previous_word: str | None = None
    physical_rows = 1

    for row_number, fields in row_iterator:
        physical_rows = row_number
        if len(fields) != len(columns):
            raise ValueError(
                f"TUBELEX artifact row {row_number} has an unexpected field count"
            )
        word = fields[0]
        if not word or len(word) > max_word_chars:
            raise ValueError(f"Invalid word at TUBELEX artifact row {row_number}")
        if any(character in word for character in ("\x00", "\t", "\r", "\n")):
            raise ValueError(f"Invalid word at TUBELEX artifact row {row_number}")
        numeric = tuple(
            _parse_nonnegative_integer(
                value,
                row_number=row_number,
                max_integer=max_integer,
            )
            for value in fields[1:]
        )
        count, videos, channels, *raw_categories = numeric
        category_counts = tuple(raw_categories)
        if channels > videos or videos > count:
            raise ValueError(
                f"TUBELEX artifact row {row_number} violates count invariants"
            )
        if sum(category_counts) != count:
            raise ValueError(
                f"TUBELEX artifact row {row_number} violates category totals"
            )
        record = TubelexRecord(word, count, videos, channels, category_counts)
        if word == TOTAL_WORD:
            if total_record is not None:
                raise ValueError("TUBELEX artifact contains more than one [TOTAL] row")
            total_record = record
            continue
        if len(records) >= max_rows:
            raise ValueError("TUBELEX artifact row limit exceeded")
        if enforce_lookup_filter and not is_lookup_compatible_word(word):
            raise ValueError("TUBELEX artifact contains a non-lookup-compatible word")
        if word in seen_words:
            raise ValueError("Duplicate word in TUBELEX artifact")
        if require_sorted and previous_word is not None and word <= previous_word:
            raise ValueError("TUBELEX artifact words are not in canonical sorted order")
        previous_word = word
        seen_words.add(word)
        records.append(record)
        included_count += count
        for index, value in enumerate(category_counts):
            included_categories[index] += value

    if total_record is None:
        raise ValueError("TUBELEX artifact is missing its [TOTAL] row")
    if not records:
        raise ValueError("TUBELEX artifact contains no word rows")
    if included_count > total_record.count:
        raise ValueError("TUBELEX artifact word counts exceed the declared total")
    if any(
        subtotal > total
        for subtotal, total in zip(included_categories, total_record.category_counts)
    ):
        raise ValueError("TUBELEX artifact category counts exceed declared totals")
    if require_complete_totals and included_count != total_record.count:
        raise ValueError("TUBELEX artifact word counts do not match the declared total")
    if require_complete_totals and tuple(included_categories) != total_record.category_counts:
        raise ValueError("TUBELEX artifact category counts do not match declared totals")
    return records, categories, total_record, {
        "physical_rows": physical_rows,
        "word_rows": len(records),
        "columns": len(columns),
        "included_token_mass": included_count,
    }


def load_verified_tubelex_index(
    artifact_path: str | os.PathLike[str],
    *,
    expected_artifact_sha256: str | None = None,
    expected_artifact_bytes: int | None = None,
    expected_source_sha256: str | None = None,
    expected_resource_id: str | None = None,
    max_manifest_bytes: int = DEFAULT_MAX_MANIFEST_BYTES,
    **load_limits: int,
) -> TubelexIndex:
    """Load an artifact only after validating its adjacent manifest and pins.

    An externally supplied artifact or source checksum protects against joint
    replacement of both artifact and manifest.  Without such a pin this still
    validates internal integrity, schema, totals, and manifest consistency, but
    does not establish provenance by itself.
    """

    artifact = Path(artifact_path)
    manifest_path = artifact.with_name("manifest.json")
    if not artifact.is_file():
        raise FileNotFoundError(f"TUBELEX artifact does not exist: {artifact}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"TUBELEX manifest does not exist: {manifest_path}")
    _validate_positive_limits(max_manifest_bytes=max_manifest_bytes)
    if manifest_path.stat().st_size > max_manifest_bytes:
        raise ValueError("TUBELEX manifest size limit exceeded")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("TUBELEX manifest is not valid UTF-8 JSON") from exc
    if not isinstance(manifest, dict):
        raise ValueError("TUBELEX manifest must be a JSON object")
    source = manifest.get("source")
    artifact_metadata = manifest.get("artifact")
    schema = manifest.get("schema")
    totals = manifest.get("totals")
    build = manifest.get("build")
    if not all(
        isinstance(value, dict)
        for value in (source, artifact_metadata, schema, totals, build)
    ):
        raise ValueError(
            "TUBELEX manifest is missing source, artifact, schema, totals, or build"
        )
    lookup_filter = build.get("lookup_filter")
    if not isinstance(lookup_filter, dict):
        raise ValueError("TUBELEX manifest is missing its lookup-filter metadata")

    expected_artifact_digest = _validated_sha256(expected_artifact_sha256)
    expected_source_digest = _validated_sha256(expected_source_sha256)
    if expected_artifact_bytes is not None and (
        isinstance(expected_artifact_bytes, bool)
        or not isinstance(expected_artifact_bytes, int)
        or expected_artifact_bytes < 0
    ):
        raise ValueError("Expected artifact size must be a non-negative integer")
    actual_artifact_bytes = artifact.stat().st_size
    actual_artifact_digest = sha256_file(artifact)
    if artifact_metadata.get("file") != artifact.name:
        raise ValueError("TUBELEX manifest artifact filename mismatch")
    if artifact_metadata.get("bytes") != actual_artifact_bytes:
        raise ValueError("TUBELEX manifest artifact size mismatch")
    if artifact_metadata.get("sha256") != actual_artifact_digest:
        raise ValueError("TUBELEX manifest artifact SHA-256 mismatch")
    if expected_artifact_bytes is not None and actual_artifact_bytes != expected_artifact_bytes:
        raise ValueError("TUBELEX artifact does not match the externally pinned size")
    if expected_artifact_digest is not None and actual_artifact_digest != expected_artifact_digest:
        raise ValueError("TUBELEX artifact does not match the externally pinned SHA-256")
    if expected_source_digest is not None and source.get("sha256") != expected_source_digest:
        raise ValueError("TUBELEX source does not match the externally pinned SHA-256")
    if expected_resource_id is not None and manifest.get("id") != expected_resource_id:
        raise ValueError("TUBELEX manifest resource ID mismatch")

    index = load_tubelex_index(
        artifact,
        metadata=manifest,
        **load_limits,
    )
    if artifact_metadata.get("rows") != len(index):
        raise ValueError("TUBELEX manifest artifact row-count mismatch")
    manifest_categories = schema.get("categories")
    if manifest_categories != list(index.categories):
        raise ValueError("TUBELEX manifest category schema mismatch")
    if artifact_metadata.get("columns") != [
        *BASE_COLUMNS,
        *(f"count:{category}" for category in index.categories),
    ]:
        raise ValueError("TUBELEX manifest artifact columns mismatch")
    expected_totals = {
        "count": index.totals.count,
        "videos": index.totals.videos,
        "channels": index.totals.channels,
        "category_counts": dict(zip(index.categories, index.totals.category_counts)),
    }
    if totals != expected_totals:
        raise ValueError("TUBELEX manifest total-count mismatch")
    source_vocabulary_size = lookup_filter.get("source_vocabulary_size")
    source_rows = lookup_filter.get("source_rows")
    retained_rows = lookup_filter.get("retained_rows")
    excluded_rows = lookup_filter.get("excluded_rows")
    retained_token_mass = lookup_filter.get("retained_token_mass")
    retained_ratio = lookup_filter.get("retained_token_mass_ratio")
    if (
        source_vocabulary_size != index.source_vocabulary_size
        or source_rows != index.source_vocabulary_size
        or retained_rows != len(index)
        or excluded_rows != index.source_vocabulary_size - len(index)
        or retained_token_mass != index.retained_token_mass
        or not isinstance(retained_ratio, (int, float))
        or isinstance(retained_ratio, bool)
        or not math.isclose(
            float(retained_ratio),
            index.retained_token_mass / index.totals.count if index.totals.count else 0.0,
            rel_tol=0.0,
            abs_tol=1e-15,
        )
    ):
        raise ValueError("TUBELEX manifest lookup-filter statistics mismatch")
    return index


def _smoothed_record_metrics(
    record: TubelexRecord | None,
    *,
    totals: TubelexRecord,
    source_vocabulary_size: int,
) -> tuple[float, float, float]:
    """Return the three public, smoothed TUBELEX measures for one type."""

    count = record.count if record is not None else 0
    videos = record.videos if record is not None else 0
    channels = record.channels if record is not None else 0
    frequency_zipf = math.log10(
        1_000_000_000
        * (count + 1)
        / (totals.count + source_vocabulary_size)
    )
    # Beta(1, 1) posterior means prevent unseen items from disappearing from
    # document-level averages while keeping coverage as a separate diagnostic.
    video_log10_prevalence = math.log10((videos + 1) / (totals.videos + 2))
    channel_log10_prevalence = math.log10(
        (channels + 1) / (totals.channels + 2)
    )
    return frequency_zipf, video_log10_prevalence, channel_log10_prevalence


def summarize_tubelex_tokens(
    tokens: Iterable[str], index: TubelexIndex
) -> dict[str, int | float | None]:
    """Aggregate the three public TUBELEX measures for one token sequence.

    Inputs are first aligned to the pinned Treebank adapter.  Add-one frequency
    and Beta(1,1) video/channel prevalence give every observation, including an
    unseen one, a finite value.  Coverage remains separate and makes OOV rates
    explicit.  Category entropy is intentionally not reported because TUBELEX
    category base sizes are unequal.  No input text or token value is returned
    or retained.
    """

    return _summarize_aligned_tokens(adapt_tubelex_tokens(tokens), index)


def _summarize_aligned_tokens(
    aligned_tokens: Iterable[str], index: TubelexIndex
) -> dict[str, int | float | None]:
    """Aggregate tokens already emitted by :func:`tokenize_tubelex_text`."""

    frequencies = Counter(aligned_tokens)
    token_total = sum(frequencies.values())
    type_total = len(frequencies)
    covered_tokens = 0
    covered_types = 0
    token_sums = [0.0, 0.0, 0.0]
    type_sums = [0.0, 0.0, 0.0]

    for token, multiplicity in frequencies.items():
        record = index.lookup(token)
        if record is not None:
            covered_tokens += multiplicity
            covered_types += 1
        values = _smoothed_record_metrics(
            record,
            totals=index.totals,
            source_vocabulary_size=index.source_vocabulary_size,
        )
        for metric_index, value in enumerate(values):
            token_sums[metric_index] += value * multiplicity
            type_sums[metric_index] += value

    result: dict[str, int | float | None] = {
        "tokens": token_total,
        "types": type_total,
        "covered_tokens": covered_tokens,
        "covered_types": covered_types,
        "token_coverage": covered_tokens / token_total if token_total else 0.0,
        "type_coverage": covered_types / type_total if type_total else 0.0,
    }
    metric_names = (
        "frequency_zipf",
        "video_log10_prevalence",
        "channel_log10_prevalence",
    )
    for metric_index, name in enumerate(metric_names):
        result[f"{name}_token_mean"] = (
            token_sums[metric_index] / token_total
            if token_total
            else None
        )
        result[f"{name}_type_mean"] = (
            type_sums[metric_index] / type_total
            if type_total
            else None
        )
    return result


def summarize_tubelex_text(
    text: str, index: TubelexIndex
) -> dict[str, int | float | None]:
    """Tokenize one complete text once and aggregate its TUBELEX measures."""

    return _summarize_aligned_tokens(tokenize_tubelex_text(text), index)


def aggregate_tubelex_document(
    document: str | Iterable[str], index: TubelexIndex
) -> dict[str, int | float | None]:
    """Aggregate a full text string, or a legacy iterable of application tokens."""

    if isinstance(document, str):
        return summarize_tubelex_text(document, index)
    return summarize_tubelex_tokens(document, index)
