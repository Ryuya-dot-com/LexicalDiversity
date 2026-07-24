import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLAN_PATH = PROJECT_ROOT / "benchmarks" / "ellipse" / "analysis-plan.json"
MANIFEST_PATH = PROJECT_ROOT / "benchmarks" / "ellipse" / "manifest.json"


def _documents():
    return (
        json.loads(PLAN_PATH.read_text(encoding="utf-8")),
        json.loads(MANIFEST_PATH.read_text(encoding="utf-8")),
    )


def test_plan_is_cross_pinned_to_the_verified_resource():
    plan, manifest = _documents()
    data = plan["data"]

    assert data["resource_id"] == manifest["resource_id"]
    assert data["verification_manifest"] == "benchmarks/ellipse/manifest.json"
    assert data["upstream_commit"] == manifest["upstream"]["commit"]
    assert {
        (item["bytes"], item["sha256"])
        for item in data["accepted_outer_archives"]
    } == {
        (item["bytes"], item["sha256"])
        for item in manifest["outer_archive"]["accepted_variants"]
    }
    assert data["expected_primary_records"] == 6482
    assert data["expected_unique_essay_ids"] == 6482
    assert data["expected_prompt_labels"] == 44


def test_confirmatory_metric_and_model_scope_is_small_and_fixed():
    plan, _manifest = _documents()

    assert [item["id"] for item in plan["metrics"]["controls"]] == [
        "n_tokens",
        "mtld",
        "ngsl_pct_beyond_k2",
    ]
    assert [item["id"] for item in plan["metrics"]["focal"]] == [
        "tubelex_frequency_zipf_type_mean",
        "tubelex_channel_log10_prevalence_type_mean",
    ]
    assert {
        item["expected_direction_with_vocabulary"]
        for item in plan["metrics"]["focal"]
    } == {"negative"}
    assert plan["models"]["B1"]["extends"] == "B0"
    assert plan["metrics"]["selection_rules"][
        "outcome_associations_may_influence_selection"
    ] is False
    assert plan["metrics"]["selection_rules"]["substitution_after_qc_failure"] is False


def test_unknown_prompt_endpoint_and_inference_are_frozen():
    plan, _manifest = _documents()
    validation = plan["validation"]
    interval = plan["inference"]["primary_performance_interval"]

    assert plan["outcomes"]["primary"]["column"] == "Vocabulary"
    assert validation["scheme"] == "leave_one_prompt_out"
    assert validation["expected_folds"] == 44
    assert validation["feature_scaling_fit_on_training_fold_only"] is True
    assert validation["spline_knots_fit_on_training_fold_only"] is True
    assert validation["primary_endpoint"]["id"] == "delta_macro_mae"
    assert interval == {
        "method": "paired_nonparametric_bootstrap",
        "resampling_unit": "prompt",
        "resamples": 10000,
        "seed": 20260723,
    }


def test_plan_keeps_human_text_out_of_network_product_and_release_paths():
    plan, _manifest = _documents()
    governance = plan["governance"]
    serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True)

    assert plan["purpose"]["release_gate"] is False
    assert governance["external_api_calls_with_human_essays"] is False
    assert governance["network_and_telemetry_during_corpus_processing"] == "disabled"
    assert governance["corpus_bundled_with_tool_or_release"] is False
    assert governance["individual_feature_rows_published"] is False
    assert governance["individual_predictions_published"] is False
    assert governance["fitted_model_published_or_deployed"] is False
    assert "/Users/" not in serialized
    assert "LexicalSophistication/" not in serialized
    assert "ellipse_test" not in serialized
    assert "ellipse_raw_data" not in serialized
    assert "results" not in plan
