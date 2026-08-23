"""Private one-shot worker for :mod:`ldfreq.isolated`.

The worker accepts exactly one length-framed JSON request on stdin and writes
aggregate-only progress/result frames to a dedicated inherited descriptor.
Ordinary stdout and stderr are disabled by the parent.  No request-derived
value is placed in argv, the environment, a file, or an exception response.
"""
from __future__ import annotations

import json
import os
import re
import struct
import sys
from typing import Any, BinaryIO, Mapping

try:
    import resource
except ModuleNotFoundError:  # pragma: no cover - unavailable on non-POSIX hosts
    resource = None


PROTOCOL_VERSION = 2
MIB = 1024 * 1024
MAX_SOURCE_BYTES = 20 * MIB
MAX_REQUEST_BYTES = 48 * MIB
MAX_RESPONSE_BYTES = 32 * MIB
MAX_DOCUMENTS = 200
EVENT_FD_ENV = "LDFREQ_WORKER_EVENT_FD"
RESULT_ENCODING = "typed-map-v1"
MAP_TAG = "__ldfreq_typed_map_v1__"

_REQUEST_KEYS = frozenset({"version", "documents", "config", "resources"})
_RESOURCE_KEYS = frozenset(
    {"list_id", "lemmatizer_name", "semantic_enabled", "tubelex_enabled"}
)
_CONFIG_KEYS = frozenset(
    {
        "thresholds",
        "min_tokens",
        "msttr_segment",
        "mattr_window",
        "mtld_threshold",
        "hdd_sample",
        "vocd_seed",
        "advanced_cutoff",
        "unit",
        "tokenizer_policy",
    }
)
_LIST_ID_RE = re.compile(r"^[a-z0-9_]{1,64}$")


class _InvalidRequest(Exception):
    pass


class _ResourceUnavailable(Exception):
    pass


class _ResponseTooLarge(Exception):
    pass


