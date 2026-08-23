import csv
from pathlib import Path

import pytest

from ldfreq import frequency as FRQ


class FixtureNormalizer:
    def __init__(self, forms):
        self.forms = forms

    def normalize(self, token):
        return self.forms.get(token, token)


def _category_total(diagnostics):
    return sum(
        diagnostics[key]
        for key in (
            "surface_hit_tokens",
            "normalized_fallback_hit_tokens",
            "normalized_off_list_tokens",
            "identity_fallback_tokens",
        )
    )


def test_surface_hit_precedes_normalization_and_fallback_is_reported():
    normalizer = FixtureNormalizer({"went": "go", "going": "go"})
    result = FRQ.panel_b(
        ["went", "going"],
        {"went": 40, "go": 5},
        normalizer,
        n_levels=1,
    )

    assert result["_mapped"] == [("went", 40), ("go", 5)]
    diagnostics = result["mapping_diagnostics"]
    assert diagnostics["method_id"] == (
        "surface_first_rank_lookup_normalized_fallback_v1"
    )
    assert diagnostics["surface_hit_tokens"] == 1
    assert diagnostics["normalized_fallback_hit_tokens"] == 1
    assert diagnostics["surface_hit_rate"] == pytest.approx(0.5)
    assert diagnostics["normalized_fallback_hit_rate"] == pytest.approx(0.5)


def test_family_heads_collapse_distinct_surface_types():
    family = {
        "accept": {"head": "accept", "rank": 1},
        "accepted": {"head": "accept", "rank": 1},
        "acceptance": {"head": "accept", "rank": 1},
    }
    result = FRQ.panel_b(
        ["accept", "accepted", "acceptance", "accepted"],
        family,
        FixtureNormalizer({}),
        n_levels=1,
    )

    assert {head for head, _rank in result["_mapped"]} == {"accept"}
    diagnostics = result["mapping_diagnostics"]
    assert diagnostics["input_surface_types"] == 3
    assert diagnostics["mapped_unit_types"] == 1
    assert diagnostics["collapsed_surface_types"] == 2


def test_off_list_normalized_and_identity_paths_are_disjoint():
    result = FRQ.panel_b(
        ["Invented", "plain", "invented"],
        {},
        FixtureNormalizer({"invented": "invent"}),
        n_levels=1,
    )

    assert result["_mapped"] == [
        ("invent", None),
        ("plain", None),
        ("invent", None),
    ]
    diagnostics = result["mapping_diagnostics"]
    assert diagnostics["normalized_off_list_tokens"] == 2
    assert diagnostics["identity_fallback_tokens"] == 1
    assert diagnostics["normalized_off_list_rate"] == pytest.approx(2 / 3)
    assert diagnostics["identity_fallback_rate"] == pytest.approx(1 / 3)
    assert _category_total(diagnostics) == diagnostics["input_tokens"]


def test_empty_input_has_zero_counts_rates_and_no_term_fields():
    diagnostics = FRQ.panel_b(
        [], {}, FixtureNormalizer({}), n_levels=1
    )["mapping_diagnostics"]

    assert diagnostics["input_tokens"] == 0
    assert diagnostics["input_surface_types"] == 0
    assert diagnostics["mapped_unit_types"] == 0
    assert diagnostics["collapsed_surface_types"] == 0
    assert _category_total(diagnostics) == 0
    for key, value in diagnostics.items():
        if key.endswith("_rate"):
            assert value == 0.0
    assert all(not isinstance(value, (list, tuple, set, dict)) for value in diagnostics.values())


def test_mapping_diagnostics_do_not_disclose_submitted_terms():
    secret_terms = ["privatealpha", "privatebeta"]
    diagnostics = FRQ.panel_b(
        secret_terms,
        {},
        FixtureNormalizer({"privatealpha": "private", "privatebeta": "private"}),
        n_levels=1,
    )["mapping_diagnostics"]

    rendered = repr(diagnostics)
    assert all(term not in rendered for term in secret_terms)
    assert _category_total(diagnostics) == diagnostics["input_tokens"]
    assert sum(
        diagnostics[key]
        for key in (
            "surface_hit_rate",
            "normalized_fallback_hit_rate",
            "normalized_off_list_rate",
            "identity_fallback_rate",
        )
    ) == pytest.approx(1.0)


def test_plain_list_loaders_publish_truthful_lookup_units(tmp_path: Path):
    ranked_path = tmp_path / "ranked.csv"
    with ranked_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rank", "word"])
        writer.writerow([1, "go (went)"])
    _ranked, ranked_meta = FRQ.load_ranked_list(str(ranked_path))

    ngsl_dir = tmp_path / "ngsl"
    ngsl_dir.mkdir()
    with (ngsl_dir / "NGSL_1.2_stats.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.writer(handle)
        writer.writerow(["Lemma", "SFI Rank"])
        writer.writerow(["go", "1"])
    _ngsl, ngsl_meta = FRQ.load_ngsl(str(ngsl_dir))

    headword_dir = tmp_path / "headwords"
    headword_dir.mkdir()
    (headword_dir / "headwords 1st 1000.txt").write_text("go\n", encoding="utf-8")
    _headwords, headword_meta = FRQ.load_headword_bands(str(headword_dir))

    assert ranked_meta["lookup_unit"] == "listed_surface_form_or_normalized_fallback"
    assert ngsl_meta["lookup_unit"] == (
        "listed_surface_form_at_lemma_rank_or_normalized_fallback"
    )
    assert headword_meta["lookup_unit"] == "listed_headword_or_normalized_fallback"
