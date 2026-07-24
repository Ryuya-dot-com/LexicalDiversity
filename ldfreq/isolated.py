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
from .analysis import AnalysisBatch, AnalysisConfig, TextDocument
from .privacy import AGGREGATE_RESULT_KEYS, sensitive_paths
from . import tubelex as TUBELEX


PROTOCOL_VERSION = 1
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
    "LDFREQ_NJ8_PATH",
    "LDFREQ_ANTBNC_PATH",
    "LDFREQ_BNCCOCA_PATH",
    "LDFREQ_BNCCOCA_FAMILIES_PATH",
    "LDFREQ_RANGE_PATH",
    "LDFREQ_NGSL_PATH",
    "LDFREQ_NATION_BNCCOCA_INDEX_PATH",
    "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
)
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
    for name in (*_RESOURCE_ENV_NAMES, *_RUNTIME_ENV_NAMES):
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
    ]
    if resources.lemmatizer_name == "antbnc":
        notes.append(
            "AntBNC mode is an NWLC approximation, not bit-identical to New Word "
            "Level Checker."
        )
    if resources.list_id is None:
        return notes

    server_ids = {
        item.strip()
        for item in os.environ.get("LDFREQ_SERVER_ONLY_RESOURCE_IDS", "").split(",")
        if item.strip()
    }
    if (
        os.environ.get("LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED") == "1"
        and resources.list_id in {"bnc_coca", "nation_bnc_coca_families"}
        and resources.list_id in server_ids
    ):
        notes.append(
            "Server-only lookup requires at least 100 lexical tokens and 20 distinct "
            "surface types per document."
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
            "Panel B maps tokens to flemmas/head forms before frequency-list lookup."
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
                "Coverage can differ from LexTutor unless the frequency list, word-family "
                "expansion, tokenizer, proper-noun/number policy, and lemmatizer all match."
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


def _valid_optional_number(value: Any) -> bool:
    return value is None or (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _validate_document_payload(
    item: Mapping[str, Any],
    *,
    name: str,
    expected_notes: list[str] | None,
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
    if expected_notes is not None and payload.get("method_notes") != expected_notes:
        raise AnalysisProtocolError("Worker method metadata was invalid.")
    if not isinstance(payload.get("settings"), dict):
        raise AnalysisProtocolError("Worker settings metadata was invalid.")
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
            tubelex_expected=(
                resources.tubelex_enabled if resources is not None else None
            ),
        )
        list_path = item.get("list_path")
        if list_path is not None and (
            not isinstance(list_path, str) or Path(list_path).name != list_path
        ):
            raise AnalysisProtocolError("Worker resource label was invalid.")
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
