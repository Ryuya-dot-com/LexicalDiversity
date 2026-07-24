"""Framework-independent lexical analysis service.

The service deliberately has no dependency on Streamlit or another serving
framework.  Source text and token-level working data exist only inside a call;
the returned objects contain aggregate results and pseudonymous labels only.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Protocol

from . import OUTPUT_SCHEMA_VERSION, __version__
from . import batch as BATCH
from . import frequency as FRQ
from . import indices as IDX
from . import semantic_network as SEMANTIC
from . import tubelex as TUBELEX
from .exporting import clean_deep
from .lemmatizers import WordFormLemmatizer
from .privacy import retain_aggregate_result, sensitive_paths
from .tokenizer import tokenize


TOKENIZER_POLICY = (
    "ASCII letters plus internal apostrophes; numbers, hyphens, and periods "
    "split or drop."
)
SERVER_ONLY_MIN_TOKENS = 100
SERVER_ONLY_MIN_TYPES = 20


class Normalizer(Protocol):
    """Minimal normalizer contract required by the analysis core."""

    name: str
    version: str

    def normalize(self, token: str) -> str: ...


@dataclass(frozen=True, slots=True)
class AnalysisConfig:
    """Typed, serializable choices that determine numerical analysis.

    Runtime resources (rank maps, lexical-network indexes, and normalizers) are
    injected separately through :class:`AnalysisResources`.  This keeps config
    suitable for request validation and reproducibility metadata.
    """

    thresholds: tuple[int, ...] = (90, 95, 98)
    min_tokens: int = 50
    msttr_segment: int = 50
    mattr_window: int = 50
    mtld_threshold: float = 0.72
    hdd_sample: int = 42
    vocd_seed: int = 42
    advanced_cutoff: int = 2
    unit: str = "token"
    tokenizer_policy: str = TOKENIZER_POLICY

    def __post_init__(self) -> None:
        object.__setattr__(self, "thresholds", tuple(self.thresholds))
        if self.unit != "token":
            raise ValueError("Only token counting is currently supported")
        if not self.thresholds or any(
            isinstance(value, bool) or not isinstance(value, int) or not 0 < value <= 100
            for value in self.thresholds
        ):
            raise ValueError("thresholds must contain integer percentages from 1 to 100")
        for name in ("min_tokens", "msttr_segment", "mattr_window", "hdd_sample"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.advanced_cutoff, bool)
            or not isinstance(self.advanced_cutoff, int)
            or self.advanced_cutoff < 0
        ):
            raise ValueError("advanced_cutoff must be a non-negative integer")
        if (
            isinstance(self.vocd_seed, bool)
            or not isinstance(self.vocd_seed, int)
            or self.vocd_seed < 0
        ):
            raise ValueError("vocd_seed must be a non-negative integer")
        if not 0 < float(self.mtld_threshold) < 1:
            raise ValueError("mtld_threshold must be between zero and one")


@dataclass(frozen=True, slots=True)
class AnalysisResources:
    """Preloaded, request-independent resources used by the service."""

    lemmatizer: Normalizer | None = None
    rank_map: Mapping[str, Any] | None = None
    list_meta: Mapping[str, Any] | None = None
    list_entry: Mapping[str, Any] | None = None
    list_path: str | Path | None = None
    semantic_index: SEMANTIC.SemanticNetworkIndex | None = None
    tubelex_index: TUBELEX.TubelexIndex | None = None

    def __post_init__(self) -> None:
        if (self.rank_map is None) != (self.list_meta is None):
            raise ValueError("rank_map and list_meta must be supplied together")
        if self.list_meta is not None:
            levels = self.list_meta.get("n_levels")
            if isinstance(levels, bool) or not isinstance(levels, int) or levels <= 0:
                raise ValueError("list_meta.n_levels must be a positive integer")


@dataclass(frozen=True, slots=True)
class TextDocument:
    """One request document.  The service assigns its own output label."""

    text: str


@dataclass(frozen=True, slots=True)
class AnalysisBatch:
    """Aggregate-only service response for one or more request documents."""

    results: tuple[dict[str, Any], ...]
    payload: dict[str, Any]
    skipped: tuple[dict[str, str], ...]


ProgressCallback = Callable[[int, int, str], None]


def _normalizer(resources: AnalysisResources) -> Normalizer:
    return resources.lemmatizer or WordFormLemmatizer()


def _normalizer_info(normalizer: Normalizer) -> dict[str, str]:
    return {
        "name": str(getattr(normalizer, "name", "unknown")),
        "version": str(getattr(normalizer, "version", "unknown")),
    }


def _safe_list_entry(entry: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    public_fields = (
        "id",
        "registry_id",
        "name",
        "license",
        "license_url",
        "source_url",
        "modification_notice",
        "unit",
        "redistributable",
        "public_web",
        "delivery_mode",
    )
    return clean_deep({key: entry.get(key) for key in public_fields if key in entry})


def _settings(
    config: AnalysisConfig,
    resources: AnalysisResources,
    normalizer: Normalizer,
) -> dict[str, Any]:
    entry = resources.list_entry or {}
    meta = resources.list_meta or {}
    delivery_mode = entry.get("delivery_mode", "bundled-public")
    return clean_deep({
        "unit": config.unit,
        "lemmatizer": getattr(normalizer, "name", "unknown"),
        "lemmatizer_version": getattr(normalizer, "version", "unknown"),
        "lemmatizer_scope": "Panel B lookup fallback",
        "list": (
            None
            if delivery_mode == "server-side-only"
            else Path(resources.list_path).name if resources.list_path else None
        ),
        "list_id": entry.get("id"),
        "list_registry_id": entry.get("registry_id"),
        "list_name": entry.get("name"),
        "list_license": entry.get("license"),
        "list_license_url": entry.get("license_url"),
        "list_source_url": entry.get("source_url"),
        "list_modification_notice": entry.get("modification_notice"),
        "list_delivery": delivery_mode,
        "server_only_min_tokens": (
            SERVER_ONLY_MIN_TOKENS if delivery_mode == "server-side-only" else None
        ),
        "server_only_min_types": (
            SERVER_ONLY_MIN_TYPES if delivery_mode == "server-side-only" else None
        ),
        "list_entries": meta.get("entries"),
        "list_levels": meta.get("n_levels"),
        "list_lookup_unit": meta.get("lookup_unit"),
        "tokenizer_policy": config.tokenizer_policy,
        "thresholds": list(config.thresholds),
        "min_tokens": config.min_tokens,
        "msttr_segment": config.msttr_segment,
        "mattr_window": config.mattr_window,
        "mtld_threshold": config.mtld_threshold,
        "hdd_sample": config.hdd_sample,
        "vocd_seed": config.vocd_seed,
        "advanced_cutoff": config.advanced_cutoff,
    })


def _method_notes(
    config: AnalysisConfig,
    resources: AnalysisResources,
    normalizer: Normalizer,
    panel_b_available: bool,
) -> list[str]:
    notes = [f"Tokenizer policy: {config.tokenizer_policy}"]
    notes.append(
        "Counting unit is token: Panel A uses lower-cased surface tokens without "
        "lemmatization."
    )
    if getattr(normalizer, "name", None) == "antbnc":
        notes.append(
            "AntBNC mode is an NWLC approximation, not bit-identical to New Word "
            "Level Checker."
        )
    if not panel_b_available:
        return notes

    if (resources.list_entry or {}).get("delivery_mode") == "server-side-only":
        notes.append(
            f"Server-only lookup requires at least {SERVER_ONLY_MIN_TOKENS} lexical "
            f"tokens and {SERVER_ONLY_MIN_TYPES} distinct surface types per document."
        )

    entry_id = (resources.list_entry or {}).get("id")
    if entry_id in {"bnc_coca_families", "nation_bnc_coca_families"}:
        notes.append(
            "Panel B maps tokens to BNC/COCA word-family heads when the selected "
            "family list contains the token/form."
        )
    elif entry_id == "range_baseword":
        notes.append(
            "Panel B maps tokens to Range/AntWordProfiler baseword-family heads "
            "when the selected level-list contains the token/form."
        )
    else:
        notes.append(
            "Panel B maps tokens to flemmas/head forms before frequency-list lookup."
        )
    notes.extend([
        "Coverage thresholds are selected-list matched coverage, not an automatic "
        "reader-known coverage estimate.",
        "Proper nouns, marginal words, acronyms, and other potentially known items "
        "are not automatically credited unless they match the selected list/normalizer.",
        "Coverage can differ from LexTutor unless the frequency list, word-family "
        "expansion, tokenizer, proper-noun/number policy, and lemmatizer all match.",
        "P_Lex counts unclassified off-list items as hard words under this app's "
        "no-automatic-proper-noun-adjustment policy.",
        "S uses the selected list's ranks, not Kojima & Yamashita's BNC-spoken "
        "family lists; values are not directly comparable to published S values.",
    ])
    return notes


def _document_payload(
    result: Mapping[str, Any],
    config: AnalysisConfig,
    resources: AnalysisResources,
    normalizer: Normalizer,
) -> dict[str, Any]:
    panel_b = result.get("panel_b")
    return clean_deep({
        "ldfreq_version": __version__,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "document": {"name": result["name"]},
        "settings": _settings(config, resources, normalizer),
        "method_notes": _method_notes(
            config,
            resources,
            normalizer,
            panel_b is not None,
        ),
        "privacy": {
            "source_text_retained": False,
            "source_filename_retained": False,
            "token_level_output_retained": False,
        },
        "n_tokens": result["n_tokens"],
        "n_types": result["n_types"],
        "panel_a": {key: result["indices"].get(key) for key in IDX._FUNCS},
        "panel_b": (
            {key: value for key, value in panel_b.items() if not str(key).startswith("_")}
            if panel_b is not None
            else None
        ),
        "semantic_network": result.get("semantic_network"),
        "tubelex": result.get("tubelex"),
    })


def _analyze_transient_document(
    text: str,
    label: str,
    config: AnalysisConfig,
    resources: AnalysisResources,
    normalizer: Normalizer,
) -> dict[str, Any]:
    raw_surfaces = tokenize(text, lower=False)
    raw_tokens = [token.lower() for token in raw_surfaces]
    if not raw_tokens:
        return {"name": label, "error": "No tokens found."}
    if (resources.list_entry or {}).get("delivery_mode") == "server-side-only" and (
        len(raw_tokens) < SERVER_ONLY_MIN_TOKENS
        or len(set(raw_tokens)) < SERVER_ONLY_MIN_TYPES
    ):
        return {
            "name": label,
            "error": (
                "Server-only list analysis requires at least "
                f"{SERVER_ONLY_MIN_TOKENS} lexical tokens and "
                f"{SERVER_ONLY_MIN_TYPES} distinct types."
            ),
        }

    indices = IDX.all_indices(
        raw_tokens,
        segment=config.msttr_segment,
        window=config.mattr_window,
        mtld_threshold=config.mtld_threshold,
        hdd_sample=config.hdd_sample,
        vocd_seed=config.vocd_seed,
    )
    panel_b = None
    if resources.rank_map is not None and resources.list_meta is not None:
        panel_b = FRQ.panel_b(
            raw_tokens,
            resources.rank_map,
            normalizer,
            n_levels=resources.list_meta["n_levels"],
            thresholds=config.thresholds,
            advanced_cutoff=config.advanced_cutoff,
            min_tokens=config.min_tokens,
            mtld_threshold=config.mtld_threshold,
            mattr_window=config.mattr_window,
            hdd_sample=config.hdd_sample,
        )

    semantic_summary = None
    if resources.semantic_index is not None:
        normalized_lemmas = [normalizer.normalize(token) for token in raw_tokens]
        semantic_summary = {
            **SEMANTIC.summarize_lemmas(normalized_lemmas, resources.semantic_index),
            "resource": "Open English WordNet 2025",
            "license": "CC BY 4.0",
            "lookup_pos": "all parts of speech (POS-agnostic aggregation)",
            "normalizer": (
                f"{getattr(normalizer, 'name', 'unknown')} "
                f"{getattr(normalizer, 'version', 'unknown')}"
            ),
        }

    tubelex_summary = None
    if resources.tubelex_index is not None:
        tubelex_index = resources.tubelex_index
        tubelex_manifest = tubelex_index.metadata
        source_metadata = tubelex_manifest.get("source") or {}
        artifact_metadata = tubelex_manifest.get("artifact") or {}
        tubelex_totals = tubelex_index.totals
        tubelex_source_types = tubelex_index.source_vocabulary_size
        tubelex_metrics = TUBELEX.summarize_tubelex_text(
            text,
            tubelex_index,
        )
        tubelex_summary = {
            **tubelex_metrics,
            "metadata": {
                "name": "TUBELEX-EN Treebank published frequency aggregates",
                "version": TUBELEX.TUBELEX_EN_SOURCE_COMMIT,
                "source_asset": TUBELEX.TUBELEX_EN_SOURCE_ASSET,
                "source_url": TUBELEX.TUBELEX_EN_SOURCE_URL,
                "source_sha256": source_metadata.get("sha256"),
                "artifact_sha256": artifact_metadata.get("sha256"),
                "license": TUBELEX.TUBELEX_REPOSITORY_LICENSE,
                "license_spdx": TUBELEX.TUBELEX_REPOSITORY_LICENSE_SPDX,
                "license_url": TUBELEX.TUBELEX_REPOSITORY_LICENSE_URL,
                "method_id": TUBELEX.TUBELEX_EN_METHOD_ID,
                "lookup_unit": "lower-cased Treebank surface/clitic lexical token",
                "normalization": TUBELEX.TUBELEX_RUNTIME_NORMALIZATION,
                "corpus_tokens": tubelex_totals.count,
                "corpus_types": tubelex_source_types,
                "corpus_videos": tubelex_totals.videos,
                "corpus_channels": tubelex_totals.channels,
                "runtime_index_rows": len(tubelex_index),
                "retained_reference_token_mass": tubelex_index.retained_token_mass,
                "frequency_unseen_zipf": math.log10(
                    1_000_000_000
                    / (tubelex_totals.count + tubelex_source_types)
                ),
                "video_unseen_log10_prevalence": math.log10(
                    1 / (tubelex_totals.videos + 2)
                ),
                "channel_unseen_log10_prevalence": math.log10(
                    1 / (tubelex_totals.channels + 2)
                ),
            },
        }

    return {
        "name": label,
        # These three sequences are transient and are stripped before return.
        "raw_tokens": raw_tokens,
        "raw_surfaces": raw_surfaces,
        "a_tokens": raw_tokens,
        "n_tokens": len(raw_tokens),
        "n_types": len(set(raw_tokens)),
        "indices": indices,
        "panel_b": panel_b,
        "semantic_network": semantic_summary,
        "tubelex": tubelex_summary,
        "list_meta": clean_deep(dict(resources.list_meta or {})),
        "list_entry": _safe_list_entry(resources.list_entry),
        "list_path": (
            None
            if (resources.list_entry or {}).get("delivery_mode") == "server-side-only"
            else Path(resources.list_path).name if resources.list_path else None
        ),
        "effective_lemmatizer": _normalizer_info(normalizer),
    }


def _text_from_document(document: TextDocument | str | Mapping[str, Any]) -> str:
    if isinstance(document, TextDocument):
        text = document.text
    elif isinstance(document, str):
        text = document
    elif isinstance(document, Mapping):
        text = document.get("text")
    else:
        raise TypeError("documents must contain TextDocument, string, or mapping values")
    if not isinstance(text, str):
        raise TypeError("each document must provide text as a string")
    return text


def _batch_payload(
    transient_results: list[dict[str, Any]],
    config: AnalysisConfig,
) -> dict[str, Any]:
    documents = [result["payload"] for result in transient_results]
    if len(documents) == 1:
        return documents[0]
    return clean_deep({
        "ldfreq_version": __version__,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "batch": {"n_documents": len(documents)},
        "batch_diagnostics": {
            "bands": BATCH.band_rows(transient_results),
            "reliability": BATCH.reliability_rows(
                transient_results,
                segment=config.msttr_segment,
                window=config.mattr_window,
                hdd_sample=config.hdd_sample,
            ),
            "overlap_matrix": BATCH.overlap_matrix_rows(transient_results),
            "overlap_pairs": BATCH.overlap_pair_rows(transient_results),
        },
        "documents": documents,
    })


def analyze_documents(
    documents: Iterable[TextDocument | str | Mapping[str, Any]],
    config: AnalysisConfig | None = None,
    *,
    resources: AnalysisResources | None = None,
    progress: ProgressCallback | None = None,
) -> AnalysisBatch:
    """Analyze documents and return only aggregate, pseudonymously labelled data.

    Input mapping ``name`` fields are intentionally ignored.  Labels are assigned
    by request order so uploaded filenames cannot cross the service boundary.
    Empty/tokenless documents are reported by label without echoing their text.
    """

    effective_config = config or AnalysisConfig()
    effective_resources = resources or AnalysisResources()
    normalizer = _normalizer(effective_resources)
    request_documents = list(documents)
    transient_results: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total = len(request_documents)

    for index, document in enumerate(request_documents, start=1):
        label = f"Document {index:03d}"
        text = _text_from_document(document)
        result = _analyze_transient_document(
            text,
            label,
            effective_config,
            effective_resources,
            normalizer,
        )
        if result.get("error"):
            skipped.append({"name": label, "error": str(result["error"])})
        else:
            result["payload"] = _document_payload(
                result,
                effective_config,
                effective_resources,
                normalizer,
            )
            transient_results.append(result)
        if progress is not None:
            progress(index, total, label)

    if not transient_results:
        payload: dict[str, Any] = {
            "ldfreq_version": __version__,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "batch": {"n_documents": 0},
            "documents": [],
        }
        return AnalysisBatch(results=(), payload=payload, skipped=tuple(skipped))

    payload = _batch_payload(transient_results, effective_config)
    retained_results = tuple(
        clean_deep(retain_aggregate_result(result)) for result in transient_results
    )
    response = AnalysisBatch(
        results=retained_results,
        payload=payload,
        skipped=tuple(skipped),
    )
    unsafe_paths = sensitive_paths({
        "results": response.results,
        "payload": response.payload,
        "skipped": response.skipped,
    })
    if unsafe_paths:
        raise RuntimeError("Privacy invariant failed before returning analysis results")
    return response


def analyze_text(
    text: str,
    config: AnalysisConfig | None = None,
    *,
    resources: AnalysisResources | None = None,
) -> dict[str, Any]:
    """Analyze one text and return its aggregate result.

    ``ValueError`` is raised when the document is ineligible for analysis; the
    exception does not include source content.
    """

    response = analyze_documents([TextDocument(text)], config, resources=resources)
    if not response.results:
        reason = response.skipped[0]["error"] if response.skipped else "No tokens found"
        raise ValueError(reason)
    return response.results[0]
