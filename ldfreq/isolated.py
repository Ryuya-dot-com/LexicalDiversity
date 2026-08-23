"""One-shot subprocess boundary for analysis of untrusted learner text.

The parent process never passes source text through argv, environment variables,
pickle, a temporary file, or a shared cache.  A length-framed JSON request is
written to the worker's stdin, while aggregate-only events return on a separate
inherited file descriptor.  The worker's ordinary stdout and stderr are sent to
``DEVNULL`` so a dependency cannot accidentally echo source content into the
application log.

This module is POSIX-only by design.  The reference deployment is Cloud Run
Linux, and the development/test environment is macOS.  A one-shot ``Popen``
boundary avoids forking Streamlit's multithreaded process and makes a hard
TERM/KILL/reap deadline possible.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
import os
from pathlib import Path
import re
import selectors
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import Any, Callable, Iterable, Mapping

from . import OUTPUT_SCHEMA_VERSION, __version__
from .analysis import (
    AnalysisBatch,
    AnalysisConfig,
    TextDocument,
    _normalizer,
    _settings,
)
from .privacy import AGGREGATE_RESULT_KEYS, sensitive_paths
from . import batch as BATCH
from . import frequency as FRQ
from . import indices as IDX
from . import server_only_gate as SERVER_GATE
from . import tubelex as TUBELEX


PROTOCOL_VERSION = 2
MIB = 1024 * 1024
DEFAULT_MAX_SOURCE_BYTES = 20 * MIB
DEFAULT_MAX_REQUEST_BYTES = 48 * MIB
DEFAULT_MAX_RESPONSE_BYTES = 32 * MIB
DEFAULT_MAX_DOCUMENTS = 200
WORKER_EVENT_FD_ENV = "LDFREQ_WORKER_EVENT_FD"
WORKER_MODULE = "ldfreq.analysis_worker"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_ENCODING = "typed-map-v1"
MAP_TAG = "__ldfreq_typed_map_v1__"

_RESOURCE_ENV_NAMES = (
    "LDFREQ_ALLOW_LOCAL_RESTRICTED",
    "LDFREQ_SERVING_MODE",
    "LDFREQ_SERVER_ONLY_RESOURCE_IDS",
    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED",
    "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION",
    "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID",
    "LDFREQ_NJ8_PATH",
    "LDFREQ_ANTBNC_PATH",
    "LDFREQ_BNCCOCA_PATH",
    "LDFREQ_BNCCOCA_FAMILIES_PATH",
    "LDFREQ_RANGE_PATH",
    "LDFREQ_NGSL_PATH",
    "LDFREQ_NATION_BNCCOCA_INDEX_PATH",
    "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
)
_SERVER_ONLY_GATE_ENV_NAMES = frozenset({
    SERVER_GATE.SERVER_ONLY_RESOURCE_IDS_ENV,
    SERVER_GATE.SERVER_ONLY_RIGHTS_ACK_ENV,
    SERVER_GATE.SERVER_ONLY_CONTROL_ATTESTATION_ENV,
    SERVER_GATE.SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV,
})
_SERVER_ONLY_PATH_ENV_NAMES_BY_ID = {
    "bnc_coca": frozenset({"LDFREQ_BNCCOCA_PATH"}),
    "nation_bnc_coca_families": frozenset({
        "LDFREQ_NATION_BNCCOCA_INDEX_PATH",
        "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
    }),
}
_SERVER_ONLY_PATH_ENV_NAMES = frozenset().union(
    *_SERVER_ONLY_PATH_ENV_NAMES_BY_ID.values()
)
_LOCAL_RESTRICTED_PATH_ENV_NAMES = frozenset({
    "LDFREQ_NJ8_PATH",
    "LDFREQ_ANTBNC_PATH",
    "LDFREQ_BNCCOCA_FAMILIES_PATH",
    "LDFREQ_RANGE_PATH",
})
_RUNTIME_ENV_NAMES = (
    "TMPDIR",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "__PYVENV_LAUNCHER__",
)
_LIST_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_LEMMATIZER_IDS = frozenset({"open_flemma", "simplemma", "word_form", "antbnc"})
_WORKER_ERROR_CODES = frozenset(
    {
        "internal-error",
        "invalid-request",
        "resource-unavailable",
        "response-too-large",
    }
)
_RESULT_KEYS = AGGREGATE_RESULT_KEYS
_SKIPPED_KEYS = frozenset({"name", "error"})
_SKIP_ERRORS = frozenset(
    {
        "No tokens found.",
        (
            "Server-only list analysis requires at least 100 lexical tokens "
            "and 20 distinct types."
        ),
    }
)
_DOCUMENT_PAYLOAD_KEYS = frozenset(
    {
        "ldfreq_version",
        "output_schema_version",
        "document",
        "settings",
        "method_notes",
        "privacy",
        "n_tokens",
        "n_types",
        "panel_a",
        "panel_a_records",
        "panel_b",
        "semantic_network",
        "tubelex",
    }
)
_PANEL_A_KEYS = frozenset(
    {
        "ttr",
        "rttr",
        "cttr",
        "herdan",
        "maas",
        "msttr",
        "mattr",
        "mtld",
        "hdd",
        "vocd",
        "yule_k",
        "yule_i",
    }
)
_INDEX_RECORD_KEYS = frozenset(
    {
        "value",
        "status",
        "missing_reason",
        "method_id",
        "requested_parameters",
        "effective_parameters",
        "advisory_quality_floor_tokens",
        "advisory_quality_status",
    }
)
_INDEX_MISSING_REASONS = frozenset(
    {
        "empty_input",
        "insufficient_tokens_for_formula",
        "too_short_for_requested_parameter",
        "no_convergence",
        "zero_denominator",
        "no_factor",
        "undefined_for_text",
    }
)
_PANEL_B_KEYS = frozenset(
    {
        "mapping_diagnostics",
        "lfp",
        "coverage_threshold",
        "advanced_guiraud",
        "pct_beyond_k",
        "mean_rank",
        "p_lex",
        "s_index",
        "band_wise",
    }
)
_PANEL_B_MAPPING_KEYS = frozenset(
    {
        "method_id",
        "input_tokens",
        "input_surface_types",
        "mapped_unit_types",
        "collapsed_surface_types",
        "surface_hit_tokens",
        "surface_hit_rate",
        "normalized_fallback_hit_tokens",
        "normalized_fallback_hit_rate",
        "normalized_off_list_tokens",
        "normalized_off_list_rate",
        "identity_fallback_tokens",
        "identity_fallback_rate",
    }
)
_PANEL_B_PATHS = (
    "surface_hit",
    "normalized_fallback_hit",
    "normalized_off_list",
    "identity_fallback",
)
_LFP_ROW_KEYS = frozenset(
    {"level", "tokens", "types", "coverage_%", "cumulative_%"}
)
_PANEL_B_MEAN_RANK_KEYS = frozenset(
    {"mean_rank", "mean_log_rank", "pct_off_list"}
)
_P_LEX_SHORT_KEYS = frozenset({"lambda", "n_segments"})
_P_LEX_FULL_KEYS = frozenset(
    {
        "lambda",
        "n_segments",
        "mean_hard_per_seg",
        "observed_distribution",
        "fitted_distribution",
    }
)
_S_SHORT_KEYS = frozenset({"S", "note"})
_S_FULL_KEYS = frozenset(
    {"S", "empirical_coverage_pct", "capped", "reference_list_note"}
)
_BAND_WISE_ROW_KEYS = frozenset(
    {"level", "tokens", "types", "MTLD", "MATTR", "HD-D", "Min N"}
)
_SEMANTIC_KEYS = frozenset(
    {
        "tokens",
        "types",
        "covered_tokens",
        "covered_types",
        "token_coverage",
        "type_coverage",
        "depth_covered_tokens",
        "depth_covered_types",
        "depth_token_coverage",
        "depth_type_coverage",
        "polysemy_token_mean",
        "polysemy_type_mean",
        "hypernym_depth_token_mean",
        "hypernym_depth_type_mean",
        "resource",
        "license",
        "lookup_pos",
        "normalizer",
    }
)
_BATCH_DIAGNOSTIC_KEYS = frozenset(
    {"bands", "reliability", "overlap_matrix", "overlap_pairs"}
)
_BATCH_BAND_ROW_KEYS = _LFP_ROW_KEYS | frozenset({"document"})
_RELIABILITY_ROW_KEYS = frozenset(
    {
        "document",
        "index_key",
        "index",
        "status",
        "status_code",
        "n_tokens",
        "required_tokens",
        "value",
        "note",
        "missing_reason",
        "method_id",
        "requested_parameters",
        "effective_parameters",
        "advisory_quality_floor_tokens",
        "advisory_quality_status",
    }
)
_OVERLAP_MATRIX_ROW_KEYS = frozenset(
    {"document_a", "document_b", "shared_types", "union_types", "jaccard"}
)
_OVERLAP_PAIR_ROW_KEYS = frozenset(
    {
        "document_a",
        "document_b",
        "shared_types",
        "a_only_types",
        "b_only_types",
        "union_types",
        "jaccard",
    }
)
_TUBELEX_KEYS = frozenset(
    {
        "tokens",
        "types",
        "covered_tokens",
        "covered_types",
        "token_coverage",
        "type_coverage",
        "frequency_zipf_token_mean",
        "frequency_zipf_type_mean",
        "video_log10_prevalence_token_mean",
        "video_log10_prevalence_type_mean",
        "channel_log10_prevalence_token_mean",
        "channel_log10_prevalence_type_mean",
        "metadata",
    }
)
_TUBELEX_METADATA_KEYS = frozenset(
    {
        "name",
        "version",
        "source_asset",
        "source_url",
        "source_sha256",
        "artifact_sha256",
        "license",
        "license_spdx",
        "license_url",
        "method_id",
        "lookup_unit",
        "normalization",
        "corpus_tokens",
        "corpus_types",
        "corpus_videos",
        "corpus_channels",
        "runtime_index_rows",
        "retained_reference_token_mass",
        "frequency_unseen_zipf",
        "video_unseen_log10_prevalence",
        "channel_unseen_log10_prevalence",
    }
)
_SINGLE_FLIGHT = threading.BoundedSemaphore(1)


class AnalysisIsolationError(RuntimeError):
    """Base class whose messages never contain request-derived data."""


class AnalysisInputInvalid(ValueError):
    """The request cannot safely cross the isolation boundary."""


class AnalysisInputTooLarge(AnalysisInputInvalid):
    """Source or framed request exceeds a configured hard limit."""


class AnalysisDeadlineExceeded(TimeoutError):
    """The isolated process was stopped at its monotonic wall-clock deadline."""


class AnalysisCancelled(AnalysisIsolationError):
    """The caller requested cancellation and the worker was reaped."""


class AnalysisBusy(AnalysisIsolationError):
    """Another analysis already occupies this application process."""


class AnalysisProtocolError(AnalysisIsolationError):
    """The worker emitted a malformed or oversized event."""


class AnalysisWorkerError(AnalysisIsolationError):
    """The worker failed, exposing only a fixed content-free error code."""

    def __init__(self, code: str = "internal-error") -> None:
        self.code = code if code in _WORKER_ERROR_CODES else "internal-error"
        super().__init__("The isolated analysis worker did not produce a usable result.")


@dataclass(frozen=True, slots=True)
class ResourceSpec:
    """Resource IDs only; paths are resolved by the worker's rights-gated registry."""

    list_id: str | None = None
    lemmatizer_name: str = "open_flemma"
    semantic_enabled: bool = True
    tubelex_enabled: bool = False

    def __post_init__(self) -> None:
        if self.list_id is not None and (
            not isinstance(self.list_id, str) or not _LIST_ID_RE.fullmatch(self.list_id)
        ):
            raise ValueError("list_id must be a short registry identifier")
        if self.lemmatizer_name not in _LEMMATIZER_IDS:
            raise ValueError("lemmatizer_name is not allow-listed")
        if not isinstance(self.semantic_enabled, bool):
            raise ValueError("semantic_enabled must be boolean")
        if not isinstance(self.tubelex_enabled, bool):
            raise ValueError("tubelex_enabled must be boolean")


