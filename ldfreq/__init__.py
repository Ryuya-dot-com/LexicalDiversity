"""ldfreq — Lexical Diversity & frequency-profile analysis core.

Code is released under the MIT License (see LICENSE).
Bundled word-list and lemmatizer data is governed separately (see data/*/manifest.json).
"""
from __future__ import annotations

import json
from importlib.resources import files
from types import MappingProxyType
from typing import Any


def _load_release_identity() -> dict[str, Any]:
    path = files(__package__).joinpath("release.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):  # pragma: no cover - fail-closed import boundary
        raise RuntimeError("ldfreq release identity must be a JSON object")
    return document


_RELEASE_IDENTITY = _load_release_identity()
RELEASE_IDENTITY = MappingProxyType(_RELEASE_IDENTITY)
__version__ = str(_RELEASE_IDENTITY["application_version"])
OUTPUT_SCHEMA_VERSION = str(_RELEASE_IDENTITY["output_schema_version"])
TARGET_APPLICATION_RELEASE = str(_RELEASE_IDENTITY["target_application_release"])
RELEASE_PHASE = str(_RELEASE_IDENTITY["release_phase"])

__all__ = [
    "OUTPUT_SCHEMA_VERSION",
    "RELEASE_IDENTITY",
    "RELEASE_PHASE",
    "TARGET_APPLICATION_RELEASE",
    "__version__",
]
