"""Export helpers for analysis payloads."""
from __future__ import annotations

import json
import math
import re
import zipfile
from datetime import datetime
from io import BytesIO
from typing import Any

import pandas as pd

from . import indices as IDX


EXPORT_FLOAT_DECIMAL_PLACES = 12
JSON_TERMINAL_NEWLINE = True
XLSX_DOCUMENT_DATETIME = datetime(1980, 1, 1, 0, 0, 0)
XLSX_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
XLSX_ZIP_COMPRESSION_LEVEL = 9
XLSX_CORE_TIMESTAMP = b"1980-01-01T00:00:00Z"
_XLSX_CORE_TIME_PATTERNS = (
    re.compile(rb"(<dcterms:created\b[^>]*>)[^<]*(</dcterms:created>)"),
    re.compile(rb"(<dcterms:modified\b[^>]*>)[^<]*(</dcterms:modified>)"),
)

_PANEL_A_RECORD_FIELDS = frozenset(
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
_PANEL_A_STANDARD_KEYS = frozenset(IDX._FUNCS)
_PANEL_A_ALLOWED_KEYS = _PANEL_A_STANDARD_KEYS | frozenset(
    IDX.ADAPTIVE_METHOD_IDS
)
_PANEL_A_MISSING_REASONS = frozenset(
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


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _validated_panel_a_records(
    records: Any,
    values: Any,
) -> dict[str, dict[str, Any]]:
    """Validate the schema-2 structured records used by public serializers."""

    error = "Panel A schema 2.0 requires complete, consistent structured records"
    if not isinstance(records, dict) or not isinstance(values, dict):
        raise ValueError(error)
    record_keys = frozenset(records)
    if (
        not _PANEL_A_STANDARD_KEYS.issubset(record_keys)
        or not record_keys.issubset(_PANEL_A_ALLOWED_KEYS)
        or frozenset(values) != record_keys
    ):
        raise ValueError(error)

    for key, record in records.items():
        if not isinstance(record, dict) or frozenset(record) != _PANEL_A_RECORD_FIELDS:
            raise ValueError(error)
        value = record["value"]
        scalar = values[key]
        value_missing = _is_missing(value)
        scalar_missing = _is_missing(scalar)
        if (
            value_missing != scalar_missing
            or (
                not value_missing
                and (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    or isinstance(scalar, bool)
                    or not isinstance(scalar, (int, float))
                    or not math.isfinite(float(scalar))
                    or value != scalar
                )
            )
            or not isinstance(record["requested_parameters"], dict)
            or not isinstance(record["effective_parameters"], dict)
            or isinstance(record["advisory_quality_floor_tokens"], bool)
            or not isinstance(record["advisory_quality_floor_tokens"], int)
            or record["advisory_quality_floor_tokens"] <= 0
            or record["advisory_quality_status"]
            not in {"below_advisory_floor", "meets_advisory_floor"}
        ):
            raise ValueError(error)

        expected_method_id = (
            IDX.METHOD_IDS[key]
            if key in _PANEL_A_STANDARD_KEYS
            else IDX.ADAPTIVE_METHOD_IDS[key]
        )
        if record["method_id"] != expected_method_id:
            raise ValueError(error)
        if record["status"] == "available":
            if value_missing or record["missing_reason"] is not None:
                raise ValueError(error)
            if (
                key in _PANEL_A_STANDARD_KEYS
                and record["effective_parameters"]
                != record["requested_parameters"]
            ):
                raise ValueError(error)
        elif record["status"] == "missing":
            if (
                not value_missing
                or record["missing_reason"] not in _PANEL_A_MISSING_REASONS
                or record["effective_parameters"] != {}
            ):
                raise ValueError(error)
        else:
            raise ValueError(error)
    return records


def clean_deep(obj: Any) -> Any:
    """Recursively turn NaN floats into None for JSON-safe payloads."""
    if isinstance(obj, dict):
        return {k: clean_deep(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [clean_deep(v) for v in obj]
    if hasattr(obj, "item"):
        try:
            return clean_deep(obj.item())
        except Exception:
            pass
    if isinstance(obj, float) and math.isnan(obj):
        return None
    return obj


def canonical_export_value(obj: Any) -> Any:
    """Return a JSON/XLSX-safe value under the frozen export precision policy.

    Runtime calculations retain their native precision.  Public serialization
    rounds finite floats to 12 decimal places so immaterial libm/architecture
    noise cannot change a release fixture.  NaN remains a missing value; an
    infinity is rejected because silently exporting it would hide a numerical
    failure.
    """

    obj = clean_deep(obj)
    if isinstance(obj, dict):
        return {key: canonical_export_value(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [canonical_export_value(value) for value in obj]
    if isinstance(obj, tuple):
        return [canonical_export_value(value) for value in obj]
    if isinstance(obj, float):
        if not math.isfinite(obj):
            raise ValueError("Public exports cannot contain infinite values")
        rounded = round(obj, EXPORT_FLOAT_DECIMAL_PLACES)
        return 0.0 if rounded == 0 else rounded
    return obj


def payload_to_json(payload: dict[str, Any]) -> str:
    """Serialize one payload with the frozen public JSON representation."""

    serialized = json.dumps(
        canonical_export_value(payload),
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
    )
    return serialized + ("\n" if JSON_TERMINAL_NEWLINE else "")


def _docs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "documents" in payload:
        return list(payload["documents"])
    return [payload]


def _json_cell(value: Any) -> Any:
    value = canonical_export_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def _doc_name(doc: dict[str, Any]) -> str:
    meta = doc.get("document") or {}
    return meta.get("name") or "document"


def summary_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        settings = doc.get("settings") or {}
        panel_a = doc.get("panel_a") or {}
        panel_b = doc.get("panel_b") or {}
        mean_rank = (panel_b.get("mean_rank") or {}) if panel_b else {}
        p_lex = (panel_b.get("p_lex") or {}) if panel_b else {}
        s_index = (panel_b.get("s_index") or {}) if panel_b else {}
        semantic = doc.get("semantic_network") or {}
        tubelex = doc.get("tubelex") or {}
        row = {
            "document": _doc_name(doc),
            "n_tokens": doc.get("n_tokens"),
            "n_types": doc.get("n_types"),
            "unit": settings.get("unit"),
            "lemmatizer": settings.get("lemmatizer"),
            "lemmatizer_version": settings.get("lemmatizer_version"),
            "frequency_list": settings.get("list_name"),
            "list_lookup_unit": settings.get("list_lookup_unit"),
            "ttr": panel_a.get("ttr"),
            "mattr": panel_a.get("mattr"),
            "mtld": panel_a.get("mtld"),
            "hdd": panel_a.get("hdd"),
            "vocd": panel_a.get("vocd"),
            "advanced_guiraud": panel_b.get("advanced_guiraud") if panel_b else None,
            "pct_beyond_k": panel_b.get("pct_beyond_k") if panel_b else None,
            "pct_off_list": mean_rank.get("pct_off_list"),
            "p_lex_lambda": p_lex.get("lambda"),
            "s_index": s_index.get("S"),
            "s_capped": s_index.get("capped"),
            "oewn_token_coverage": semantic.get("token_coverage"),
            "oewn_type_coverage": semantic.get("type_coverage"),
            "oewn_polysemy_token_mean": semantic.get("polysemy_token_mean"),
            "oewn_polysemy_type_mean": semantic.get("polysemy_type_mean"),
            "oewn_hypernym_depth_token_mean": semantic.get("hypernym_depth_token_mean"),
            "oewn_hypernym_depth_type_mean": semantic.get("hypernym_depth_type_mean"),
            "tubelex_token_coverage": tubelex.get("token_coverage"),
            "tubelex_type_coverage": tubelex.get("type_coverage"),
            "tubelex_frequency_zipf_token_mean": tubelex.get(
                "frequency_zipf_token_mean"
            ),
            "tubelex_frequency_zipf_type_mean": tubelex.get(
                "frequency_zipf_type_mean"
            ),
            "tubelex_video_log10_prevalence_token_mean": tubelex.get(
                "video_log10_prevalence_token_mean"
            ),
            "tubelex_video_log10_prevalence_type_mean": tubelex.get(
                "video_log10_prevalence_type_mean"
            ),
            "tubelex_channel_log10_prevalence_token_mean": tubelex.get(
                "channel_log10_prevalence_token_mean"
            ),
            "tubelex_channel_log10_prevalence_type_mean": tubelex.get(
                "channel_log10_prevalence_type_mean"
            ),
        }
        rows.append(clean_deep(row))
    return rows


def descriptive_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return descriptive statistics for numeric batch-summary measures."""
    rows = summary_rows(payload)
    if not rows:
        return []

    df = pd.DataFrame(rows)
    measures = [
        "n_tokens",
        "n_types",
        "ttr",
        "mattr",
        "mtld",
        "hdd",
        "vocd",
        "advanced_guiraud",
        "pct_beyond_k",
        "pct_off_list",
        "p_lex_lambda",
        "s_index",
        "oewn_token_coverage",
        "oewn_type_coverage",
        "oewn_polysemy_token_mean",
        "oewn_polysemy_type_mean",
        "oewn_hypernym_depth_token_mean",
        "oewn_hypernym_depth_type_mean",
        "tubelex_token_coverage",
        "tubelex_type_coverage",
        "tubelex_frequency_zipf_token_mean",
        "tubelex_frequency_zipf_type_mean",
        "tubelex_video_log10_prevalence_token_mean",
        "tubelex_video_log10_prevalence_type_mean",
        "tubelex_channel_log10_prevalence_token_mean",
        "tubelex_channel_log10_prevalence_type_mean",
    ]
    out = []
    total = len(df)
    for measure in measures:
        if measure not in df.columns:
            continue
        values = pd.to_numeric(df[measure], errors="coerce").dropna()
        row = {
            "measure": measure,
            "n": int(values.count()),
            "missing": int(total - values.count()),
            "mean": values.mean() if not values.empty else None,
            "sd": values.std(ddof=1) if values.count() > 1 else None,
            "min": values.min() if not values.empty else None,
            "q1": values.quantile(0.25) if not values.empty else None,
            "median": values.median() if not values.empty else None,
            "q3": values.quantile(0.75) if not values.empty else None,
            "max": values.max() if not values.empty else None,
        }
        out.append(clean_deep(row))
    return out


def panel_a_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        panel_a = doc.get("panel_a")
        records = _validated_panel_a_records(
            doc.get("panel_a_records"),
            panel_a,
        )
        keys = [*IDX._FUNCS, *[key for key in records if key not in IDX._FUNCS]]
        for key in keys:
            base_key = key.removesuffix("_adaptive")
            record = records[key]
            value = record["value"]
            rows.append({
                "document": _doc_name(doc),
                "index_key": key,
                "index": IDX.PRETTY.get(key, IDX.ADAPTIVE_PRETTY.get(key, key)),
                "value": clean_deep(value),
                "direction": "higher" if IDX.DIRECTION[base_key] > 0 else "lower",
                "status": record["status"],
                "missing_reason": record["missing_reason"],
                "method_id": record["method_id"],
                "requested_parameters": _json_cell(record["requested_parameters"]),
                "effective_parameters": _json_cell(record["effective_parameters"]),
                "advisory_quality_floor_tokens": record[
                    "advisory_quality_floor_tokens"
                ],
                "advisory_quality_status": record["advisory_quality_status"],
            })
    return rows


def lfp_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        panel_b = doc.get("panel_b") or {}
        for row in panel_b.get("lfp") or []:
            rows.append({"document": _doc_name(doc), **clean_deep(row)})
    return rows


def coverage_threshold_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        panel_b = doc.get("panel_b") or {}
        for threshold, band in (panel_b.get("coverage_threshold") or {}).items():
            rows.append({
                "document": _doc_name(doc),
                "threshold_%": threshold,
                "selected_list_band_needed": (
                    f"K{band}" if band else "not reached by selected list"
                ),
            })
    return rows


def p_lex_s_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        panel_b = doc.get("panel_b") or {}
        p_lex = panel_b.get("p_lex") or {}
        s_index = panel_b.get("s_index") or {}
        rows.extend([
            {
                "document": _doc_name(doc),
                "measure": "P_Lex lambda",
                "value": clean_deep(p_lex.get("lambda")),
                "note": "Poisson fit over complete 10-word segments",
            },
            {
                "document": _doc_name(doc),
                "measure": "P_Lex segments",
                "value": clean_deep(p_lex.get("n_segments")),
                "note": "Complete 10-word segments used",
            },
            {
                "document": _doc_name(doc),
                "measure": "S fitted rank",
                "value": clean_deep(s_index.get("S")),
                "note": "Selected-list rank where fitted coverage reaches 100%",
            },
            {
                "document": _doc_name(doc),
                "measure": "S capped",
                "value": clean_deep(s_index.get("capped")),
                "note": s_index.get("note") or s_index.get("reference_list_note"),
            },
        ])
    return rows


def p_lex_distribution_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        p_lex = ((doc.get("panel_b") or {}).get("p_lex") or {})
        observed = p_lex.get("observed_distribution") or {}
        fitted = p_lex.get("fitted_distribution") or {}
        keys = sorted({int(k) for k in observed} | {int(k) for k in fitted})
        for k in keys:
            rows.append({
                "document": _doc_name(doc),
                "hard_words": k,
                "observed_probability": observed.get(k, observed.get(str(k))),
                "fitted_probability": fitted.get(k, fitted.get(str(k))),
            })
    return clean_deep(rows)


def s_empirical_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        s_index = ((doc.get("panel_b") or {}).get("s_index") or {})
        for rank, coverage in (s_index.get("empirical_coverage_pct") or {}).items():
            rows.append({
                "document": _doc_name(doc),
                "rank": rank,
                "empirical_coverage_%": coverage,
            })
    return clean_deep(rows)


def band_wise_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        panel_b = doc.get("panel_b") or {}
        for row in panel_b.get("band_wise") or []:
            rows.append({"document": _doc_name(doc), **clean_deep(row)})
    return rows


def metadata_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for doc in _docs(payload):
        for key in ("ldfreq_version", "output_schema_version"):
            rows.append(
                {
                    "document": _doc_name(doc),
                    "field": key,
                    "value": doc.get(key),
                }
            )
        for key, value in (doc.get("settings") or {}).items():
            rows.append({"document": _doc_name(doc), "field": key, "value": _json_cell(value)})
        for note in doc.get("method_notes") or []:
            rows.append({"document": _doc_name(doc), "field": "method_note", "value": note})
    return rows


def semantic_network_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one aggregate Open English WordNet row per document."""
    rows = []
    for doc in _docs(payload):
        semantic = doc.get("semantic_network") or {}
        if semantic:
            rows.append({"document": _doc_name(doc), **clean_deep(semantic)})
    return rows


def tubelex_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return one aggregate TUBELEX-EN row per document."""
    rows = []
    for doc in _docs(payload):
        tubelex = doc.get("tubelex") or {}
        if tubelex:
            rows.append({"document": _doc_name(doc), **clean_deep(tubelex)})
    return rows


def _canonicalize_xlsx_archive(payload: bytes) -> bytes:
    """Normalize ZIP metadata and member order in a generated XLSX archive."""

    members: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(BytesIO(payload), "r") as source:
            for member in source.infolist():
                if member.filename in members:
                    raise ValueError("Generated XLSX contains a duplicate ZIP member")
                members[member.filename] = source.read(member)
    except zipfile.BadZipFile as exc:  # pragma: no cover - defensive writer boundary
        raise ValueError("Generated XLSX is not a valid ZIP archive") from exc

    core_name = "docProps/core.xml"
    if core_name not in members:
        raise ValueError("Generated XLSX has no core properties")
    core = members[core_name]
    for pattern in _XLSX_CORE_TIME_PATTERNS:
        core, replacements = pattern.subn(
            rb"\g<1>" + XLSX_CORE_TIMESTAMP + rb"\g<2>",
            core,
        )
        if replacements != 1:
            raise ValueError("Generated XLSX core timestamp is ambiguous")
    members[core_name] = core

    output = BytesIO()
    with zipfile.ZipFile(
        output,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=XLSX_ZIP_COMPRESSION_LEVEL,
        strict_timestamps=True,
    ) as destination:
        for name in sorted(members):
            member = zipfile.ZipInfo(name, date_time=XLSX_ZIP_TIMESTAMP)
            member.compress_type = zipfile.ZIP_DEFLATED
            member.create_system = 0
            member.create_version = 20
            member.extract_version = 20
            member.external_attr = 0
            destination.writestr(
                member,
                members[name],
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=XLSX_ZIP_COMPRESSION_LEVEL,
            )
    return output.getvalue()


def payload_to_excel(payload: dict[str, Any]) -> bytes:
    """Build an XLSX workbook from a single- or multi-document payload."""
    payload = canonical_export_value(payload)
    diagnostics = payload.get("batch_diagnostics") or {}
    sheets = {
        "summary": summary_rows(payload),
        "descriptives": descriptive_rows(payload),
        "panel_a": panel_a_rows(payload),
        "lfp": lfp_rows(payload),
        "thresholds": coverage_threshold_rows(payload),
        "p_lex_s": p_lex_s_rows(payload),
        "p_lex_dist": p_lex_distribution_rows(payload),
        "s_empirical": s_empirical_rows(payload),
        "band_wise": band_wise_rows(payload),
        "semantic_network": semantic_network_rows(payload),
        "tubelex": tubelex_rows(payload),
        "metadata": metadata_rows(payload),
    }
    if diagnostics:
        sheets.update({
            "batch_bands": diagnostics.get("bands") or [],
            "reliability": [
                {key: _json_cell(value) for key, value in row.items()}
                for row in diagnostics.get("reliability") or []
            ],
            "off_list": diagnostics.get("off_list") or [],
            "overlap_matrix": diagnostics.get("overlap_matrix") or [],
            "overlap_pairs": diagnostics.get("overlap_pairs") or [],
        })
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, rows in sheets.items():
            pd.DataFrame(rows).to_excel(writer, sheet_name=name, index=False)
        properties = writer.book.properties
        properties.creator = "ldfreq"
        properties.lastModifiedBy = "ldfreq"
        properties.created = XLSX_DOCUMENT_DATETIME
        properties.modified = XLSX_DOCUMENT_DATETIME
    return _canonicalize_xlsx_archive(output.getvalue())
