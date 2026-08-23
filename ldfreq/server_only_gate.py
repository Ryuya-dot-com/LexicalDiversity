"""Fail-closed activation contract for public server-only resources.

The control evidence ID is a short reference to an externally retained review
record.  Accepting its shape proves only that an operator supplied the fixed
attestation profile and a reference; this module does not contact, inspect, or
verify a shared limiter, account quota, audit system, anomaly rule, or
extraction-resistance test environment.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import os
import re


SERVER_ONLY_RESOURCE_IDS_ENV = "LDFREQ_SERVER_ONLY_RESOURCE_IDS"
SERVER_ONLY_RIGHTS_ACK_ENV = "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED"
SERVER_ONLY_CONTROL_ATTESTATION_ENV = (
    "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION"
)
SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV = (
    "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID"
)
SERVER_ONLY_CONTROL_PROFILE = "shared-abuse-controls-v1"
SERVER_ONLY_ELIGIBLE_IDS = frozenset({
    "bnc_coca",
    "nation_bnc_coca_families",
})

_EVIDENCE_ID_RE = re.compile(
    r"[A-Z][A-Z0-9]{1,11}(?:-[A-Z0-9]{2,16}){1,5}"
)
_EVIDENCE_ID_MIN_LENGTH = 8
_EVIDENCE_ID_MAX_LENGTH = 64
_PROHIBITED_EVIDENCE_SEGMENTS = frozenset(
    {
        "DEMO",
        "DUMMY",
        "EXAMPLE",
        "FAKE",
        "HASH",
        "KEY",
        "NONE",
        "NULL",
        "PASSWORD",
        "PENDING",
        "PLACEHOLDER",
        "SAMPLE",
        "SECRET",
        "SHA",
        "TBD",
        "TEST",
        "TOKEN",
        "TODO",
        "UNKNOWN",
    }
)
_PROHIBITED_EVIDENCE_PREFIXES = (
    "CHANGEME",
    "DEMO",
    "DUMMY",
    "EXAMPLE",
    "FAKE",
    "HASH",
    "PENDING",
    "PLACEHOLDER",
    "SAMPLE",
    "TBD",
    "TEST",
    "TODO",
    "UNKNOWN",
)
_SENSITIVE_EVIDENCE_MARKERS = (
    "APIKEY",
    "CREDENTIAL",
    "PASSWORD",
    "PASSWD",
    "PRIVATEKEY",
    "SECRET",
    "TOKEN",
)


def valid_control_evidence_id(value: object) -> bool:
    """Whether *value* has the bounded, non-path external-reference shape."""

    if not isinstance(value, str) or value != value.strip():
        return False
    if not _EVIDENCE_ID_MIN_LENGTH <= len(value) <= _EVIDENCE_ID_MAX_LENGTH:
        return False
    if _EVIDENCE_ID_RE.fullmatch(value) is None:
        return False
    segments = value.split("-")
    if _PROHIBITED_EVIDENCE_SEGMENTS & set(segments):
        return False
    if any(
        segment.startswith(_PROHIBITED_EVIDENCE_PREFIXES)
        or any(marker in segment for marker in _SENSITIVE_EVIDENCE_MARKERS)
        for segment in segments
    ):
        return False
    compact = value.replace("-", "")
    return not (
        len(compact) >= 24 and re.fullmatch(r"[A-F0-9]+", compact) is not None
    )


def controls_attested(environment: Mapping[str, str] | None = None) -> bool:
    """Check the fixed operator declaration without claiming external verification."""

    values = os.environ if environment is None else environment
    return (
        values.get(SERVER_ONLY_RIGHTS_ACK_ENV) == "1"
        and values.get(SERVER_ONLY_CONTROL_ATTESTATION_ENV)
        == SERVER_ONLY_CONTROL_PROFILE
        and valid_control_evidence_id(
            values.get(SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV)
        )
    )


def configured_server_only_ids(
    eligible_ids: Iterable[str],
    environment: Mapping[str, str] | None = None,
) -> frozenset[str]:
    """Return enabled eligible IDs only after every activation gate passes."""

    values = os.environ if environment is None else environment
    if not controls_attested(values):
        return frozenset()
    requested = {
        item.strip()
        for item in values.get(SERVER_ONLY_RESOURCE_IDS_ENV, "").split(",")
        if item.strip()
    }
    eligible = frozenset(eligible_ids)
    if not requested or not requested <= eligible:
        return frozenset()
    return frozenset(requested)


__all__ = [
    "SERVER_ONLY_CONTROL_ATTESTATION_ENV",
    "SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV",
    "SERVER_ONLY_CONTROL_PROFILE",
    "SERVER_ONLY_ELIGIBLE_IDS",
    "SERVER_ONLY_RESOURCE_IDS_ENV",
    "SERVER_ONLY_RIGHTS_ACK_ENV",
    "configured_server_only_ids",
    "controls_attested",
    "valid_control_evidence_id",
]
