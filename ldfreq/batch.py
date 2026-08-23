"""Batch diagnostics for multi-document analyses."""
from __future__ import annotations

from collections import Counter, defaultdict
from itertools import combinations
from typing import Any

from . import indices as IDX
from .exporting import _validated_panel_a_records, clean_deep


DERIVATIONAL_SUFFIXES = (
    "ization",
    "isation",
    "ational",
    "fulness",
    "iveness",
    "ability",
    "ibility",
    "ments",
    "ness",
    "ment",
    "tion",
    "sion",
    "able",
    "ible",
    "ally",
    "ing",
    "ed",
    "er",
    "est",
    "ly",
    "ity",
    "ize",
    "ise",
    "ive",
    "ous",
    "less",
    "ful",
    "al",
)


def band_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        panel_b = result.get("panel_b") or {}
        for row in panel_b.get("lfp") or []:
            rows.append({"document": result["name"], **clean_deep(row)})
    return rows


def reliability_rows(
    results: list[dict[str, Any]],
    *,
    segment: int | None = None,
    window: int | None = None,
    hdd_sample: int | None = None,
) -> list[dict[str, Any]]:
    """Return reliability rows using each record's requested method parameters.

    The optional legacy arguments are assertions only.  They can confirm that a
    caller's batch configuration matches the records, but they never override
    the parameters recorded by the computation that produced a value or a
    missing result.
    """
    rows = []
    for result in results:
        n_tokens = result.get("n_tokens")
        if n_tokens is None:
            n_tokens = len(result.get("a_tokens") or [])
        indices = result.get("indices")
        index_records = _validated_panel_a_records(
            result.get("index_records"),
            indices,
        )
        for key in IDX._FUNCS:
            record = index_records[key]
            value = record["value"]
            requested = record["requested_parameters"]
            segment_length = requested.get("segment_length", 50)
            window_length = requested.get("window_length", 50)
            sample_size = requested.get("sample_size", 42)
            vocd_hi = requested.get("sample_size_max", 50)
            mtld_min_factor_len = requested.get("minimum_factor_length", 10)

            assertions = {
                "msttr": ("segment", segment, segment_length),
                "mattr": ("window", window, window_length),
                "hdd": ("hdd_sample", hdd_sample, sample_size),
            }
            if key in assertions:
                argument_name, asserted, recorded = assertions[key]
                if asserted is not None and asserted != recorded:
                    raise ValueError(
                        f"{argument_name} contradicts the structured Panel A record"
                    )
            required = IDX.computational_min_tokens(
                key,
                segment=segment_length,
                window=window_length,
                hdd_sample=sample_size,
                vocd_hi=vocd_hi,
                mtld_min_factor_len=mtld_min_factor_len,
            )
            quality_floor = record["advisory_quality_floor_tokens"]
            quality_status = record["advisory_quality_status"]
            missing_reason = record["missing_reason"]
            if value is None or (isinstance(value, float) and value != value):
                if missing_reason in {
                    "empty_input",
                    "insufficient_tokens_for_formula",
                    "too_short_for_requested_parameter",
                }:
                    status, code = "too short", 0
                    note = f"N={n_tokens} < computational minimum {required}"
                else:
                    status, code = "undefined", 1
                    note = missing_reason.replace("_", " ")
            else:
                status, code = "available", 2
                note = (
                    f"N={n_tokens} < advisory quality floor {quality_floor}"
                    if quality_status == "below_advisory_floor"
                    else ""
                )
            rows.append({
                "document": result["name"],
                "index_key": key,
                "index": IDX.PRETTY[key],
                "status": status,
                "status_code": code,
                "n_tokens": n_tokens,
                "required_tokens": required,
                "value": clean_deep(value),
                "note": note,
                "missing_reason": missing_reason,
                "method_id": record["method_id"],
                "requested_parameters": clean_deep(record["requested_parameters"]),
                "effective_parameters": clean_deep(record["effective_parameters"]),
                "advisory_quality_floor_tokens": quality_floor,
                "advisory_quality_status": quality_status,
            })
    return rows


