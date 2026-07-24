"""Machine-readable interpretation guidance for aggregate lexical results.

The functions in this module accept aggregate counts and scores only.  They do
not inspect, retain, or reconstruct submitted text.  The same output is used by
the Web UI, JSON payload, and Excel export so cautions cannot silently diverge.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

from . import indices as IDX


INTERPRETATION_SCHEMA_VERSION = "1.0.0"
TUBELEX_SHORT_TOKEN_NOTICE = 50
TUBELEX_SHORT_TYPE_NOTICE = 20
COVERAGE_NOTICE_THRESHOLD = 0.90
PANEL_B_OFF_LIST_NOTICE_PERCENT = 10.0

GENERAL_CAUTIONS = (
    "These outputs are descriptive properties of this text under the recorded "
    "tokenizer, resources, and settings; no single value is a proficiency, quality, "
    "grade, or diagnostic score.",
    "Compare texts only when task, genre, preprocessing, resource versions, lookup "
    "units, and index parameters are sufficiently comparable.",
    "Interpret differences together with text length, prompt/topic, repetition, "
    "coverage, and sampling uncertainty; higher or lower is not automatically better.",
    "Automatic cautions are transparent display heuristics, not validated cut scores. "
    "No triggered caution does not establish reliability or validity.",
)


_PANEL_A_CARD = {
    "id": "panel_a",
    "title": "Panel A — lexical diversity",
    "construct": (
        "Variation and repetition among lower-cased surface tokens in this text, "
        "without a reference frequency list."
    ),
    "direction": (
        "Direction is index-specific. Most indices increase with diversity; Maas and "
        "Yule's K decrease. Direction does not imply writing quality."
    ),
    "weighting": "Each index applies its own token-sequence and sample-size formula.",
    "report_with": [
        "token and type counts",
        "index-specific recommended minimum",
        "tokenizer and all index parameters",
    ],
    "limitations": [
        "TTR-family values remain sensitive to text length and sampling.",
        "Repetition may be rhetorically appropriate and is not automatically a deficit.",
        "Diversity is not the same construct as lexical sophistication or accuracy.",
    ],
}

_PANEL_B_CARD = {
    "id": "panel_b",
    "title": "Panel B — selected-list frequency profile",
    "construct": (
        "How submitted tokens map to ranks, bands, or families in the selected lexical "
        "list under the recorded normalizer."
    ),
    "direction": (
        "More low-frequency or off-list material means less match to that list, not "
        "automatically greater sophistication or poorer comprehensibility."
    ),
    "weighting": "Coverage is token weighted; family/head mapping depends on the resource.",
    "report_with": [
        "list name and version",
        "lookup unit and normalizer version",
        "off-list rate and selected-list coverage",
    ],
    "limitations": [
        "Off-list does not mean unknown to a reader.",
        "Names, spelling variants, acronyms, compounds, and topic words can affect results.",
        "Values from different lists or family policies are not interchangeable.",
    ],
}

_TUBELEX_CARD = {
    "id": "tubelex",
    "title": "TUBELEX-EN Treebank — everyday-exposure reference",
    "construct": (
        "Smoothed word-form frequency and video/channel prevalence in the published "
        "TUBELEX English YouTube-derived aggregate."
    ),
    "direction": (
        "Higher Zipf means more frequent in TUBELEX. Log prevalence closer to zero "
        "means occurrence across a larger share of videos or channels. Neither direction "
        "is inherently better."
    ),
    "weighting": (
        "Token means count repetitions; type means give each distinct lookup unit one "
        "vote. Unseen units remain in both means at documented smoothing floors."
    ),
    "report_with": [
        "token- and type-weighted means",
        "token and type coverage",
        "method ID, corpus totals, tokenizer, and resource version",
    ],
    "limitations": [
        "This is mixed global YouTube-derived exposure, not balanced American English.",
        "It is not COCA, spontaneous-conversation frequency, or a TAALES COCA replica.",
        "Low values can reflect names, spelling, tokenization, or topic as well as rarity.",
        "Video and channel counts are contextual-prevalence units, not learner norms.",
    ],
}

_SEMANTIC_CARD = {
    "id": "semantic_network",
    "title": "Open English WordNet — semantic-network baseline",
    "construct": (
        "Dictionary sense inventory size and longest hypernym-path depth for normalized "
        "lemmas found in Open English WordNet."
    ),
    "direction": (
        "Higher polysemy means more listed senses; greater depth means a longer recorded "
        "noun/verb hierarchy path. Neither establishes contextual sophistication."
    ),
    "weighting": (
        "Token means weight repeated covered lemmas; type means weight distinct covered "
        "lemmas once. Uncovered lemmas are omitted from these means, not assigned zero."
    ),
    "report_with": [
        "token and type coverage",
        "token- and type-weighted means",
        "normalizer and Open English WordNet version",
    ],
    "limitations": [
        "The current baseline is POS-agnostic and not word-sense disambiguated.",
        "Listed senses are not evidence that a writer activated those senses in context.",
        "Coverage changes the population over which semantic means are calculated.",
    ],
}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _coverage_flag(scope: str, values: Mapping[str, Any]) -> dict[str, Any] | None:
    token_coverage = _finite_number(values.get("token_coverage"))
    type_coverage = _finite_number(values.get("type_coverage"))
    observed = [value for value in (token_coverage, type_coverage) if value is not None]
    if not observed or min(observed) >= COVERAGE_NOTICE_THRESHOLD:
        return None
    label = "TUBELEX" if scope == "tubelex" else "Open English WordNet"
    consequence = (
        "Unseen units remain in the smoothed means."
        if scope == "tubelex"
        else "Uncovered lemmas are omitted from the semantic means."
    )
    return {
        "scope": scope,
        "code": f"{scope}_coverage_below_90pct",
        "severity": "caution",
        "message": (
            f"At least one {label} coverage value is below 90%. {consequence} "
            "Report coverage and do not attribute the score difference solely to lexical "
            "choice. The 90% alert is a display heuristic, not a validity cutoff."
        ),
        "observed": {
            "token_coverage": token_coverage,
            "type_coverage": type_coverage,
        },
        "threshold": {"display_notice_below": COVERAGE_NOTICE_THRESHOLD},
    }


def _panel_a_short_flag(n_tokens: int, settings: Mapping[str, Any]) -> dict[str, Any] | None:
    thresholds: dict[str, int] = {}
    for key in IDX._FUNCS:
        minimum = IDX.effective_min_tokens(
            key,
            segment=int(settings.get("msttr_segment", 50)),
            window=int(settings.get("mattr_window", 50)),
            hdd_sample=int(settings.get("hdd_sample", 42)),
            min_tokens_override=settings.get("min_tokens"),
        )
        if n_tokens < minimum:
            thresholds[key] = minimum
    if not thresholds:
        return None
    return {
        "scope": "panel_a",
        "code": "panel_a_below_recommended_minimum",
        "severity": "caution",
        "message": (
            "This text is below the displayed recommended minimum for one or more "
            "Panel A indices. Retained values are descriptive but may be unstable; use "
            "the per-index Warning column."
        ),
        "observed": {"n_tokens": int(n_tokens)},
        "threshold": {"minimum_tokens_by_index": thresholds},
    }


def build_document_interpretation(
    *,
    n_tokens: int,
    n_types: int,
    settings: Mapping[str, Any] | None,
    panel_b: Mapping[str, Any] | None,
    semantic_network: Mapping[str, Any] | None,
    tubelex: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return deterministic cards and flags from aggregate document results."""

    settings = settings or {}
    cards = [dict(_PANEL_A_CARD)]
    flags: list[dict[str, Any]] = []

    panel_a_flag = _panel_a_short_flag(int(n_tokens), settings)
    if panel_a_flag is not None:
        flags.append(panel_a_flag)

    if panel_b is not None:
        cards.append(dict(_PANEL_B_CARD))
        mean_rank = panel_b.get("mean_rank")
        off_list = (
            _finite_number(mean_rank.get("pct_off_list"))
            if isinstance(mean_rank, Mapping)
            else None
        )
        if off_list is not None and off_list > PANEL_B_OFF_LIST_NOTICE_PERCENT:
            flags.append(
                {
                    "scope": "panel_b",
                    "code": "panel_b_off_list_over_10pct",
                    "severity": "caution",
                    "message": (
                        "More than 10% of tokens are off-list for the selected resource. "
                        "This can reflect list fit, names, spelling, compounds, or topic; "
                        "it is not an estimate of unknown vocabulary. The 10% alert is a "
                        "display heuristic, not a validity cutoff."
                    ),
                    "observed": {"pct_off_list": off_list},
                    "threshold": {
                        "display_notice_above_percent": PANEL_B_OFF_LIST_NOTICE_PERCENT
                    },
                }
            )

    if tubelex is not None:
        cards.append(dict(_TUBELEX_CARD))
        tubelex_tokens = int(tubelex.get("tokens", 0))
        tubelex_types = int(tubelex.get("types", 0))
        if (
            tubelex_tokens < TUBELEX_SHORT_TOKEN_NOTICE
            or tubelex_types < TUBELEX_SHORT_TYPE_NOTICE
        ):
            flags.append(
                {
                    "scope": "tubelex",
                    "code": "tubelex_short_profile",
                    "severity": "caution",
                    "message": (
                        "The TUBELEX profile has fewer than 50 lookup tokens or 20 lookup "
                        "types. Values are retained for reproducibility but are especially "
                        "sensitive to individual word choices. These thresholds are display "
                        "heuristics, not validated reliability boundaries."
                    ),
                    "observed": {"tokens": tubelex_tokens, "types": tubelex_types},
                    "threshold": {
                        "minimum_tokens": TUBELEX_SHORT_TOKEN_NOTICE,
                        "minimum_types": TUBELEX_SHORT_TYPE_NOTICE,
                    },
                }
            )
        coverage_flag = _coverage_flag("tubelex", tubelex)
        if coverage_flag is not None:
            flags.append(coverage_flag)

    if semantic_network is not None:
        cards.append(dict(_SEMANTIC_CARD))
        coverage_flag = _coverage_flag("semantic_network", semantic_network)
        if coverage_flag is not None:
            flags.append(coverage_flag)

    return {
        "schema_version": INTERPRETATION_SCHEMA_VERSION,
        "purpose": "Interpretive guidance for aggregate descriptive indices",
        "general_cautions": list(GENERAL_CAUTIONS),
        "automatic_flags": flags,
        "method_cards": cards,
        "document_context": {
            "n_tokens": int(n_tokens),
            "n_types": int(n_types),
        },
    }