def _read_exact(stream: BinaryIO, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            raise _InvalidRequest
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _json_loads(payload: bytes) -> Any:
    def reject_constant(_value: str) -> None:
        raise ValueError

    try:
        return json.loads(payload.decode("utf-8"), parse_constant=reject_constant)
    except (UnicodeDecodeError, ValueError, TypeError, RecursionError):
        raise _InvalidRequest from None


def _read_request() -> dict[str, Any]:
    stream = sys.stdin.buffer
    header = _read_exact(stream, 4)
    size = struct.unpack(">I", header)[0]
    if size <= 0 or size + 4 > MAX_REQUEST_BYTES:
        raise _InvalidRequest
    request = _json_loads(_read_exact(stream, size))
    if stream.read(1):
        raise _InvalidRequest
    if not isinstance(request, dict) or frozenset(request) != _REQUEST_KEYS:
        raise _InvalidRequest
    version = request.get("version")
    if isinstance(version, bool) or version != PROTOCOL_VERSION:
        raise _InvalidRequest

    documents = request.get("documents")
    if (
        not isinstance(documents, list)
        or not 1 <= len(documents) <= MAX_DOCUMENTS
        or not all(isinstance(text, str) for text in documents)
    ):
        raise _InvalidRequest
    try:
        source_bytes = sum(len(text.encode("utf-8")) for text in documents)
    except UnicodeEncodeError:
        raise _InvalidRequest from None
    if source_bytes > MAX_SOURCE_BYTES:
        raise _InvalidRequest

    config = request.get("config")
    resources = request.get("resources")
    if not isinstance(config, dict) or frozenset(config) != _CONFIG_KEYS:
        raise _InvalidRequest
    if not isinstance(resources, dict) or frozenset(resources) != _RESOURCE_KEYS:
        raise _InvalidRequest
    return request


def _event_stream() -> BinaryIO:
    raw_fd = os.environ.get(EVENT_FD_ENV)
    if raw_fd is None:
        raise _InvalidRequest
    try:
        fd = int(raw_fd)
    except (TypeError, ValueError):
        raise _InvalidRequest from None
    if fd < 3:
        raise _InvalidRequest
    try:
        # ``pass_fds`` makes the descriptor inheritable for this exec.  Restore
        # close-on-exec before importing analysis dependencies so any future
        # helper subprocess cannot keep the event pipe open accidentally.
        os.set_inheritable(fd, False)
        os.environ.pop(EVENT_FD_ENV, None)
        return os.fdopen(fd, "wb", buffering=0)
    except OSError:
        raise _InvalidRequest from None


def _wire_encode(value: Any) -> Any:
    """Encode mappings without losing integer key types in JSON."""

    if isinstance(value, Mapping):
        pairs: list[list[Any]] = []
        for key, item in value.items():
            if isinstance(key, str):
                typed_key: list[Any] = ["s", key]
            elif isinstance(key, int) and not isinstance(key, bool):
                typed_key = ["i", key]
            else:
                raise _ResponseTooLarge
            pairs.append([typed_key, _wire_encode(item)])
        return {MAP_TAG: pairs}
    if isinstance(value, (list, tuple)):
        return [_wire_encode(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise _ResponseTooLarge


def _encode_frame(message: Mapping[str, Any]) -> bytes:
    try:
        payload = json.dumps(
            message,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError):
        raise _ResponseTooLarge from None
    if not payload or len(payload) > MAX_RESPONSE_BYTES:
        raise _ResponseTooLarge
    return struct.pack(">I", len(payload)) + payload


def _write_all(stream: BinaryIO, payload: bytes) -> None:
    view = memoryview(payload)
    sent = 0
    while sent < len(view):
        written = stream.write(view[sent:])
        if written is None or written <= 0:
            raise BrokenPipeError
        sent += written


class _EventChannel:
    """Bound the complete event stream, not merely each individual frame."""

    def __init__(self, stream: BinaryIO) -> None:
        self.stream = stream
        self.sent_bytes = 0

    def send(self, message: Mapping[str, Any]) -> None:
        frame = _encode_frame(message)
        if self.sent_bytes + len(frame) > MAX_RESPONSE_BYTES:
            raise _ResponseTooLarge
        _write_all(self.stream, frame)
        self.sent_bytes += len(frame)


def _safe_list_entry(
    entry: Mapping[str, Any],
    *,
    delivery_mode: str,
) -> dict[str, Any]:
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
    )
    safe = {key: entry.get(key) for key in public_fields if key in entry}
    safe["delivery_mode"] = delivery_mode
    return safe


def _resources_from_request(spec: Mapping[str, Any]):
    # Imports happen only after the complete request has passed framing, size,
    # and primitive schema validation.  All paths come from the worker registry
    # or its operator-controlled environment, never from request JSON.
    from .analysis import AnalysisResources
    from . import lemmatizers as LEM
    from . import semantic_network as SEMANTIC
    from . import tubelex as TUBELEX
    from . import wordlists as WL

    list_id = spec.get("list_id")
    lemmatizer_name = spec.get("lemmatizer_name")
    semantic_enabled = spec.get("semantic_enabled")
    tubelex_enabled = spec.get("tubelex_enabled")
    if list_id is not None and (
        not isinstance(list_id, str) or not _LIST_ID_RE.fullmatch(list_id)
    ):
        raise _InvalidRequest
    if lemmatizer_name not in {"open_flemma", "simplemma", "word_form", "antbnc"}:
        raise _InvalidRequest
    if not isinstance(semantic_enabled, bool):
        raise _InvalidRequest
    if not isinstance(tubelex_enabled, bool):
        raise _InvalidRequest

    rank = meta = list_path = list_entry = None
    if list_id is not None:
        local_mode = os.environ.get("LDFREQ_SERVING_MODE") == "local"
        include_restricted = (
            local_mode
            and os.environ.get("LDFREQ_ALLOW_LOCAL_RESTRICTED") == "1"
        )
        entry = WL.available_by_id(
            list_id,
            include_restricted=include_restricted,
        )
        if entry is None:
            raise _ResourceUnavailable
        try:
            rank, meta = entry["loader"](entry["path"])
        except BaseException:
            raise _ResourceUnavailable from None
        server_only = list_id in WL.server_only_resource_ids()
        delivery_mode = (
            "server-side-only"
            if server_only
            else "local-private"
            if include_restricted and not entry.get("public_web", False)
            else "bundled-public"
        )
        list_entry = _safe_list_entry(entry, delivery_mode=delivery_mode)
        list_path = entry["path"]

    semantic_index = None
    if semantic_enabled or lemmatizer_name == "open_flemma":
        try:
            semantic_index = SEMANTIC.load_verified_semantic_network_index()
        except BaseException:
            raise _ResourceUnavailable from None

    if lemmatizer_name == "antbnc":
        if (
            os.environ.get("LDFREQ_SERVING_MODE") != "local"
            or os.environ.get("LDFREQ_ALLOW_LOCAL_RESTRICTED") != "1"
        ):
            raise _ResourceUnavailable
        antbnc_path = os.environ.get("LDFREQ_ANTBNC_PATH")
        if not antbnc_path:
            raise _ResourceUnavailable
        try:
            lemmatizer = LEM.build("antbnc", antbnc_path=antbnc_path)
        except BaseException:
            raise _ResourceUnavailable from None
        if not getattr(lemmatizer, "loaded", False):
            raise _ResourceUnavailable
    else:
        try:
            lemmatizer = LEM.build(
                str(lemmatizer_name),
                extra_heads=(
                    semantic_index.lemmas
                    if lemmatizer_name == "open_flemma" and semantic_index is not None
                    else ()
                ),
            )
        except BaseException:
            raise _ResourceUnavailable from None

    if not semantic_enabled:
        semantic_index = None

    tubelex_index = None
    if tubelex_enabled:
        try:
            tubelex_index = TUBELEX.load_verified_tubelex_index(
                TUBELEX.DEFAULT_ARTIFACT_PATH,
                expected_artifact_sha256=TUBELEX.PRODUCTION_ARTIFACT_SHA256,
                expected_artifact_bytes=TUBELEX.PRODUCTION_ARTIFACT_BYTES,
                expected_source_sha256=TUBELEX.TUBELEX_EN_SOURCE_SHA256,
                expected_resource_id=TUBELEX.PRODUCTION_RESOURCE_ID,
            )
        except BaseException:
            raise _ResourceUnavailable from None

    try:
        return AnalysisResources(
            lemmatizer=lemmatizer,
            rank_map=rank,
            list_meta=meta,
            list_entry=list_entry,
            list_path=list_path,
            semantic_index=semantic_index,
            tubelex_index=tubelex_index,
        )
    except BaseException:
        raise _ResourceUnavailable from None


def _run(request: Mapping[str, Any], events: _EventChannel) -> None:
    from .analysis import AnalysisConfig, TextDocument, analyze_documents
    from .privacy import sensitive_paths

    try:
        config = AnalysisConfig(**request["config"])
    except BaseException:
        raise _InvalidRequest from None
    resources = _resources_from_request(request["resources"])

    def progress(completed: int, total: int, _label: str) -> None:
        events.send(
            {"type": "progress", "completed": int(completed), "total": int(total)},
        )

    try:
        batch = analyze_documents(
            [TextDocument(text) for text in request["documents"]],
            config,
            resources=resources,
            progress=progress,
        )
    except (_InvalidRequest, _ResourceUnavailable, _ResponseTooLarge):
        raise
    except BaseException:
        raise RuntimeError from None

    response = {
        "type": "result",
        "encoding": RESULT_ENCODING,
        "results": _wire_encode(list(batch.results)),
        "payload": _wire_encode(batch.payload),
        "skipped": _wire_encode(list(batch.skipped)),
    }
    if sensitive_paths(
        {"results": batch.results, "payload": batch.payload, "skipped": batch.skipped}
    ):
        raise RuntimeError
    events.send(response)


def main() -> int:
    events: _EventChannel | None = None
    error_code = "internal-error"
    try:
        if resource is None:
            return 1
        events = _EventChannel(_event_stream())
        # Disable request-bearing core dumps before reading a single source byte.
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        os.umask(0o077)
        request = _read_request()
        _run(request, events)
        return 0
    except _InvalidRequest:
        error_code = "invalid-request"
    except _ResourceUnavailable:
        error_code = "resource-unavailable"
    except _ResponseTooLarge:
        error_code = "response-too-large"
    except BaseException:
        error_code = "internal-error"

    if events is not None:
        try:
            events.send({"type": "error", "code": error_code})
        except BaseException:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
