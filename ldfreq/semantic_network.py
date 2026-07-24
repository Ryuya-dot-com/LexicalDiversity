"""Open lexical-network metrics used by the lexical sophistication analyzer.

The runtime API reads a compact, derived lemma table.  The original Open
English WordNet XML is only needed by :func:`build_oewn_lemma_artifact`, so a
web deployment does not need to ship or parse the 89 MB source XML.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from statistics import fmean
from typing import Iterable


ARTIFACT_COLUMNS = (
    "lemma",
    "pos",
    "polysemy",
    "depth_sense_count",
    "hypernym_depth_min",
    "hypernym_depth_mean",
    "hypernym_depth_max",
)

DEFAULT_ARTIFACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "open"
    / "open_english_wordnet"
    / "2025"
    / "open_english_wordnet_2025_lemma_metrics.csv.gz"
)
OEWN_VERSION = "2025"
OEWN_RELEASE_TAG = "2025-edition"
OEWN_RELEASE_COMMIT = "dc343f2683279ecbb13fab4e2fd778d7b162d287"
OEWN_RELEASE_PUBLISHED_AT = "2025-12-31T07:29:46Z"
OEWN_RELEASE_URL = (
    "https://github.com/globalwordnet/english-wordnet/releases/tag/2025-edition"
)
OEWN_SOURCE_NAME = "english-wordnet-2025.xml.gz"
OEWN_SOURCE_URL = (
    "https://github.com/globalwordnet/english-wordnet/releases/download/"
    "2025-edition/english-wordnet-2025.xml.gz"
)
OEWN_SOURCE_SIZE = 11_363_503
OEWN_SOURCE_SHA256 = "9ca6d1dcb75f822fdd66617f7d9da48142ace38dd544d6ad5e2feca1674ad3fe"
OEWN_LICENSE = "Creative Commons Attribution 4.0 International"
OEWN_LICENSE_URL = "https://creativecommons.org/licenses/by/4.0/"
PRODUCTION_ARTIFACT_BYTES = 770_980
PRODUCTION_ARTIFACT_ROWS = 135_282
PRODUCTION_ARTIFACT_SHA256 = (
    "ec8a1f74aaca49f58129f6dd7a8f22eb3aa1f208bb15b9809e42c9b6589e9bea"
)

_POS_ALIASES = {
    "n": "n",
    "noun": "n",
    "v": "v",
    "verb": "v",
    "a": "a",
    "s": "a",
    "adj": "a",
    "adjective": "a",
    "r": "r",
    "adv": "r",
    "adverb": "r",
}


def normalize_lemma(value: str) -> str:
    """Normalize a lookup lemma without retaining the original text."""

    normalized = unicodedata.normalize("NFKC", str(value)).strip().casefold()
    normalized = normalized.replace("_", " ")
    return " ".join(normalized.split())


def normalize_pos(value: str) -> str:
    """Return the one-letter Open English WordNet POS code."""

    key = str(value).strip().casefold()
    try:
        return _POS_ALIASES[key]
    except KeyError as exc:
        raise ValueError(f"Unsupported part of speech: {value!r}") from exc


@dataclass(frozen=True, slots=True)
class LemmaNetworkMetrics:
    """Polysemy and hypernym-depth metrics for one normalized lemma.

    ``hypernym_depth_*`` are based on the longest hypernym path from each
    noun/verb sense to a root (root depth is zero).  Depth is unavailable for
    adjectives and adverbs because they do not use the noun/verb hypernym
    hierarchy.
    """

    lemma: str
    pos: str | None
    polysemy: int
    depth_sense_count: int
    hypernym_depth_min: float | None
    hypernym_depth_mean: float | None
    hypernym_depth_max: float | None


class SemanticNetworkIndex:
    """In-memory lookup over the compact Open English WordNet artifact."""

    def __init__(self, records: Iterable[LemmaNetworkMetrics]):
        self._by_key: dict[tuple[str, str], LemmaNetworkMetrics] = {}
        self._by_lemma: dict[str, list[LemmaNetworkMetrics]] = defaultdict(list)
        for record in records:
            if record.pos is None:
                raise ValueError("Artifact records must have a part of speech")
            key = (record.lemma, record.pos)
            if key in self._by_key:
                raise ValueError(f"Duplicate lemma/POS row in artifact: {key!r}")
            self._by_key[key] = record
            self._by_lemma[record.lemma].append(record)
        self._lemmas = frozenset(self._by_lemma)

    def __len__(self) -> int:
        return len(self._by_key)

    @property
    def lemmas(self) -> frozenset[str]:
        """Return the immutable normalized head vocabulary in the artifact."""

        return self._lemmas

    def lookup(self, lemma: str, pos: str | None = None) -> LemmaNetworkMetrics | None:
        """Look up one lemma, optionally restricted to a part of speech.

        Without ``pos``, sense counts are summed across parts of speech and
        mean depth is weighted by the number of senses with a depth value.
        """

        normalized = normalize_lemma(lemma)
        if pos is not None:
            return self._by_key.get((normalized, normalize_pos(pos)))

        records = self._by_lemma.get(normalized)
        if not records:
            return None
        if len(records) == 1:
            return records[0]

        depth_records = [r for r in records if r.depth_sense_count]
        depth_count = sum(r.depth_sense_count for r in depth_records)
        if depth_count:
            depth_mean = sum(
                (r.hypernym_depth_mean or 0.0) * r.depth_sense_count
                for r in depth_records
            ) / depth_count
            depth_min = min(
                r.hypernym_depth_min
                for r in depth_records
                if r.hypernym_depth_min is not None
            )
            depth_max = max(
                r.hypernym_depth_max
                for r in depth_records
                if r.hypernym_depth_max is not None
            )
        else:
            depth_min = depth_mean = depth_max = None

        return LemmaNetworkMetrics(
            lemma=normalized,
            pos=None,
            polysemy=sum(r.polysemy for r in records),
            depth_sense_count=depth_count,
            hypernym_depth_min=depth_min,
            hypernym_depth_mean=depth_mean,
            hypernym_depth_max=depth_max,
        )


def _open_text(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", newline="")
    return path.open("r", encoding="utf-8", newline="")


def load_semantic_network_index(
    path: str | os.PathLike[str] = DEFAULT_ARTIFACT_PATH,
) -> SemanticNetworkIndex:
    """Load a deterministic OEWN lemma-metrics CSV or CSV.gz artifact."""

    artifact_path = Path(path)
    records: list[LemmaNetworkMetrics] = []
    with _open_text(artifact_path) as fh:
        reader = csv.DictReader(fh)
        if tuple(reader.fieldnames or ()) != ARTIFACT_COLUMNS:
            raise ValueError(
                f"Unexpected semantic-network artifact columns: {reader.fieldnames!r}"
            )
        for row in reader:
            depth_count = int(row["depth_sense_count"])
            records.append(
                LemmaNetworkMetrics(
                    lemma=row["lemma"],
                    pos=row["pos"],
                    polysemy=int(row["polysemy"]),
                    depth_sense_count=depth_count,
                    hypernym_depth_min=(
                        float(row["hypernym_depth_min"]) if depth_count else None
                    ),
                    hypernym_depth_mean=(
                        float(row["hypernym_depth_mean"]) if depth_count else None
                    ),
                    hypernym_depth_max=(
                        float(row["hypernym_depth_max"]) if depth_count else None
                    ),
                )
            )
    return SemanticNetworkIndex(records)


def load_verified_semantic_network_index(
    path: str | os.PathLike[str] = DEFAULT_ARTIFACT_PATH,
) -> SemanticNetworkIndex:
    """Fail closed unless artifact and adjacent manifest match the pinned build."""

    artifact_path = Path(path)
    manifest_path = artifact_path.with_name("manifest.json")
    if not artifact_path.is_file():
        raise FileNotFoundError(f"OEWN artifact does not exist: {artifact_path}")
    if not manifest_path.is_file():
        raise FileNotFoundError(f"OEWN manifest does not exist: {manifest_path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("OEWN manifest must be a JSON object")
    source = manifest.get("source")
    artifact = manifest.get("artifact")
    if not isinstance(source, dict) or not isinstance(artifact, dict):
        raise ValueError("OEWN manifest is missing source or artifact metadata")
    if (
        manifest.get("id") != "open_english_wordnet_2025_lemma_metrics"
        or manifest.get("version") != OEWN_VERSION
        or manifest.get("release_tag") != OEWN_RELEASE_TAG
        or manifest.get("release_commit") != OEWN_RELEASE_COMMIT
        or manifest.get("license_url") != OEWN_LICENSE_URL
        or source.get("asset") != OEWN_SOURCE_NAME
        or source.get("url") != OEWN_SOURCE_URL
        or source.get("bytes") != OEWN_SOURCE_SIZE
        or source.get("sha256") != OEWN_SOURCE_SHA256
    ):
        raise ValueError("OEWN manifest does not match the pinned official source")
    if (
        artifact.get("file") != artifact_path.name
        or artifact.get("bytes") != PRODUCTION_ARTIFACT_BYTES
        or artifact.get("rows") != PRODUCTION_ARTIFACT_ROWS
        or artifact.get("sha256") != PRODUCTION_ARTIFACT_SHA256
    ):
        raise ValueError("OEWN manifest does not match the pinned production artifact")
    if artifact_path.stat().st_size != PRODUCTION_ARTIFACT_BYTES:
        raise ValueError("OEWN production artifact size mismatch")
    if sha256_file(artifact_path) != PRODUCTION_ARTIFACT_SHA256:
        raise ValueError("OEWN production artifact SHA-256 mismatch")

    index = load_semantic_network_index(artifact_path)
    if len(index) != PRODUCTION_ARTIFACT_ROWS:
        raise ValueError("OEWN production artifact row count mismatch")
    return index


def summarize_lemmas(
    lemmas: Iterable[str],
    index: SemanticNetworkIndex,
    *,
    pos: str | None = None,
) -> dict[str, int | float | None]:
    """Return token- and type-weighted open semantic-network indices."""

    normalized = [normalize_lemma(lemma) for lemma in lemmas]
    normalized = [lemma for lemma in normalized if lemma]
    token_records = [index.lookup(lemma, pos=pos) for lemma in normalized]
    covered_tokens = [record for record in token_records if record is not None]
    unique_lemmas = sorted(set(normalized))
    type_records = [index.lookup(lemma, pos=pos) for lemma in unique_lemmas]
    covered_types = [record for record in type_records if record is not None]

    token_depths = [
        record.hypernym_depth_mean
        for record in covered_tokens
        if record.hypernym_depth_mean is not None
    ]
    type_depths = [
        record.hypernym_depth_mean
        for record in covered_types
        if record.hypernym_depth_mean is not None
    ]

    return {
        "tokens": len(normalized),
        "types": len(unique_lemmas),
        "covered_tokens": len(covered_tokens),
        "covered_types": len(covered_types),
        "token_coverage": len(covered_tokens) / len(normalized) if normalized else 0.0,
        "type_coverage": len(covered_types) / len(unique_lemmas) if unique_lemmas else 0.0,
        # Polysemy means are conditional on OEWN coverage.  Hypernym-depth
        # means have a narrower denominator again: only matched lookup units
        # whose noun/verb senses have an available hypernym path.  Expose both
        # eligible counts and all-input coverage so callers cannot silently
        # present the depth means as if every lookup unit contributed.
        "depth_covered_tokens": len(token_depths),
        "depth_covered_types": len(type_depths),
        "depth_token_coverage": (
            len(token_depths) / len(normalized) if normalized else 0.0
        ),
        "depth_type_coverage": (
            len(type_depths) / len(unique_lemmas) if unique_lemmas else 0.0
        ),
        "polysemy_token_mean": (
            fmean(record.polysemy for record in covered_tokens)
            if covered_tokens
            else None
        ),
        "polysemy_type_mean": (
            fmean(record.polysemy for record in covered_types)
            if covered_types
            else None
        ),
        "hypernym_depth_token_mean": fmean(token_depths) if token_depths else None,
        "hypernym_depth_type_mean": fmean(type_depths) if type_depths else None,
    }


def sha256_file(path: str | os.PathLike[str]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_oewn(
    source_path: Path,
) -> tuple[
    dict[tuple[str, str], set[str]],
    dict[str, str],
    dict[str, tuple[str, ...]],
    int,
]:
    lemma_senses: dict[tuple[str, str], set[str]] = defaultdict(set)
    synset_pos: dict[str, str] = {}
    hypernyms: dict[str, tuple[str, ...]] = {}
    lexical_entries = 0

    with _open_text(source_path) as source:
        for _event, elem in ET.iterparse(source, events=("end",)):
            tag = _local_name(elem.tag)
            if tag == "LexicalEntry":
                lemma_node = next(
                    (child for child in elem if _local_name(child.tag) == "Lemma"),
                    None,
                )
                if lemma_node is not None:
                    lemma = normalize_lemma(lemma_node.attrib.get("writtenForm", ""))
                    raw_pos = lemma_node.attrib.get("partOfSpeech", "")
                    if lemma and raw_pos:
                        lexical_entries += 1
                        pos = normalize_pos(raw_pos)
                        senses = {
                            child.attrib["synset"]
                            for child in elem
                            if _local_name(child.tag) == "Sense"
                            and child.attrib.get("synset")
                        }
                        lemma_senses[(lemma, pos)].update(senses)
                elem.clear()
            elif tag == "Synset":
                synset_id = elem.attrib.get("id")
                raw_pos = elem.attrib.get("partOfSpeech")
                if synset_id and raw_pos:
                    synset_pos[synset_id] = normalize_pos(raw_pos)
                    parents = tuple(
                        child.attrib["target"]
                        for child in elem
                        if _local_name(child.tag) == "SynsetRelation"
                        and child.attrib.get("relType")
                        in {"hypernym", "instance_hypernym"}
                        and child.attrib.get("target")
                    )
                    if parents:
                        hypernyms[synset_id] = parents
                elem.clear()

    missing = sorted(
        {
            synset
            for senses in lemma_senses.values()
            for synset in senses
            if synset not in synset_pos
        }
    )
    if missing:
        preview = ", ".join(missing[:5])
        raise ValueError(f"Lexical entries reference missing synsets: {preview}")
    return lemma_senses, synset_pos, hypernyms, lexical_entries


def _maximum_hypernym_depths(
    synset_pos: dict[str, str],
    hypernyms: dict[str, tuple[str, ...]],
) -> dict[str, int]:
    memo: dict[str, int] = {}
    visiting: set[str] = set()

    def visit(synset: str) -> int:
        if synset in memo:
            return memo[synset]
        if synset in visiting:
            raise ValueError(f"Cycle in OEWN hypernym graph at {synset}")
        visiting.add(synset)
        parents = [parent for parent in hypernyms.get(synset, ()) if parent in synset_pos]
        depth = 0 if not parents else 1 + max(visit(parent) for parent in parents)
        visiting.remove(synset)
        memo[synset] = depth
        return depth

    for synset, pos in synset_pos.items():
        if pos in {"n", "v"}:
            visit(synset)
    return memo


def _format_depth(value: float | None) -> str:
    if value is None:
        return ""
    if value.is_integer():
        return str(int(value))
    return f"{value:.6f}".rstrip("0").rstrip(".")


def build_oewn_lemma_artifact(
    source_path: str | os.PathLike[str],
    artifact_path: str | os.PathLike[str],
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> dict[str, int | str]:
    """Build a deterministic, gzip-compressed OEWN lemma metrics table."""

    source = Path(source_path)
    source_size = source.stat().st_size
    source_sha256 = sha256_file(source)
    if expected_size is not None and source_size != expected_size:
        raise ValueError(f"Source size mismatch: expected {expected_size}, got {source_size}")
    if expected_sha256 is not None and source_sha256 != expected_sha256:
        raise ValueError(
            f"Source SHA-256 mismatch: expected {expected_sha256}, got {source_sha256}"
        )

    lemma_senses, synset_pos, hypernyms, lexical_entries = _parse_oewn(source)
    depths = _maximum_hypernym_depths(synset_pos, hypernyms)

    destination = Path(artifact_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    depth_rows = 0
    sense_links = 0
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8", newline="") as text:
                    writer = csv.writer(text, lineterminator="\n")
                    writer.writerow(ARTIFACT_COLUMNS)
                    for (lemma, pos), senses in sorted(lemma_senses.items()):
                        sense_links += len(senses)
                        sense_depths = sorted(
                            depths[synset]
                            for synset in senses
                            if pos in {"n", "v"} and synset in depths
                        )
                        if sense_depths:
                            depth_rows += 1
                            minimum = float(min(sense_depths))
                            mean = fmean(sense_depths)
                            maximum = float(max(sense_depths))
                        else:
                            minimum = mean = maximum = None
                        writer.writerow(
                            (
                                lemma,
                                pos,
                                len(senses),
                                len(sense_depths),
                                _format_depth(minimum),
                                _format_depth(mean),
                                _format_depth(maximum),
                            )
                        )
        os.replace(temporary, destination)
        destination.chmod(0o644)
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "source_bytes": source_size,
        "source_sha256": source_sha256,
        "artifact_bytes": destination.stat().st_size,
        "artifact_sha256": sha256_file(destination),
        "lemma_pos_rows": len(lemma_senses),
        "source_lexical_entries": lexical_entries,
        "lemma_sense_links": sense_links,
        "depth_rows": depth_rows,
        "synsets": len(synset_pos),
        "hypernym_edges": sum(len(values) for values in hypernyms.values()),
    }
