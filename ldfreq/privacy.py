"""Privacy helpers for retaining analysis results without source text.

The analysis pipeline may use token and lookup sequences while a request is being
processed.  Only aggregate values should survive in Streamlit Session State.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


SENSITIVE_RESULT_KEYS = frozenset({
    "a_tokens",
    "raw_tokens",
    "raw_surfaces",
    "text",
})
AGGREGATE_RESULT_KEYS = frozenset(
    {
        "name",
        "n_tokens",
        "n_types",
        "indices",
        "index_records",
        "panel_b",
        "semantic_network",
        "tubelex",
        "list_meta",
        "list_entry",
        "list_path",
        "effective_lemmatizer",
        "payload",
    }
)


def pseudonymize_documents(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return request documents labelled by upload order, without filenames."""
    output: list[dict[str, Any]] = []
    for index, document in enumerate(documents, start=1):
        copied = dict(document)
        copied["name"] = f"Document {index:03d}"
        output.append(copied)
    return output


def retain_aggregate_result(result: dict[str, Any]) -> dict[str, Any]:
    """Strip token-level material before a result enters long-lived UI state."""
    # Positive allow-list: a future transient field is discarded by default,
    # even if its developer forgets to add a new name to SENSITIVE_RESULT_KEYS.
    retained = {
        key: value for key, value in result.items() if key in AGGREGATE_RESULT_KEYS
    }
    panel_b = retained.get("panel_b")
    if panel_b is not None:
        retained["panel_b"] = {
            key: value
            for key, value in panel_b.items()
            if not str(key).startswith("_")
        }
    list_path = retained.get("list_path")
    if list_path:
        retained["list_path"] = Path(str(list_path)).name
    list_entry = retained.get("list_entry")
    if isinstance(list_entry, dict):
        retained["list_entry"] = {
            key: value
            for key, value in list_entry.items()
            if key not in {"available", "loader", "path"} and not callable(value)
        }
    return retained


def sensitive_paths(value: Any, path: str = "$") -> list[str]:
    """Return paths to fields that must not be retained after analysis."""
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child_path = f"{path}.{key}"
            if key in SENSITIVE_RESULT_KEYS or str(key).startswith("_"):
                found.append(child_path)
            found.extend(sensitive_paths(item, child_path))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(sensitive_paths(item, f"{path}[{index}]"))
    return found