def _alpha_only(value: str) -> str:
    return "".join(ch for ch in value if ch.isalpha())


def _is_uppercase_form(value: str) -> bool:
    letters = _alpha_only(value)
    return len(letters) > 1 and letters.isupper()


def _is_capitalized_form(value: str) -> bool:
    letters = _alpha_only(value)
    return len(letters) > 1 and letters[0].isupper() and not letters.isupper()


def _suffix_evidence(head: str, forms: list[str]) -> str | None:
    candidates = [head.lower(), *[form.lower() for form in forms]]
    for value in candidates:
        if len(value) < 5:
            continue
        for suffix in DERIVATIONAL_SUFFIXES:
            if value.endswith(suffix):
                return suffix
    return None


def _classify_offlist(head: str, forms: Counter[str]) -> tuple[str, str]:
    """Classify off-list causes from token surface clues.

    The labels are heuristic diagnostics for review, not linguistic annotation.
    """
    ordered_forms = [form for form, _ in forms.most_common()]

    for form in ordered_forms:
        if _is_uppercase_form(form):
            return "acronym or initialism", f"uppercase form: {form}"

    for form in ordered_forms:
        if "'" in form:
            return "contraction or possessive", f"apostrophe form: {form}"

    for form in ordered_forms:
        if _is_capitalized_form(form):
            return "proper noun or sentence-initial capitalization", (
                f"capitalized form: {form}"
            )

    suffix = _suffix_evidence(head, ordered_forms)
    if suffix:
        return "derived or inflected form", f"suffix: -{suffix}"

    if len(head) <= 2:
        return "short token or abbreviation", f"short head: {head}"

    return "specialized term, name, or possible spelling issue", "no surface cue"


def offlist_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for result in results:
        mapped = ((result.get("panel_b") or {}).get("_mapped") or [])
        raw_tokens = result.get("raw_tokens") or []
        raw_surfaces = result.get("raw_surfaces") or raw_tokens
        for surface, pair in zip(raw_surfaces, mapped):
            head, rank = pair
            if rank is None:
                grouped[(result["name"], head)][surface] += 1

    rows = []
    for (document, head), forms in grouped.items():
        total = sum(forms.values())
        form_summary = ", ".join(
            f"{form} ({count})" for form, count in forms.most_common(8)
        )
        cause, evidence = _classify_offlist(head, forms)
        rows.append({
            "document": document,
            "head": head,
            "count": total,
            "forms": form_summary,
            "n_forms": len(forms),
            "cause": cause,
            "evidence": evidence,
        })
    return sorted(rows, key=lambda row: (-row["count"], row["document"], row["head"]))


def overlap_matrix_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    type_sets = {result["name"]: set(result.get("a_tokens") or []) for result in results}
    rows = []
    for left_name, left_set in type_sets.items():
        for right_name, right_set in type_sets.items():
            union = left_set | right_set
            shared = left_set & right_set
            rows.append({
                "document_a": left_name,
                "document_b": right_name,
                "shared_types": len(shared),
                "union_types": len(union),
                "jaccard": (len(shared) / len(union)) if union else None,
            })
    return clean_deep(rows)


def overlap_pair_rows(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    type_sets = {result["name"]: set(result.get("a_tokens") or []) for result in results}
    rows = []
    for left_name, right_name in combinations(type_sets, 2):
        left_set = type_sets[left_name]
        right_set = type_sets[right_name]
        union = left_set | right_set
        shared = left_set & right_set
        rows.append({
            "document_a": left_name,
            "document_b": right_name,
            "shared_types": len(shared),
            "a_only_types": len(left_set - right_set),
            "b_only_types": len(right_set - left_set),
            "union_types": len(union),
            "jaccard": (len(shared) / len(union)) if union else None,
        })
    return clean_deep(rows)