@dataclass(frozen=True, slots=True)
class IsolationLimits:
    """Hard limits enforced by the parent in addition to worker-side ceilings."""

    deadline_seconds: float = 120.0
    max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES
    max_request_bytes: int = DEFAULT_MAX_REQUEST_BYTES
    max_response_bytes: int = DEFAULT_MAX_RESPONSE_BYTES
    max_documents: int = DEFAULT_MAX_DOCUMENTS
    poll_seconds: float = 0.05
    termination_grace_seconds: float = 0.5

    def __post_init__(self) -> None:
        for name in ("max_source_bytes", "max_request_bytes", "max_response_bytes", "max_documents"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.max_documents > DEFAULT_MAX_DOCUMENTS:
            raise ValueError("max_documents exceeds the worker hard ceiling")
        if self.max_source_bytes > DEFAULT_MAX_SOURCE_BYTES:
            raise ValueError("max_source_bytes exceeds the worker hard ceiling")
        if self.max_request_bytes > DEFAULT_MAX_REQUEST_BYTES:
            raise ValueError("max_request_bytes exceeds the worker hard ceiling")
        if self.max_response_bytes > DEFAULT_MAX_RESPONSE_BYTES:
            raise ValueError("max_response_bytes exceeds the worker hard ceiling")
        for name in ("deadline_seconds", "poll_seconds", "termination_grace_seconds"):
            raw_value = getattr(self, name)
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ValueError(f"{name} must be a positive finite number")
            value = float(raw_value)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
            object.__setattr__(self, name, value)


ProgressCallback = Callable[[int, int, str], None]
CancelCallback = Callable[[], bool]


def _document_text(document: TextDocument | str | Mapping[str, Any]) -> str:
    if isinstance(document, TextDocument):
        text = document.text
    elif isinstance(document, str):
        text = document
    elif isinstance(document, Mapping):
        text = document.get("text")
    else:
        raise AnalysisInputInvalid("Each document must contain text.")
    if not isinstance(text, str):
        raise AnalysisInputInvalid("Each document must contain text.")
    return text


def _request_bytes(
    documents: Iterable[TextDocument | str | Mapping[str, Any]],
    config: AnalysisConfig,
    resources: ResourceSpec,
    limits: IsolationLimits,
) -> tuple[bytes, int]:
    texts: list[str] = []
    source_bytes = 0
    for document in documents:
        if len(texts) >= limits.max_documents:
            raise AnalysisInputInvalid("Document count is outside the configured limit.")
        text = _document_text(document)
        # UTF-8 uses at least one byte per Unicode code point, so this cheap
        # check rejects pathological values without first allocating an equally
        # large encoded copy.
        if len(text) > limits.max_source_bytes:
            raise AnalysisInputTooLarge("Source text exceeds the configured byte limit.")
        try:
            encoded_length = len(text.encode("utf-8"))
        except UnicodeEncodeError:
            raise AnalysisInputInvalid("Source text is not valid UTF-8.") from None
        source_bytes += encoded_length
        if source_bytes > limits.max_source_bytes:
            raise AnalysisInputTooLarge("Source text exceeds the configured byte limit.")
        texts.append(text)
    if not texts:
        raise AnalysisInputInvalid("Document count is outside the configured limit.")
    request = {
        "version": PROTOCOL_VERSION,
        "documents": texts,
        "config": asdict(config),
        "resources": asdict(resources),
    }
    try:
        payload = json.dumps(
            request,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError):
        raise AnalysisInputInvalid("Analysis request could not be encoded.") from None
    if len(payload) + 4 > limits.max_request_bytes:
        raise AnalysisInputTooLarge("Framed request exceeds the configured byte limit.")
    return struct.pack(">I", len(payload)) + payload, len(texts)


def _worker_environment(event_fd: int) -> dict[str, str]:
    # Deliberately omit cloud credentials, PYTHONPATH, base64 payloads, HOME,
    # arbitrary application settings, and every user-derived value.
    environment = {
        WORKER_EVENT_FD_ENV: str(event_fd),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
    }
    local_restricted = (
        os.environ.get("LDFREQ_SERVING_MODE") == "local"
        and os.environ.get("LDFREQ_ALLOW_LOCAL_RESTRICTED") == "1"
    )
    server_only_ids = SERVER_GATE.configured_server_only_ids(
        SERVER_GATE.SERVER_ONLY_ELIGIBLE_IDS
    )
    permitted_server_only_paths = frozenset().union(
        *(
            _SERVER_ONLY_PATH_ENV_NAMES_BY_ID[resource_id]
            for resource_id in server_only_ids
        )
    )
    for name in (*_RESOURCE_ENV_NAMES, *_RUNTIME_ENV_NAMES):
        if name in _SERVER_ONLY_GATE_ENV_NAMES and not server_only_ids:
            continue
        if (
            name in _SERVER_ONLY_PATH_ENV_NAMES
            and not local_restricted
            and name not in permitted_server_only_paths
        ):
            continue
        if name in _LOCAL_RESTRICTED_PATH_ENV_NAMES and not local_restricted:
            continue
        value = os.environ.get(name)
        if value is not None:
            environment[name] = value
    return environment


def _json_loads(payload: bytes) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError("non-finite number")

    return json.loads(payload.decode("utf-8"), parse_constant=reject_constant)


def _wire_decode(value: Any, *, depth: int = 0) -> Any:
    """Decode the worker's typed mapping representation with strict bounds."""

    if depth > 100:
        raise AnalysisProtocolError("Worker result nesting was invalid.")
    if isinstance(value, list):
        return [_wire_decode(item, depth=depth + 1) for item in value]
    if isinstance(value, dict):
        if frozenset(value) != {MAP_TAG} or not isinstance(value[MAP_TAG], list):
            raise AnalysisProtocolError("Worker result mapping encoding was invalid.")
        decoded: dict[str | int, Any] = {}
        for pair in value[MAP_TAG]:
            if not isinstance(pair, list) or len(pair) != 2:
                raise AnalysisProtocolError("Worker result mapping entry was invalid.")
            typed_key, item = pair
            if not isinstance(typed_key, list) or len(typed_key) != 2:
                raise AnalysisProtocolError("Worker result mapping key was invalid.")
            key_type, raw_key = typed_key
            if key_type == "s" and isinstance(raw_key, str):
                key: str | int = raw_key
            elif (
                key_type == "i"
                and isinstance(raw_key, int)
                and not isinstance(raw_key, bool)
            ):
                key = raw_key
            else:
                raise AnalysisProtocolError("Worker result mapping key type was invalid.")
            if key in decoded:
                raise AnalysisProtocolError("Worker result mapping contained a duplicate key.")
            decoded[key] = _wire_decode(item, depth=depth + 1)
        return decoded
    if isinstance(value, float) and not math.isfinite(value):
        raise AnalysisProtocolError("Worker result number was not finite.")
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise AnalysisProtocolError("Worker result value type was invalid.")


def _pop_frames(buffer: bytearray, max_frame_bytes: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    while len(buffer) >= 4:
        frame_bytes = struct.unpack(">I", buffer[:4])[0]
        if frame_bytes <= 0 or frame_bytes + 4 > max_frame_bytes:
            raise AnalysisProtocolError("Worker event exceeded the frame limit.")
        if len(buffer) < 4 + frame_bytes:
            break
        payload = bytes(buffer[4 : 4 + frame_bytes])
        del buffer[: 4 + frame_bytes]
        try:
            message = _json_loads(payload)
        except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
            raise AnalysisProtocolError("Worker event was not valid JSON.") from None
        if not isinstance(message, dict):
            raise AnalysisProtocolError("Worker event was not an object.")
        messages.append(message)
    return messages


def _expected_method_notes(
    config: AnalysisConfig,
    resources: ResourceSpec,
) -> list[str]:
    notes = [
        f"Tokenizer policy: {config.tokenizer_policy}",
        (
            "Counting unit is token: Panel A uses lower-cased surface tokens without "
            "lemmatization."
        ),
        (
            "Panel A standard method IDs retain the requested segment, window, and "
            "sample parameters; advisory quality floors never trigger parameter shrinking."
        ),
        (
            "Python MTLD uses a bidirectional arithmetic mean, closes factors at "
            "TTR <= threshold after at least 10 tokens, and has a Python-specific "
            "method ID rather than an R-equivalence claim."
        ),
    ]
    if resources.lemmatizer_name == "antbnc":
        notes.append(
            "AntBNC mode is an NWLC approximation, not bit-identical to New Word "
            "Level Checker."
        )
    if resources.list_id is None:
        return notes

    server_ids = SERVER_GATE.configured_server_only_ids(
        {"bnc_coca", "nation_bnc_coca_families"}
    )
    if resources.list_id in server_ids:
        notes.append(
            "Server-only lookup requires at least 100 lexical tokens and 20 distinct "
            "surface types per document."
        )
    notes.append(
        f"Panel B mapping method {FRQ.PANEL_B_MAPPING_METHOD_ID} is hybrid: an exact "
        "lower-cased surface-form key is looked up first, and the configured "
        "normalizer is used only after that lookup misses. Direct hits are not "
        "re-normalized, so this is not a pure flemma or lemma pipeline."
    )
    if resources.list_id in {"bnc_coca_families", "nation_bnc_coca_families"}:
        notes.append(
            "Panel B maps tokens to BNC/COCA word-family heads when the selected "
            "family list contains the token/form."
        )
    elif resources.list_id == "range_baseword":
        notes.append(
            "Panel B maps tokens to Range/AntWordProfiler baseword-family heads "
            "when the selected level-list contains the token/form."
        )
    else:
        notes.append(
            "For spelling/headword resources, a successful normalized fallback maps "
            "to the corresponding ranked entry; a direct listed spelling keeps its "
            "own rank."
        )
    notes.extend(
        [
            (
                "Coverage thresholds are selected-list matched coverage, not an automatic "
                "reader-known coverage estimate."
            ),
            (
                "Proper nouns, marginal words, acronyms, and other potentially known items "
                "are not automatically credited unless they match the selected "
                "list/normalizer."
            ),
            (
                "Panel B values are not claimed to be numerically comparable to LexTutor. "
                "Comparison would require the same hybrid lookup order, frequency list, "
                "word-family expansion, tokenizer, proper-noun/number policy, and normalizer."
            ),
            (
                "P_Lex counts unclassified off-list items as hard words under this app's "
                "no-automatic-proper-noun-adjustment policy."
            ),
            (
                "S uses the selected list's ranks, not Kojima & Yamashita's BNC-spoken "
                "family lists; values are not directly comparable to published S values."
            ),
        ]
    )
    return notes


def _expected_resource_contract(
    config: AnalysisConfig,
    resources: ResourceSpec,
) -> dict[str, Any]:
    """Rebuild trusted settings and retained resource metadata in the parent.

    Resource loading is repeated from the parent-controlled registry.  Submitted
    text is not involved.  TUBELEX and semantic output resources are skipped
    unless semantic data is required to reproduce the open-flemma identity,
    because neither output axis otherwise contributes a settings field.
    """
    from .analysis_worker import _resources_from_request

    resource_request = asdict(resources)
    resource_request["tubelex_enabled"] = False
    resource_request["semantic_enabled"] = (
        resources.lemmatizer_name == "open_flemma"
    )
    try:
        expected_resources = _resources_from_request(resource_request)
        normalizer = _normalizer(expected_resources)
        list_entry = (
            dict(expected_resources.list_entry)
            if expected_resources.list_entry is not None
            else None
        )
        delivery_mode = (list_entry or {}).get("delivery_mode")
        list_path = (
            None
            if delivery_mode == "server-side-only"
            else (
                Path(expected_resources.list_path).name
                if expected_resources.list_path is not None
                else None
            )
        )
        return {
            "settings": _settings(config, expected_resources, normalizer),
            "list_meta": dict(expected_resources.list_meta or {}),
            "list_entry": list_entry,
            "list_path": list_path,
            "effective_lemmatizer": {
                "name": str(getattr(normalizer, "name", "unknown")),
                "version": str(getattr(normalizer, "version", "unknown")),
            },
        }
    except Exception:
        raise AnalysisProtocolError(
            "Worker resource metadata could not be independently verified."
        ) from None


def _valid_optional_number(value: Any) -> bool:
    return value is None or (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _plain_nonnegative_integer(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value >= 0


def _finite_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _close(left: int | float, right: int | float, *, tolerance: float = 1e-12) -> bool:
    return math.isclose(
        float(left),
        float(right),
        rel_tol=tolerance,
        abs_tol=tolerance,
    )


def _index_value_in_mathematical_domain(
    key: str,
    value: int | float,
    requested: Mapping[str, Any],
) -> bool:
    """Check source-independent mathematical bounds for an available metric."""

    numeric = float(value)
    if key in {"ttr", "msttr", "mattr", "hdd"}:
        return 0.0 <= numeric <= 1.0
    if key == "vocd":
        grid_max = requested.get("grid_max")
        return _finite_number(grid_max) and 1.0 <= numeric < float(grid_max)
    if key == "yule_k":
        return 0.0 <= numeric < 10_000.0
    if key in {"mtld", "yule_i"}:
        return numeric > 0.0
    return numeric >= 0.0


def _validate_index_records(
    records: Any,
    panel_a: Mapping[str, Any],
    *,
    n_tokens: int,
    n_types: int,
    settings: Mapping[str, Any],
) -> None:
    if not isinstance(records, dict) or frozenset(records) != _PANEL_A_KEYS:
        raise AnalysisProtocolError("Worker Panel A record schema was invalid.")
    expected_parameters = IDX.requested_parameters(
        segment=settings.get("msttr_segment", 50),
        window=settings.get("mattr_window", 50),
        mtld_threshold=settings.get("mtld_threshold", 0.72),
        hdd_sample=settings.get("hdd_sample", 42),
        vocd_seed=settings.get("vocd_seed", 42),
    )
    for key, record in records.items():
        if not isinstance(record, dict) or frozenset(record) != _INDEX_RECORD_KEYS:
            raise AnalysisProtocolError("Worker Panel A record schema was invalid.")
        value = record.get("value")
        status = record.get("status")
        reason = record.get("missing_reason")
        requested = record.get("requested_parameters")
        effective = record.get("effective_parameters")
        quality_floor = record.get("advisory_quality_floor_tokens")
        expected_quality_floor = IDX.effective_min_tokens(
            key,
            segment=settings.get("msttr_segment", 50),
            window=settings.get("mattr_window", 50),
            hdd_sample=settings.get("hdd_sample", 42),
            min_tokens_override=settings.get("min_tokens"),
        )
        if (
            not _valid_optional_number(value)
            or value != panel_a.get(key)
            or status not in {"available", "missing"}
            or (status == "available" and (value is None or reason is not None))
            or (status == "missing" and (value is not None or reason not in _INDEX_MISSING_REASONS))
            or record.get("method_id") != IDX.METHOD_IDS[key]
            or requested != expected_parameters[key]
            or effective != (requested if status == "available" else {})
            or isinstance(quality_floor, bool)
            or not isinstance(quality_floor, int)
            or quality_floor != expected_quality_floor
            or record.get("advisory_quality_status")
            != (
                "below_advisory_floor"
                if n_tokens < quality_floor
                else "meets_advisory_floor"
            )
        ):
            raise AnalysisProtocolError("Worker Panel A record values were invalid.")
        if status == "available" and not _index_value_in_mathematical_domain(
            key,
            value,
            requested,
        ):
            raise AnalysisProtocolError(
                "Worker Panel A value was outside its mathematical domain."
            )

    # These invariants depend only on trusted configuration and the aggregate
    # N/V counts.  Recompute the five values that are fully determined by N/V,
    # and fail closed on every standard method whose computational domain can
    # be decided without retaining or reconstructing the source token stream.
    exact_values = {
        "ttr": n_types / n_tokens,
        "rttr": n_types / math.sqrt(n_tokens),
        "cttr": n_types / math.sqrt(2 * n_tokens),
    }
    if n_tokens > 1:
        exact_values.update(
            {
                "herdan": math.log(n_types) / math.log(n_tokens),
                "maas": (
                    (math.log(n_tokens) - math.log(n_types))
                    / (math.log(n_tokens) ** 2)
                ),
            }
        )
    for key, expected_value in exact_values.items():
        record = records[key]
        if (
            record["status"] != "available"
            or record["missing_reason"] is not None
            or record["value"] != expected_value
        ):
            raise AnalysisProtocolError(
                "Worker Panel A formula/domain invariants were invalid."
            )
    if n_tokens == 1:
        for key in ("herdan", "maas"):
            record = records[key]
            if (
                record["status"] != "missing"
                or record["missing_reason"] != "insufficient_tokens_for_formula"
            ):
                raise AnalysisProtocolError(
                    "Worker Panel A formula/domain invariants were invalid."
                )

    deterministic_domains = {
        "msttr": (n_tokens >= settings["msttr_segment"], "too_short_for_requested_parameter"),
        "mattr": (n_tokens >= settings["mattr_window"], "too_short_for_requested_parameter"),
        "hdd": (n_tokens >= settings["hdd_sample"], "too_short_for_requested_parameter"),
        "yule_k": (True, None),
        "yule_i": (n_tokens != n_types, "zero_denominator"),
    }
    for key, (should_be_available, missing_reason) in deterministic_domains.items():
        record = records[key]
        if should_be_available:
            invalid = record["status"] != "available" or record["missing_reason"] is not None
        else:
            invalid = (
                record["status"] != "missing"
                or record["missing_reason"] != missing_reason
            )
        if invalid:
            raise AnalysisProtocolError(
                "Worker Panel A formula/domain invariants were invalid."
            )

    mtld_record = records["mtld"]
    if n_tokens < 10:
        mtld_valid = (
            mtld_record["status"] == "missing"
            and mtld_record["missing_reason"] == "insufficient_tokens_for_formula"
        )
    else:
        mtld_valid = (
            mtld_record["status"] == "available"
            and mtld_record["missing_reason"] is None
        ) or (
            mtld_record["status"] == "missing"
            and mtld_record["missing_reason"] == "no_factor"
        )
    if not mtld_valid:
        raise AnalysisProtocolError(
            "Worker Panel A formula/domain invariants were invalid."
        )

    vocd_record = records["vocd"]
    vocd_hi = expected_parameters["vocd"]["sample_size_max"]
    if n_tokens < vocd_hi:
        vocd_valid = (
            vocd_record["status"] == "missing"
            and vocd_record["missing_reason"]
            == "too_short_for_requested_parameter"
        )
    else:
        vocd_valid = (
            vocd_record["status"] == "available"
            and vocd_record["missing_reason"] is None
        ) or (
            vocd_record["status"] == "missing"
            and vocd_record["missing_reason"] == "no_convergence"
        )
    if not vocd_valid:
        raise AnalysisProtocolError(
            "Worker Panel A formula/domain invariants were invalid."
        )


def _validate_panel_b_payload(
    value: Any,
    *,
    expected: bool,
    n_tokens: int,
    n_types: int,
    settings: Mapping[str, Any],
) -> None:
    """Validate the complete aggregate-only Panel B contract."""

    if not expected:
        if value is not None:
            raise AnalysisProtocolError("Worker Panel B payload was unexpected.")
        return
    if not isinstance(value, dict) or frozenset(value) != _PANEL_B_KEYS:
        raise AnalysisProtocolError("Worker Panel B payload schema was invalid.")

    n_levels = settings.get("list_levels")
    if (
        isinstance(n_levels, bool)
        or not isinstance(n_levels, int)
        or n_levels <= 0
    ):
        raise AnalysisProtocolError("Worker Panel B resource metadata was invalid.")
    expected_levels = [f"K{level}" for level in range(1, n_levels + 1)] + [
        "off-list"
    ]

    diagnostics = value.get("mapping_diagnostics")
    if (
        not isinstance(diagnostics, dict)
        or frozenset(diagnostics) != _PANEL_B_MAPPING_KEYS
        or diagnostics.get("method_id") != FRQ.PANEL_B_MAPPING_METHOD_ID
    ):
        raise AnalysisProtocolError("Worker Panel B mapping schema was invalid.")
    count_fields = (
        "input_tokens",
        "input_surface_types",
        "mapped_unit_types",
        "collapsed_surface_types",
        *(f"{path}_tokens" for path in _PANEL_B_PATHS),
    )
    if any(
        not _plain_nonnegative_integer(diagnostics.get(field))
        for field in count_fields
    ):
        raise AnalysisProtocolError("Worker Panel B mapping counts were invalid.")
    if (
        diagnostics["input_tokens"] != n_tokens
        or diagnostics["input_surface_types"] != n_types
        or not 1 <= diagnostics["mapped_unit_types"] <= n_types
        or diagnostics["collapsed_surface_types"]
        != n_types - diagnostics["mapped_unit_types"]
        or sum(diagnostics[f"{path}_tokens"] for path in _PANEL_B_PATHS)
        != n_tokens
    ):
        raise AnalysisProtocolError("Worker Panel B mapping counts were inconsistent.")
    for path in _PANEL_B_PATHS:
        rate = diagnostics.get(f"{path}_rate")
        expected_rate = diagnostics[f"{path}_tokens"] / n_tokens
        if (
            not _finite_number(rate)
            or not 0.0 <= float(rate) <= 1.0
            or not _close(rate, expected_rate)
        ):
            raise AnalysisProtocolError("Worker Panel B mapping rates were invalid.")

    lfp = value.get("lfp")
    if not isinstance(lfp, list) or len(lfp) != len(expected_levels):
        raise AnalysisProtocolError("Worker Panel B frequency profile was invalid.")
    cumulative_tokens = 0
    total_profile_tokens = 0
    total_profile_types = 0
    for index, (row, expected_level) in enumerate(zip(lfp, expected_levels)):
        if (
            not isinstance(row, dict)
            or frozenset(row) != _LFP_ROW_KEYS
            or row.get("level") != expected_level
            or not _plain_nonnegative_integer(row.get("tokens"))
            or not _plain_nonnegative_integer(row.get("types"))
            or row["types"] > row["tokens"]
        ):
            raise AnalysisProtocolError("Worker Panel B frequency profile was invalid.")
        total_profile_tokens += row["tokens"]
        total_profile_types += row["types"]
        if index < n_levels:
            cumulative_tokens += row["tokens"]
        expected_coverage = round(100 * row["tokens"] / n_tokens, 2)
        expected_cumulative = round(100 * cumulative_tokens / n_tokens, 2)
        if (
            not _finite_number(row.get("coverage_%"))
            or not _finite_number(row.get("cumulative_%"))
            or not _close(row["coverage_%"], expected_coverage)
            or not _close(row["cumulative_%"], expected_cumulative)
        ):
            raise AnalysisProtocolError("Worker Panel B coverage values were invalid.")
    if (
        total_profile_tokens != n_tokens
        or total_profile_types != diagnostics["mapped_unit_types"]
    ):
        raise AnalysisProtocolError("Worker Panel B frequency profile was inconsistent.")

    thresholds = settings.get("thresholds")
    threshold_result = value.get("coverage_threshold")
    if (
        not isinstance(thresholds, list)
        or not isinstance(threshold_result, dict)
        or frozenset(threshold_result) != frozenset(thresholds)
    ):
        raise AnalysisProtocolError("Worker Panel B thresholds were invalid.")
    for threshold in frozenset(thresholds):
        cumulative = 0
        expected_hit = None
        for level, row in enumerate(lfp[:-1], start=1):
            cumulative += row["tokens"]
            if 100 * cumulative / n_tokens >= threshold:
                expected_hit = level
                break
        observed_hit = threshold_result[threshold]
        if observed_hit != expected_hit:
            raise AnalysisProtocolError("Worker Panel B thresholds were inconsistent.")

    advanced = value.get("advanced_guiraud")
    pct_beyond = value.get("pct_beyond_k")
    advanced_cutoff = settings.get("advanced_cutoff")
    advanced_types = lfp[-1]["types"] + sum(
        row["types"]
        for level, row in enumerate(lfp[:-1], start=1)
        if level > advanced_cutoff
    )
    expected_guiraud = advanced_types / math.sqrt(n_tokens)
    expected_pct_beyond = (
        100 * advanced_types / diagnostics["mapped_unit_types"]
    )
    if (
        not _finite_number(advanced)
        or not _close(advanced, expected_guiraud)
        or not _finite_number(pct_beyond)
        or not _close(pct_beyond, expected_pct_beyond)
    ):
        raise AnalysisProtocolError("Worker Panel B richness values were invalid.")

    mean_rank = value.get("mean_rank")
    if not isinstance(mean_rank, dict) or frozenset(mean_rank) != _PANEL_B_MEAN_RANK_KEYS:
        raise AnalysisProtocolError("Worker Panel B rank summary was invalid.")
    off_tokens = lfp[-1]["tokens"]
    expected_off_pct = 100 * off_tokens / n_tokens
    if (
        not _finite_number(mean_rank.get("pct_off_list"))
        or not _close(mean_rank["pct_off_list"], expected_off_pct)
    ):
        raise AnalysisProtocolError("Worker Panel B off-list rate was invalid.")
    if off_tokens == n_tokens:
        if mean_rank.get("mean_rank") is not None or mean_rank.get("mean_log_rank") is not None:
            raise AnalysisProtocolError("Worker Panel B rank summary was inconsistent.")
    elif (
        not _finite_number(mean_rank.get("mean_rank"))
        or float(mean_rank["mean_rank"]) < 1.0
        or not _finite_number(mean_rank.get("mean_log_rank"))
        or float(mean_rank["mean_log_rank"]) < 0.0
    ):
        raise AnalysisProtocolError("Worker Panel B rank summary was invalid.")

    p_lex = value.get("p_lex")
    expected_segments = n_tokens // 10
    expected_p_lex_keys = _P_LEX_FULL_KEYS if expected_segments else _P_LEX_SHORT_KEYS
    if (
        not isinstance(p_lex, dict)
        or frozenset(p_lex) != expected_p_lex_keys
        or p_lex.get("n_segments") != expected_segments
    ):
        raise AnalysisProtocolError("Worker Panel B P_Lex payload was invalid.")
    if not expected_segments:
        if p_lex.get("lambda") is not None:
            raise AnalysisProtocolError("Worker Panel B P_Lex payload was invalid.")
    else:
        lam = p_lex.get("lambda")
        hard_mean = p_lex.get("mean_hard_per_seg")
        observed = p_lex.get("observed_distribution")
        fitted = p_lex.get("fitted_distribution")
        distribution_keys = frozenset(range(11))
        if (
            not _finite_number(lam)
            or not 0.0 <= float(lam) <= 10.0
            or not _finite_number(hard_mean)
            or not 0.0 <= float(hard_mean) <= 10.0
            or not isinstance(observed, dict)
            or not isinstance(fitted, dict)
            or frozenset(observed) != distribution_keys
            or frozenset(fitted) != distribution_keys
            or any(
                not _finite_number(probability)
                or not 0.0 <= float(probability) <= 1.0
                for probability in [*observed.values(), *fitted.values()]
            )
            or not _close(sum(observed.values()), 1.0)
            or not _close(
                sum(k * observed[k] for k in range(11)),
                hard_mean,
            )
            or any(
                not _close(
                    fitted[k],
                    math.exp(-float(lam)) * float(lam) ** k / math.factorial(k),
                )
                for k in range(11)
            )
        ):
            raise AnalysisProtocolError("Worker Panel B P_Lex values were invalid.")

    s_payload = value.get("s_index")
    if n_tokens < 50:
        if (
            not isinstance(s_payload, dict)
            or frozenset(s_payload) != _S_SHORT_KEYS
            or s_payload != {"S": None, "note": "n < sample (50)"}
        ):
            raise AnalysisProtocolError("Worker Panel B S payload was invalid.")
    else:
        expected_note = (
            "uses the selected list's ranks, not K&Y's "
            "BNC-spoken family lists; 'capped' = coverage never approaches "
            "100% within rank 3000, so S is not interpretable "
            "(typical when the selected list leaves substantial text off-list)"
        )
        empirical = s_payload.get("empirical_coverage_pct") if isinstance(s_payload, dict) else None
        score = s_payload.get("S") if isinstance(s_payload, dict) else None
        if (
            not isinstance(s_payload, dict)
            or frozenset(s_payload) != _S_FULL_KEYS
            or not _finite_number(score)
            or not 200.0 <= float(score) <= 30_000.0
            or type(s_payload.get("capped")) is not bool
            or s_payload["capped"] != (float(score) >= 30_000.0)
            or s_payload.get("reference_list_note") != expected_note
            or not isinstance(empirical, dict)
            or frozenset(empirical) != frozenset(range(500, 3001, 500))
            or any(
                not _finite_number(rate) or not 0.0 <= float(rate) <= 100.0
                for rate in empirical.values()
            )
        ):
            raise AnalysisProtocolError("Worker Panel B S payload was invalid.")

    band_wise = value.get("band_wise")
    if not isinstance(band_wise, list) or len(band_wise) != len(expected_levels):
        raise AnalysisProtocolError("Worker Panel B band-wise payload was invalid.")
    expected_band_floor = max(
        IDX.effective_min_tokens(
            "mtld",
            mtld_threshold=settings["mtld_threshold"],
            min_tokens_override=settings["min_tokens"],
        ),
        IDX.effective_min_tokens(
            "mattr",
            window=settings["mattr_window"],
            min_tokens_override=settings["min_tokens"],
        ),
        IDX.effective_min_tokens(
            "hdd",
            hdd_sample=settings["hdd_sample"],
            min_tokens_override=settings["min_tokens"],
        ),
    )
    for row, profile_row, expected_level in zip(band_wise, lfp, expected_levels):
        if (
            not isinstance(row, dict)
            or frozenset(row) != _BAND_WISE_ROW_KEYS
            or row.get("level") != expected_level
            or row.get("tokens") != profile_row["tokens"]
            or row.get("types") != profile_row["types"]
            or row.get("Min N") != expected_band_floor
        ):
            raise AnalysisProtocolError("Worker Panel B band-wise payload was inconsistent.")
        band_tokens = row["tokens"]
        mtld = row.get("MTLD")
        mattr = row.get("MATTR")
        hdd = row.get("HD-D")
        if (
            (mtld is not None and (not _finite_number(mtld) or float(mtld) <= 0.0))
            or (band_tokens < 10 and mtld is not None)
            or (mattr is not None and (not _finite_number(mattr) or not 0.0 <= float(mattr) <= 1.0))
            or (band_tokens < settings["mattr_window"] and mattr is not None)
            or (band_tokens >= settings["mattr_window"] and mattr is None)
            or (hdd is not None and (not _finite_number(hdd) or not 0.0 <= float(hdd) <= 1.0))
            or (band_tokens < settings["hdd_sample"] and hdd is not None)
            or (band_tokens >= settings["hdd_sample"] and hdd is None)
        ):
            raise AnalysisProtocolError("Worker Panel B band-wise values were invalid.")


def _validate_semantic_payload(
    value: Any,
    *,
    expected: bool,
    n_tokens: int,
    expected_normalizer: str,
) -> None:
    """Validate aggregate OEWN output and reject content-bearing extensions."""

    if not expected:
        if value is not None:
            raise AnalysisProtocolError("Worker semantic payload was unexpected.")
        return
    if not isinstance(value, dict) or frozenset(value) != _SEMANTIC_KEYS:
        raise AnalysisProtocolError("Worker semantic payload schema was invalid.")
    integer_fields = (
        "tokens",
        "types",
        "covered_tokens",
        "covered_types",
        "depth_covered_tokens",
        "depth_covered_types",
    )
    if any(not _plain_nonnegative_integer(value.get(field)) for field in integer_fields):
        raise AnalysisProtocolError("Worker semantic counts were invalid.")
    if not (
        value["tokens"] <= n_tokens
        and value["types"] <= value["tokens"]
        and value["covered_tokens"] <= value["tokens"]
        and value["covered_types"] <= value["types"]
        and value["depth_covered_tokens"] <= value["covered_tokens"]
        and value["depth_covered_types"] <= value["covered_types"]
    ):
        raise AnalysisProtocolError("Worker semantic counts were inconsistent.")
    for numerator, denominator, field in (
        (value["covered_tokens"], value["tokens"], "token_coverage"),
        (value["covered_types"], value["types"], "type_coverage"),
        (value["depth_covered_tokens"], value["tokens"], "depth_token_coverage"),
        (value["depth_covered_types"], value["types"], "depth_type_coverage"),
    ):
        expected_rate = numerator / denominator if denominator else 0.0
        if (
            not _finite_number(value.get(field))
            or not _close(value[field], expected_rate)
        ):
            raise AnalysisProtocolError("Worker semantic coverage was invalid.")
    for count_field, mean_field in (
        ("covered_tokens", "polysemy_token_mean"),
        ("covered_types", "polysemy_type_mean"),
        ("depth_covered_tokens", "hypernym_depth_token_mean"),
        ("depth_covered_types", "hypernym_depth_type_mean"),
    ):
        mean = value.get(mean_field)
        if value[count_field] == 0:
            if mean is not None:
                raise AnalysisProtocolError("Worker semantic conditional mean was invalid.")
        elif not _finite_number(mean) or float(mean) < 0.0:
            raise AnalysisProtocolError("Worker semantic conditional mean was invalid.")
    if (
        value.get("resource") != "Open English WordNet 2025"
        or value.get("license") != "CC BY 4.0"
        or value.get("lookup_pos")
        != "all parts of speech (POS-agnostic aggregation)"
        or value.get("normalizer") != expected_normalizer
    ):
        raise AnalysisProtocolError("Worker semantic metadata was invalid.")


def _validate_document_payload(
    item: Mapping[str, Any],
    *,
    name: str,
    expected_notes: list[str] | None,
    expected_settings: Mapping[str, Any] | None,
    panel_b_expected: bool | None,
    semantic_expected: bool | None,
    tubelex_expected: bool | None,
) -> None:
    payload = item.get("payload")
    if not isinstance(payload, dict) or payload.get("document") != {"name": name}:
        raise AnalysisProtocolError("Worker document payload schema was invalid.")
    if expected_notes is None:
        return
    if frozenset(payload) != _DOCUMENT_PAYLOAD_KEYS:
        raise AnalysisProtocolError("Worker document payload schema was invalid.")
    if (
        payload.get("ldfreq_version") != __version__
        or payload.get("output_schema_version") != OUTPUT_SCHEMA_VERSION
        or payload.get("n_tokens") != item.get("n_tokens")
        or payload.get("n_types") != item.get("n_types")
        or payload.get("panel_b") != item.get("panel_b")
        or payload.get("semantic_network") != item.get("semantic_network")
        or payload.get("tubelex") != item.get("tubelex")
        or payload.get("privacy")
        != {
            "source_text_retained": False,
            "source_filename_retained": False,
            "token_level_output_retained": False,
        }
    ):
        raise AnalysisProtocolError("Worker document payload values were invalid.")
    panel_a = payload.get("panel_a")
    indices = item.get("indices")
    if (
        not isinstance(panel_a, dict)
        or frozenset(panel_a) != _PANEL_A_KEYS
        or not all(_valid_optional_number(value) for value in panel_a.values())
        or indices != panel_a
    ):
        raise AnalysisProtocolError("Worker Panel A payload was invalid.")
    settings = payload.get("settings")
    if (
        not isinstance(settings, dict)
        or expected_settings is None
        or settings != expected_settings
    ):
        raise AnalysisProtocolError("Worker settings metadata was invalid.")
    records = payload.get("panel_a_records")
    if records != item.get("index_records"):
        raise AnalysisProtocolError("Worker Panel A records were inconsistent.")
    _validate_index_records(
        records,
        panel_a,
        n_tokens=item["n_tokens"],
        n_types=item["n_types"],
        settings=settings,
    )
    if panel_b_expected is not None:
        _validate_panel_b_payload(
            payload.get("panel_b"),
            expected=panel_b_expected,
            n_tokens=item["n_tokens"],
            n_types=item["n_types"],
            settings=settings,
        )
    if semantic_expected is not None:
        _validate_semantic_payload(
            payload.get("semantic_network"),
            expected=semantic_expected,
            n_tokens=item["n_tokens"],
            expected_normalizer=(
                f"{settings['lemmatizer']} {settings['lemmatizer_version']}"
            ),
        )
    if expected_notes is not None and payload.get("method_notes") != expected_notes:
        raise AnalysisProtocolError("Worker method metadata was invalid.")
    _validate_tubelex_payload(
        payload.get("tubelex"),
        expected=tubelex_expected,
    )


def _validate_tubelex_payload(value: Any, *, expected: bool | None) -> None:
    """Reject TUBELEX payloads that could become a request-derived side channel."""

    if expected is False:
        if value is not None:
            raise AnalysisProtocolError("Worker TUBELEX payload was unexpected.")
        return
    if expected is None:
        return
    if not isinstance(value, dict) or frozenset(value) != _TUBELEX_KEYS:
        raise AnalysisProtocolError("Worker TUBELEX payload schema was invalid.")

    integer_fields = ("tokens", "types", "covered_tokens", "covered_types")
    if any(
        isinstance(value.get(field), bool)
        or not isinstance(value.get(field), int)
        or value[field] < 0
        for field in integer_fields
    ):
        raise AnalysisProtocolError("Worker TUBELEX counts were invalid.")
    if not (
        value["types"] <= value["tokens"]
        and value["covered_tokens"] <= value["tokens"]
        and value["covered_types"] <= value["types"]
    ):
        raise AnalysisProtocolError("Worker TUBELEX counts were invalid.")

    for numerator, denominator, field in (
        (value["covered_tokens"], value["tokens"], "token_coverage"),
        (value["covered_types"], value["types"], "type_coverage"),
    ):
        observed = value.get(field)
        expected_rate = numerator / denominator if denominator else 0.0
        if (
            isinstance(observed, bool)
            or not isinstance(observed, (int, float))
            or not math.isfinite(float(observed))
            or not math.isclose(
                float(observed), expected_rate, rel_tol=0.0, abs_tol=1e-15
            )
        ):
            raise AnalysisProtocolError("Worker TUBELEX coverage was invalid.")

    mean_fields = (
        "frequency_zipf_token_mean",
        "frequency_zipf_type_mean",
        "video_log10_prevalence_token_mean",
        "video_log10_prevalence_type_mean",
        "channel_log10_prevalence_token_mean",
        "channel_log10_prevalence_type_mean",
    )
    for field in mean_fields:
        observed = value.get(field)
        if value["tokens"] == 0:
            if observed is not None:
                raise AnalysisProtocolError("Worker TUBELEX empty means were invalid.")
        elif not _valid_optional_number(observed) or observed is None:
            raise AnalysisProtocolError("Worker TUBELEX means were invalid.")

    metadata = value.get("metadata")
    expected_metadata = {
        "name": "TUBELEX-EN Treebank published frequency aggregates",
        "version": TUBELEX.TUBELEX_EN_SOURCE_COMMIT,
        "source_asset": TUBELEX.TUBELEX_EN_SOURCE_ASSET,
        "source_url": TUBELEX.TUBELEX_EN_SOURCE_URL,
        "source_sha256": TUBELEX.TUBELEX_EN_SOURCE_SHA256,
        "artifact_sha256": TUBELEX.PRODUCTION_ARTIFACT_SHA256,
        "license": TUBELEX.TUBELEX_REPOSITORY_LICENSE,
        "license_spdx": TUBELEX.TUBELEX_REPOSITORY_LICENSE_SPDX,
        "license_url": TUBELEX.TUBELEX_REPOSITORY_LICENSE_URL,
        "method_id": TUBELEX.TUBELEX_EN_METHOD_ID,
        "lookup_unit": "lower-cased Treebank surface/clitic lexical token",
        "normalization": TUBELEX.TUBELEX_RUNTIME_NORMALIZATION,
        "corpus_tokens": TUBELEX.TUBELEX_EN_SOURCE_TOTAL_TOKENS,
        "corpus_types": TUBELEX.TUBELEX_EN_SOURCE_VOCABULARY_SIZE,
        "corpus_videos": TUBELEX.TUBELEX_EN_SOURCE_TOTAL_VIDEOS,
        "corpus_channels": TUBELEX.TUBELEX_EN_SOURCE_TOTAL_CHANNELS,
        "runtime_index_rows": TUBELEX.PRODUCTION_ARTIFACT_ROWS,
        "retained_reference_token_mass": TUBELEX.TUBELEX_EN_RETAINED_TOKEN_MASS,
        "frequency_unseen_zipf": TUBELEX.TUBELEX_EN_FREQUENCY_UNSEEN_ZIPF,
        "video_unseen_log10_prevalence": (
            TUBELEX.TUBELEX_EN_VIDEO_UNSEEN_LOG10_PREVALENCE
        ),
        "channel_unseen_log10_prevalence": (
            TUBELEX.TUBELEX_EN_CHANNEL_UNSEEN_LOG10_PREVALENCE
        ),
    }
    if (
        not isinstance(metadata, dict)
        or frozenset(metadata) != _TUBELEX_METADATA_KEYS
        or metadata != expected_metadata
    ):
        raise AnalysisProtocolError("Worker TUBELEX metadata was invalid.")


def _validate_overlap_diagnostics(
    matrix: Any,
    pairs: Any,
    *,
    type_counts: Mapping[str, int],
) -> None:
    labels = tuple(type_counts)
    label_set = frozenset(labels)
    if not isinstance(matrix, list) or len(matrix) != len(labels) ** 2:
        raise AnalysisProtocolError("Worker overlap matrix schema was invalid.")
    observed_matrix: dict[tuple[str, str], tuple[int, int, float]] = {}
    for row in matrix:
        if not isinstance(row, dict) or frozenset(row) != _OVERLAP_MATRIX_ROW_KEYS:
            raise AnalysisProtocolError("Worker overlap matrix schema was invalid.")
        left = row.get("document_a")
        right = row.get("document_b")
        shared = row.get("shared_types")
        union = row.get("union_types")
        jaccard = row.get("jaccard")
        if not isinstance(left, str) or not isinstance(right, str):
            raise AnalysisProtocolError("Worker overlap matrix labels were invalid.")
        key = (left, right)
        if (
            left not in label_set
            or right not in label_set
            or key in observed_matrix
            or not _plain_nonnegative_integer(shared)
            or not _plain_nonnegative_integer(union)
            or shared > min(type_counts[left], type_counts[right])
            or union != type_counts[left] + type_counts[right] - shared
            or not _finite_number(jaccard)
            or not _close(jaccard, shared / union)
        ):
            raise AnalysisProtocolError("Worker overlap matrix values were invalid.")
        observed_matrix[key] = (shared, union, float(jaccard))
    expected_matrix_keys = {(left, right) for left in labels for right in labels}
    if frozenset(observed_matrix) != frozenset(expected_matrix_keys):
        raise AnalysisProtocolError("Worker overlap matrix labels were invalid.")
    for left in labels:
        for right in labels:
            if observed_matrix[(left, right)] != observed_matrix[(right, left)]:
                raise AnalysisProtocolError("Worker overlap matrix was not symmetric.")

    expected_pair_keys = {
        frozenset((labels[left], labels[right]))
        for left in range(len(labels))
        for right in range(left + 1, len(labels))
    }
    if not isinstance(pairs, list) or len(pairs) != len(expected_pair_keys):
        raise AnalysisProtocolError("Worker overlap-pair schema was invalid.")
    observed_pair_keys: set[frozenset[str]] = set()
    for row in pairs:
        if not isinstance(row, dict) or frozenset(row) != _OVERLAP_PAIR_ROW_KEYS:
            raise AnalysisProtocolError("Worker overlap-pair schema was invalid.")
        left = row.get("document_a")
        right = row.get("document_b")
        if not isinstance(left, str) or not isinstance(right, str):
            raise AnalysisProtocolError("Worker overlap-pair labels were invalid.")
        pair_key = frozenset((left, right))
        shared = row.get("shared_types")
        a_only = row.get("a_only_types")
        b_only = row.get("b_only_types")
        union = row.get("union_types")
        jaccard = row.get("jaccard")
        if (
            left not in label_set
            or right not in label_set
            or left == right
            or pair_key not in expected_pair_keys
            or pair_key in observed_pair_keys
            or not all(
                _plain_nonnegative_integer(item)
                for item in (shared, a_only, b_only, union)
            )
            or shared > min(type_counts[left], type_counts[right])
            or a_only != type_counts[left] - shared
            or b_only != type_counts[right] - shared
            or union != shared + a_only + b_only
            or not _finite_number(jaccard)
            or not _close(jaccard, shared / union)
            or observed_matrix[(left, right)][:2] != (shared, union)
        ):
            raise AnalysisProtocolError("Worker overlap-pair values were invalid.")
        observed_pair_keys.add(pair_key)
    if observed_pair_keys != expected_pair_keys:
        raise AnalysisProtocolError("Worker overlap-pair labels were invalid.")


def _validate_batch_diagnostics(
    diagnostics: Any,
    *,
    results: list[dict[str, Any]],
    config: AnalysisConfig,
) -> None:
    """Validate every retained multi-document diagnostic against fixed schemas."""

    if (
        not isinstance(diagnostics, dict)
        or frozenset(diagnostics) != _BATCH_DIAGNOSTIC_KEYS
    ):
        raise AnalysisProtocolError("Worker batch diagnostics schema was invalid.")
    expected_bands = BATCH.band_rows(results)
    bands = diagnostics.get("bands")
    if (
        not isinstance(bands, list)
        or any(
            not isinstance(row, dict) or frozenset(row) != _BATCH_BAND_ROW_KEYS
            for row in bands
        )
        or bands != expected_bands
    ):
        raise AnalysisProtocolError("Worker batch band diagnostics were invalid.")

    expected_reliability = BATCH.reliability_rows(
        results,
        segment=config.msttr_segment,
        window=config.mattr_window,
        hdd_sample=config.hdd_sample,
    )
    reliability = diagnostics.get("reliability")
    if (
        not isinstance(reliability, list)
        or any(
            not isinstance(row, dict) or frozenset(row) != _RELIABILITY_ROW_KEYS
            for row in reliability
        )
        or reliability != expected_reliability
    ):
        raise AnalysisProtocolError("Worker reliability diagnostics were invalid.")

    type_counts = {item["name"]: item["n_types"] for item in results}
    _validate_overlap_diagnostics(
        diagnostics.get("overlap_matrix"),
        diagnostics.get("overlap_pairs"),
        type_counts=type_counts,
    )


def _batch_from_message(
    message: Mapping[str, Any],
    *,
    expected_documents: int,
    config: AnalysisConfig | None = None,
    resources: ResourceSpec | None = None,
) -> AnalysisBatch:
    if frozenset(message) != {"type", "encoding", "results", "payload", "skipped"}:
        raise AnalysisProtocolError("Worker result envelope was invalid.")
    if message.get("type") != "result" or message.get("encoding") != RESULT_ENCODING:
        raise AnalysisProtocolError("Worker result encoding was invalid.")
    results = _wire_decode(message.get("results"))
    payload = _wire_decode(message.get("payload"))
    skipped = _wire_decode(message.get("skipped"))
    if (
        not isinstance(results, list)
        or not all(isinstance(item, dict) for item in results)
        or not isinstance(payload, dict)
        or not isinstance(skipped, list)
        or not all(isinstance(item, dict) for item in skipped)
        or len(results) + len(skipped) != expected_documents
    ):
        raise AnalysisProtocolError("Worker result schema was invalid.")
    expected_labels = {
        f"Document {index:03d}" for index in range(1, expected_documents + 1)
    }
    expected_notes = (
        _expected_method_notes(config, resources)
        if config is not None and resources is not None
        else None
    )
    expected_resource_contract = (
        _expected_resource_contract(config, resources)
        if config is not None and resources is not None
        else None
    )
    expected_settings = (
        expected_resource_contract["settings"]
        if expected_resource_contract is not None
        else None
    )
    observed_labels: set[str] = set()
    for item in results:
        if frozenset(item) != _RESULT_KEYS:
            raise AnalysisProtocolError("Worker document result schema was invalid.")
        name = item.get("name")
        if not isinstance(name, str) or name not in expected_labels or name in observed_labels:
            raise AnalysisProtocolError("Worker document label was invalid.")
        observed_labels.add(name)
        if (
            isinstance(item.get("n_tokens"), bool)
            or not isinstance(item.get("n_tokens"), int)
            or item["n_tokens"] <= 0
            or isinstance(item.get("n_types"), bool)
            or not isinstance(item.get("n_types"), int)
            or not 1 <= item["n_types"] <= item["n_tokens"]
        ):
            raise AnalysisProtocolError("Worker document aggregates were invalid.")
        _validate_document_payload(
            item,
            name=name,
            expected_notes=expected_notes,
            expected_settings=expected_settings,
            panel_b_expected=(
                resources.list_id is not None if resources is not None else None
            ),
            semantic_expected=(
                resources.semantic_enabled if resources is not None else None
            ),
            tubelex_expected=(
                resources.tubelex_enabled if resources is not None else None
            ),
        )
        list_path = item.get("list_path")
        if list_path is not None and (
            not isinstance(list_path, str) or Path(list_path).name != list_path
        ):
            raise AnalysisProtocolError("Worker resource label was invalid.")
        if expected_resource_contract is not None:
            for field in (
                "list_meta",
                "list_entry",
                "list_path",
                "effective_lemmatizer",
            ):
                if item.get(field) != expected_resource_contract[field]:
                    raise AnalysisProtocolError(
                        "Worker retained resource metadata was invalid."
                    )
    for item in skipped:
        if frozenset(item) != _SKIPPED_KEYS:
            raise AnalysisProtocolError("Worker skipped-document schema was invalid.")
        name = item.get("name")
        error = item.get("error")
        if (
            not isinstance(name, str)
            or name not in expected_labels
            or name in observed_labels
            or error not in _SKIP_ERRORS
        ):
            raise AnalysisProtocolError("Worker skipped-document value was invalid.")
        observed_labels.add(name)
    if observed_labels != expected_labels:
        raise AnalysisProtocolError("Worker document labels were incomplete.")
    batch = AnalysisBatch(
        results=tuple(results),
        payload=payload,
        skipped=tuple(skipped),
    )
    strict_payload = expected_notes is not None
    if (
        strict_payload
        and len(batch.results) == 1
        and batch.payload != batch.results[0]["payload"]
    ):
        raise AnalysisProtocolError("Worker single-document payload was inconsistent.")
    if strict_payload and len(batch.results) > 1:
        if (
            frozenset(batch.payload)
            != {
                "ldfreq_version",
                "output_schema_version",
                "batch",
                "batch_diagnostics",
                "documents",
            }
            or batch.payload.get("ldfreq_version") != __version__
            or batch.payload.get("output_schema_version") != OUTPUT_SCHEMA_VERSION
            or batch.payload.get("batch") != {"n_documents": len(batch.results)}
            or batch.payload.get("documents")
            != [item["payload"] for item in batch.results]
            or not isinstance(batch.payload.get("batch_diagnostics"), dict)
        ):
            raise AnalysisProtocolError("Worker batch payload was inconsistent.")
        _validate_batch_diagnostics(
            batch.payload["batch_diagnostics"],
            results=list(batch.results),
            config=config,
        )
    if strict_payload and not batch.results and (
        frozenset(batch.payload)
        != {"ldfreq_version", "output_schema_version", "batch", "documents"}
        or batch.payload.get("ldfreq_version") != __version__
        or batch.payload.get("output_schema_version") != OUTPUT_SCHEMA_VERSION
        or batch.payload.get("batch") != {"n_documents": 0}
        or batch.payload.get("documents") != []
    ):
        raise AnalysisProtocolError("Worker empty payload was inconsistent.")
    decoded_envelope = {
        "results": batch.results,
        "payload": batch.payload,
        "skipped": batch.skipped,
    }
    if sensitive_paths(decoded_envelope):
        raise AnalysisProtocolError("Worker result violated the privacy schema.")
    return batch


def _signal_process_group(process: subprocess.Popen[bytes], sig: int) -> None:
    try:
        os.killpg(process.pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        if process.poll() is not None:
            return
        try:
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()
        except OSError:
            pass


def _reap_process(
    process: subprocess.Popen[bytes] | None,
    *,
    graceful: bool,
    grace_seconds: float,
    deadline: float,
) -> None:
    if process is None:
        return
    if process.poll() is not None:
        if not graceful:
            # The group leader may have exited after creating a descendant.
            _signal_process_group(process, signal.SIGKILL)
        process.wait()
        return
    if graceful and process.poll() is None:
        try:
            process.wait(timeout=min(grace_seconds, _remaining(deadline)))
        except subprocess.TimeoutExpired:
            pass
    if process.poll() is None:
        if _remaining(deadline) <= 0:
            _signal_process_group(process, signal.SIGKILL)
        else:
            _signal_process_group(process, signal.SIGTERM)
            try:
                process.wait(timeout=min(grace_seconds, _remaining(deadline)))
            except subprocess.TimeoutExpired:
                _signal_process_group(process, signal.SIGKILL)
        process.wait()
    else:
        process.wait()


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def analyze_documents_isolated(
    documents: Iterable[TextDocument | str | Mapping[str, Any]],
    config: AnalysisConfig | None = None,
    resources: ResourceSpec | None = None,
    *,
    limits: IsolationLimits | None = None,
    progress: ProgressCallback | None = None,
    cancel: CancelCallback | None = None,
    _on_worker_started: Callable[[int], None] | None = None,
) -> AnalysisBatch:
    """Analyze in a one-shot subprocess and always reap it before returning.

    The deadline starts before validation/serialization and includes child
    startup, resource loading, analysis, response transfer, and validation.
    ``cancel`` is a cooperative hook for a future serving adapter; Streamlit
    does not currently guarantee a disconnect callback, so the hard deadline is
    still the final backstop.
    """

    started_at = time.monotonic()
    if os.name != "posix":
        raise AnalysisIsolationError("Isolated analysis requires a POSIX host.")
    effective_config = config or AnalysisConfig()
    effective_resources = resources or ResourceSpec()
    effective_limits = limits or IsolationLimits()
    deadline = started_at + effective_limits.deadline_seconds
    # Do not serialize and retain another essay while this process is already
    # occupied.  Cloud Run concurrency is separately pinned to one.
    if not _SINGLE_FLIGHT.acquire(blocking=False):
        raise AnalysisBusy("The isolated analysis worker is busy.")

    process: subprocess.Popen[bytes] | None = None
    event_read: int | None = None
    event_write: int | None = None
    selector: selectors.BaseSelector | None = None
    deadline_stop: threading.Event | None = None
    deadline_thread: threading.Thread | None = None
    result: AnalysisBatch | None = None
    graceful = False
    try:
        if cancel is not None and cancel():
            raise AnalysisCancelled("Analysis was cancelled.")
        if _remaining(deadline) <= 0:
            raise AnalysisDeadlineExceeded("Analysis exceeded its processing deadline.")
        request, expected_documents = _request_bytes(
            documents,
            effective_config,
            effective_resources,
            effective_limits,
        )
        if _remaining(deadline) <= 0:
            raise AnalysisDeadlineExceeded("Analysis exceeded its processing deadline.")

        event_read, event_write = os.pipe()
        environment = _worker_environment(event_write)
        try:
            process = subprocess.Popen(
                [sys.executable, "-B", "-s", "-m", WORKER_MODULE],
                cwd=PROJECT_ROOT,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                close_fds=True,
                pass_fds=(event_write,),
                start_new_session=True,
                bufsize=0,
            )
        except (OSError, subprocess.SubprocessError):
            raise AnalysisWorkerError("internal-error") from None
        deadline_stop = threading.Event()

        def stop_worker_at_deadline() -> None:
            assert deadline_stop is not None
            assert process is not None
            if not deadline_stop.wait(_remaining(deadline)):
                # Independent of the Streamlit callback thread: even a blocked
                # progress/cancel callback cannot let source processing continue
                # beyond the configured wall-clock deadline.
                _signal_process_group(process, signal.SIGKILL)

        watchdog = threading.Thread(
            target=stop_worker_at_deadline,
            name="ldfreq-analysis-deadline",
            daemon=True,
        )
        watchdog.start()
        deadline_thread = watchdog
        os.close(event_write)
        event_write = None
        if _on_worker_started is not None:
            _on_worker_started(process.pid)
        if process.stdin is None:
            raise AnalysisWorkerError("internal-error")

        input_fd = process.stdin.fileno()
        os.set_blocking(input_fd, False)
        os.set_blocking(event_read, False)
        selector = selectors.DefaultSelector()
        selector.register(input_fd, selectors.EVENT_WRITE, "input")
        selector.register(event_read, selectors.EVENT_READ, "events")
        outgoing = memoryview(request)
        sent = 0
        incoming = bytearray()
        event_eof = False
        received_bytes = 0
        progress_events = 0
        last_completed = 0
        expected_total: int | None = None

        def handle_messages() -> None:
            nonlocal result, progress_events, last_completed, expected_total
            for message in _pop_frames(
                incoming,
                effective_limits.max_response_bytes,
            ):
                if result is not None:
                    raise AnalysisProtocolError("Worker emitted data after its result.")
                event_type = message.get("type")
                if event_type == "progress":
                    if frozenset(message) != {"type", "completed", "total"}:
                        raise AnalysisProtocolError("Worker progress envelope was invalid.")
                    completed = message.get("completed")
                    total = message.get("total")
                    if (
                        isinstance(completed, bool)
                        or not isinstance(completed, int)
                        or isinstance(total, bool)
                        or not isinstance(total, int)
                        or total != expected_documents
                        or completed != last_completed + 1
                    ):
                        raise AnalysisProtocolError("Worker progress was invalid.")
                    if expected_total is not None and total != expected_total:
                        raise AnalysisProtocolError("Worker progress total changed.")
                    expected_total = total
                    progress_events += 1
                    if progress_events > expected_documents:
                        raise AnalysisProtocolError("Worker emitted too many progress events.")
                    last_completed = completed
                    if progress is not None:
                        progress(completed, total, f"Document {completed:03d}")
                elif event_type == "result":
                    result = _batch_from_message(
                        message,
                        expected_documents=expected_documents,
                        config=effective_config,
                        resources=effective_resources,
                    )
                elif event_type == "error":
                    if frozenset(message) != {"type", "code"}:
                        raise AnalysisProtocolError("Worker error envelope was invalid.")
                    code = message.get("code")
                    raise AnalysisWorkerError(
                        code if isinstance(code, str) else "internal-error"
                    )
                else:
                    raise AnalysisProtocolError("Worker event type was invalid.")

        while True:
            if cancel is not None and cancel():
                raise AnalysisCancelled("Analysis was cancelled.")
            remaining = _remaining(deadline)
            if remaining <= 0:
                raise AnalysisDeadlineExceeded("Analysis exceeded its processing deadline.")

            handle_messages()
            return_code = process.poll()
            if event_eof and return_code is not None:
                if incoming:
                    raise AnalysisProtocolError("Worker left an incomplete event frame.")
                if result is None:
                    raise AnalysisWorkerError("internal-error")
                if return_code != 0:
                    raise AnalysisWorkerError("internal-error")
                if progress_events != expected_documents:
                    raise AnalysisProtocolError("Worker progress sequence was incomplete.")
                graceful = True
                return result

            events = selector.select(min(effective_limits.poll_seconds, remaining))
            for key, _mask in events:
                if key.data == "input":
                    try:
                        written = os.write(input_fd, outgoing[sent:])
                    except BlockingIOError:
                        written = 0
                    except (BrokenPipeError, OSError):
                        selector.unregister(input_fd)
                        process.stdin.close()
                        raise AnalysisWorkerError("internal-error") from None
                    sent += written
                    if sent == len(outgoing):
                        selector.unregister(input_fd)
                        process.stdin.close()
                        outgoing.release()
                        request = b""
                else:
                    try:
                        chunk = os.read(event_read, 64 * 1024)
                    except BlockingIOError:
                        chunk = None
                    except OSError:
                        chunk = b""
                    if chunk is None:
                        continue
                    if not chunk:
                        if not event_eof:
                            selector.unregister(event_read)
                        event_eof = True
                        continue
                    received_bytes += len(chunk)
                    if received_bytes > effective_limits.max_response_bytes:
                        raise AnalysisProtocolError("Worker event stream exceeded its limit.")
                    incoming.extend(chunk)
                    handle_messages()
    finally:
        if deadline_stop is not None:
            deadline_stop.set()
        if deadline_thread is not None:
            deadline_thread.join()
        if selector is not None:
            selector.close()
        if process is not None and process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        if event_read is not None:
            try:
                os.close(event_read)
            except OSError:
                pass
        if event_write is not None:
            try:
                os.close(event_write)
            except OSError:
                pass
        _reap_process(
            process,
            graceful=graceful,
            grace_seconds=effective_limits.termination_grace_seconds,
            deadline=deadline,
        )
        _SINGLE_FLIGHT.release()
