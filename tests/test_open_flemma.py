import csv
from pathlib import Path

from ldfreq.open_flemma import (
    ALGORITHM_VERSION,
    OpenFlemmaLemmatizer,
    build_open_flemma,
    flemma_lexicon_from_rows,
    inflection_candidates,
    load_ngsl_flemma_lexicon,
)
from ldfreq.lemmatizers import build
from ldfreq.semantic_network import load_verified_semantic_network_index


NGSL_DIR = Path("data/ngsl")
NGSL_FORMS = NGSL_DIR / "NGSL_1.2_lemmatized_for_research.csv"


def test_ngsl_loader_retains_collisions_instead_of_using_file_order():
    lexicon = load_ngsl_flemma_lexicon(NGSL_DIR)

    assert lexicon.form_to_heads["abandoned"] == ("abandon",)
    assert lexicon.form_to_heads["found"] == ("find", "found")
    assert {"found", "left", "mine", "rose", "wound"} <= lexicon.ambiguous_forms
    assert lexicon.source_sha256 == (
        "d814f2a0a3c61479a2c5ad037661719a0cc6e7dbcde31f181b54f12d0f1e11a4"
    )


def test_unique_ngsl_forms_and_common_irregulars_map_to_heads():
    normalizer = build_open_flemma(NGSL_DIR)

    assert normalizer.normalize("Abandoned") == "abandon"
    assert normalizer.normalize("went") == "go"
    assert normalizer.normalize("better") == "good"
    assert normalizer.normalize("children") == "child"
    assert normalizer.normalize("an") == "a"


def test_posless_homographs_keep_the_surface_spelling():
    normalizer = build_open_flemma(NGSL_DIR)

    for form in ("found", "left", "leaves", "mine", "rose", "wound", "saw", "lay"):
        assert normalizer.normalize(form) == form


def test_rules_require_one_known_head_and_cover_regular_spelling_changes():
    lexicon = flemma_lexicon_from_rows(
        [
            ("study",),
            ("stop",),
            ("make",),
            ("box",),
            ("nice",),
            ("big",),
            ("teacher",),
        ],
        source_label="test heads",
    )
    normalizer = OpenFlemmaLemmatizer(lexicon)

    assert normalizer.normalize("studies") == "study"
    assert normalizer.normalize("stopped") == "stop"
    assert normalizer.normalize("making") == "make"
    assert normalizer.normalize("boxes") == "box"
    assert normalizer.normalize("nicer") == "nice"
    assert normalizer.normalize("biggest") == "big"
    assert normalizer.normalize("teacher") == "teacher"
    assert normalizer.normalize("unlicenseds") == "unlicenseds"


def test_multiple_known_rule_candidates_are_not_guessed():
    lexicon = flemma_lexicon_from_rows(
        [("leaf",), ("leave",), ("ax",), ("axe",), ("axis",)],
        source_label="ambiguous test heads",
    )
    normalizer = OpenFlemmaLemmatizer(lexicon)

    assert normalizer.normalize("leaves") == "leaves"
    assert normalizer.normalize("axes") == "axes"


def test_open_semantic_heads_validate_rules_and_reveal_ngsl_ambiguity():
    # ``extra_heads`` is the integration point for the verified OEWN lemma set.
    # The synthetic terms keep this unit test independent of the large artifact.
    normalizer = build_open_flemma(
        NGSL_DIR,
        extra_heads={"scrutinize", "leaf"},
    )

    assert normalizer.normalize("scrutinized") == "scrutinize"
    # NGSL maps LEAVES only to LEAVE.  OEWN's additional LEAF head makes a
    # context-free decision unsafe, so the surface spelling is retained.
    assert normalizer.normalize("leaves") == "leaves"


def test_apostrophe_policy_distinguishes_possessives_from_contractions():
    lexicon = flemma_lexicon_from_rows(
        [("teacher",), ("it",)],
        source_label="apostrophe test heads",
    )
    normalizer = OpenFlemmaLemmatizer(lexicon)

    assert normalizer.normalize("teacher's") == "teacher"
    assert normalizer.normalize("teachers'") == "teacher"
    assert normalizer.normalize("it's") == "it's"


def test_behavior_version_is_stable_and_changes_with_head_vocabulary():
    one = build_open_flemma(NGSL_DIR)
    two = build_open_flemma(NGSL_DIR)
    extended = build_open_flemma(NGSL_DIR, extra_heads={"quizzacious"})

    assert one.version == two.version
    assert one.version.startswith(ALGORITHM_VERSION + "+")
    assert extended.version != one.version


def test_normalization_is_idempotent_for_ngsl_forms():
    normalizer = build_open_flemma(NGSL_DIR)

    for form in ("went", "abandoned", "better", "found", "unknownword"):
        normalized = normalizer.normalize(form)
        assert normalizer.normalize(normalized) == normalized


def test_all_unique_ngsl_rows_reproduce_their_declared_head():
    """Regression audit over the open source table, not a hand-picked sample."""

    lexicon = load_ngsl_flemma_lexicon(NGSL_FORMS)
    normalizer = OpenFlemmaLemmatizer(lexicon)
    # These are deliberate, reviewable departures from unique NGSL rows.
    # Four preserve lexical ambiguity absent from that table; ``won`` corrects
    # its placement under WILL rather than WIN, and ``criteria`` uses its
    # transparent irregular singular rather than treating the plural as a head.
    intentional_overrides = {
        "criteria": "criterion",
        "fell": "fell",
        "leaves": "leaves",
        "saw": "saw",
        "spoke": "spoke",
        "won": "win",
    }
    with NGSL_FORMS.open(encoding="utf-8-sig", newline="") as source:
        rows = [row for row in csv.reader(source) if row and not row[0].startswith("##")]

    for row in rows:
        head = row[0].strip().lower()
        for raw_form in row:
            form = raw_form.strip().lower()
            if lexicon.form_to_heads[form] == (head,):
                assert normalizer.normalize(form) == intentional_overrides.get(form, head)


def test_candidate_generation_never_performs_derivational_stemming():
    assert "happy" not in inflection_candidates("happiness")
    assert "quick" not in inflection_candidates("quickly")
    assert "educate" not in inflection_candidates("education")


def test_public_builder_uses_pinned_ngsl_and_oewn_candidate_vocabulary():
    oewn = load_verified_semantic_network_index()
    normalizer = build(None, extra_heads=oewn.lemmas)

    assert normalizer.name == "open_flemma"
    assert normalizer.version.startswith(ALGORITHM_VERSION + "+")
    assert normalizer.normalize("went") == "go"
    # An OEWN lexical entry for an inflected spelling does not cancel a unique
    # curated NGSL flemma mapping.
    assert normalizer.normalize("abandoned") == "abandon"
    # QUIZ is in OEWN but outside the NGSL head vocabulary; this exercises the
    # open semantic vocabulary as validation for the project's own rule.
    assert normalizer.normalize("quizzed") == "quiz"
