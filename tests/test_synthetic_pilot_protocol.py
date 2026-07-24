import json
import math
import re
from datetime import date
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = (
    PROJECT_ROOT / "benchmarks" / "synthetic" / "pilot-protocol.json"
)
DOC_PATH = PROJECT_ROOT / "docs" / "synthetic-pilot-protocol.md"


def _protocol():
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def _rendered_cells(protocol):
    cells = []
    template = protocol["prompt_contract"]["user_prompt_template"]
    document_id_template = protocol["design"]["document_id_template"]
    for topic in protocol["topics"]:
        for genre in protocol["genres"]:
            for register in protocol["register_conditions"]:
                document_id = document_id_template.format(
                    topic_id=topic["id"],
                    genre_id=genre["id"],
                    register_id=register["id"],
                )
                prompt = template.format(
                    topic_title=topic["title"],
                    genre_task=topic[genre["task_field"]],
                    genre_instruction=genre["genre_instruction"],
                    register_instruction=register["instruction"],
                )
                cells.append((document_id, prompt))
    return cells


def test_protocol_is_valid_nonexecuting_prespecification():
    protocol = _protocol()
    metadata = protocol["protocol"]
    authorization = protocol["execution_authorization"]

    assert metadata["schema_version"] == "1.0.0"
    assert metadata["status"] == "specified_not_executed"
    assert date.fromisoformat(metadata["frozen_on"]) == date(2026, 7, 24)
    assert authorization["generation_authorized"] is False
    assert authorization["api_calls_during_protocol_authoring"] == 0
    assert authorization["cost_incurred_jpy"] == 0
    assert authorization["credentials_required_in_protocol"] is False


def test_pilot_has_48_unique_complete_factorial_cells():
    protocol = _protocol()
    design = protocol["design"]
    factorial = design["factorial"]

    assert len(protocol["topics"]) == factorial["topics"] == 12
    assert len(protocol["genres"]) == factorial["genres"] == 2
    assert len(protocol["register_conditions"]) == factorial["register_conditions"] == 2
    assert factorial["fixed_model_snapshots"] == 1
    assert factorial["replicates_per_cell"] == 1
    assert math.prod(
        [
            factorial["topics"],
            factorial["genres"],
            factorial["register_conditions"],
            factorial["fixed_model_snapshots"],
            factorial["replicates_per_cell"],
        ]
    ) == factorial["expected_cells"] == design["planned_documents"] == 48

    cells = _rendered_cells(protocol)
    assert len(cells) == 48
    assert len({document_id for document_id, _prompt in cells}) == 48
    assert len({prompt for _document_id, prompt in cells}) == 48
    assert all("{" not in prompt and "}" not in prompt for _document_id, prompt in cells)
    assert {item["id"] for item in protocol["genres"]} == {
        "argumentative",
        "expository",
    }
    assert {item["id"] for item in protocol["register_conditions"]} == {
        "plain",
        "formal_academic",
    }
    assert design["target_words"] == 250
    assert design["word_count_acceptance"] == {
        "minimum": 225,
        "maximum": 275,
        "role": "qc_flag_not_selection_rule",
    }
    assert design["derived_token_prefixes"]["lengths"] == [100, 150, 200]
    assert design["derived_token_prefixes"]["additional_api_calls"] is False


def test_prompts_do_not_simulate_or_label_human_proficiency():
    protocol = _protocol()
    claim = protocol["purpose"]["claim_boundary"]
    prompt_surface = "\n".join(
        [protocol["prompt_contract"]["system_prompt"]]
        + [prompt for _document_id, prompt in _rendered_cells(protocol)]
    ).casefold()

    forbidden_prompt_fragments = (
        "cefr",
        "non-native",
        "second-language learner",
        "english learner",
        "proficiency level",
        "learner-like error",
        "imitate a student",
    )
    assert not any(fragment in prompt_surface for fragment in forbidden_prompt_fragments)
    assert claim["conditions_are_experimental_manipulations_not_quality_labels"] is True
    for prohibited_claim in (
        "human_proficiency_inference",
        "cefr_targeting_or_prediction",
        "human_l2_writer_simulation",
        "proficiency_gold_labels",
        "writing_quality_gold_labels",
        "ai_authorship_detection",
    ):
        assert claim[prohibited_claim] is False
    assert protocol["prompt_contract"]["adaptive_prompting_after_qc"] is False
    assert protocol["prompt_contract"]["metric_results_may_influence_prompts"] is False


def test_preflight_freezes_official_model_and_price_evidence_14_days_early():
    protocol = _protocol()
    preflight = protocol["preflight"]
    required = set(preflight["required_frozen_record_fields"])

    assert preflight["minimum_freeze_interval_days"] == 14
    assert preflight["official_sources_only_for_model_price_and_terms"] is True
    assert preflight["official_pages_rechecked_at_run_start"] is True
    assert preflight["frozen_provider_model_and_price"] is None
    assert {
        "provider",
        "exact_requested_model_snapshot_id",
        "expected_resolved_model_id",
        "official_model_documentation_url",
        "official_pricing_url",
        "official_terms_url",
        "captured_at_utc",
        "first_generation_not_before_utc",
        "billing_currency",
        "input_price_per_million_tokens",
        "output_price_per_million_tokens",
        "price_evidence_sha256",
        "model_capability_evidence_sha256",
    } <= required
    assert preflight["all_gates_must_pass"] is True
    assert len(preflight["gates"]) == 7
    assert {gate["status"] for gate in preflight["gates"]} == {"pending"}
    assert "restart_14_day_clock" in preflight["change_detected_at_recheck"]


def test_budget_retry_and_stop_rules_are_fail_closed():
    protocol = _protocol()
    budget = protocol["budget"]
    retry = protocol["retry_policy"]
    stop = protocol["stop_policy"]

    assert budget["pilot_automatic_stop_jpy"] == 6000
    assert budget["project_total_hard_cap_jpy"] == 10000
    assert budget["contingency_outside_running_pilot_jpy"] == 4000
    assert budget["contingency_available_to_automatic_resume"] is False
    assert budget["pre_request_reservation_required"] is True
    assert budget["all_attempts_count_toward_spend"] is True
    assert budget["failed_or_retried_attempts_are_free"] is False
    assert budget["automatic_restart_after_budget_stop"] is False
    assert budget["boundary_semantics"] == "exclusive_send_inclusive_stop"
    assert "< 6000" in budget["request_send_condition"]
    assert "< 10000" in budget["request_send_condition"]
    assert "<= 6000" not in budget["request_send_condition"]
    assert "<= 10000" not in budget["request_send_condition"]
    assert ">= 6000" in budget["automatic_stop_condition"]
    assert ">= 10000" in budget["automatic_stop_condition"]

    assert retry["maximum_attempts_per_cell"] == 3
    assert retry["maximum_retries_per_cell"] == 2
    assert retry["backoff_seconds"] == [2, 8]
    assert retry["jitter"] is False
    assert retry["same_prompt_and_settings_on_retry"] is True
    assert retry["every_attempt_preserved"] is True
    assert retry["qc_based_best_of_n_selection"] is False
    assert "length_qc_failure" in retry["non_retryable_classes"]
    assert "duplicate_or_near_duplicate_output" in retry["non_retryable_classes"]

    assert stop["operational_run_stop_triggers"] == {
        "consecutive_cells_with_terminal_transport_failure": 3,
        "terminal_failure_fraction": 0.2,
        "minimum_scheduled_cells_before_fraction_rule": 10,
    }
    assert stop["resume_may_not_reset_spend"] is True
    assert stop["silent_manual_override"] is False


def test_qc_preserves_failures_and_uses_deterministic_duplicate_checks():
    protocol = _protocol()
    qc = protocol["quality_control"]
    duplicates = qc["duplicate_detection"]
    disposition = qc["disposition"]

    assert qc["word_count"]["target"] == 250
    assert qc["word_count"]["minimum"] == 225
    assert qc["word_count"]["maximum"] == 275
    assert "SHA-256" in duplicates["exact_method"]
    assert "5-grams" in duplicates["near_method"]
    assert duplicates["scope"] == "all_48_documents_all_pairs"
    assert duplicates["near_duplicate_threshold_inclusive"] == 0.85
    assert duplicates["pairwise_results_recorded"] is True
    assert {
        "provider_refusal",
        "below_word_range",
        "above_word_range",
        "exact_duplicate",
        "near_duplicate",
        "secret_like_content",
    } <= set(qc["required_document_flags"])
    assert disposition["qc_failures_trigger_regeneration"] is False
    assert disposition["qc_failures_are_deleted"] is False
    assert disposition["non_secret_failures_are_released_with_flags"] is True
    assert disposition["secret_or_personal_data_flags_block_public_release"] is True
    assert disposition["qc_flags_are_gold_labels"] is False
    assert disposition["lexical_metric_values_may_drive_qc"] is False


def test_complete_provenance_and_public_secret_boundary_are_explicit():
    protocol = _protocol()
    provenance = protocol["provenance"]
    publication = protocol["publication"]
    attempt_fields = set(provenance["attempt_record_required_fields"])
    document_fields = set(provenance["document_record_required_fields"])

    assert {
        "document_id",
        "attempt_index",
        "retry_reason",
        "full_system_prompt",
        "full_user_prompt",
        "prompt_sha256",
        "provider",
        "requested_model_id",
        "resolved_model_id",
        "request_started_at_utc",
        "response_received_at_utc",
        "provider_request_id",
        "sdk_name_and_version",
        "generation_parameters",
        "sanitized_raw_request_body",
        "sanitized_raw_response_body",
        "finish_reason",
        "input_tokens",
        "output_tokens",
        "attempt_cost_jpy",
        "cumulative_cost_jpy",
        "raw_text_sha256",
        "normalized_text_sha256",
        "qc_flags",
        "generator_git_commit",
        "dependency_lock_sha256",
        "analysis_resource_hashes",
    } <= attempt_fields
    assert {
        "document_id",
        "terminal_status",
        "attempt_record_ids",
        "raw_text",
        "normalized_text",
        "word_count",
        "qc_flags",
        "release_status",
    } <= document_fields
    assert provenance["raw_transport_headers_persisted"] is False
    assert provenance["request_and_response_bodies_sanitized_before_write"] is True

    controls = publication["secret_controls"]
    assert publication["human_or_private_source_text_used"] is False
    assert publication["current_release_status"].startswith("blocked_")
    assert publication["current_repository_boundary"] == {
        "allowed_tracked_paths": [
            "benchmarks/synthetic/pilot-protocol.json",
        ],
        "generated_text_request_or_response_tracking_allowed": False,
        "promotion_requires_new_reviewed_release_gate_allowlist": True,
    }
    assert controls["credentials_in_cli_arguments_or_protocol"] is False
    assert controls["persist_allowlisted_json_fields_only"] is True
    assert controls["secret_scan_before_each_write"] is True
    assert controls["secret_scan_before_release"] is True
    assert controls["release_inventory_allowlist_required"] is True

    serialized = PROTOCOL_PATH.read_text(encoding="utf-8")
    assert "/Users/" not in serialized
    assert "Bearer ey" not in serialized
    assert not re.search(r"\bsk-[A-Za-z0-9_-]{16,}\b", serialized)


def test_analysis_reproduction_is_distinct_from_string_regeneration():
    protocol = _protocol()
    reproducibility = protocol["reproducibility"]
    exit_gate = protocol["pilot_exit_gate"]

    assert reproducibility["analysis_reproduction"]["status"] == (
        "guaranteed_release_requirement"
    )
    assert reproducibility["analysis_reproduction"]["canonical_input"] == (
        "released_normalized_text"
    )
    assert reproducibility["generation_request_replay"]["status"] == "best_effort"
    exact = reproducibility["exact_string_regeneration"]
    assert exact["status"] == "not_guaranteed"
    assert exact["released_text_not_regenerated_text_is_authoritative"] is True
    assert exit_gate["lexical_metric_values_may_determine_core_go_no_go"] is False
    assert exit_gate["pilot_failures_or_unfavorable_outputs_may_be_deleted"] is False


def test_human_readable_protocol_tracks_the_machine_contract():
    document = DOC_PATH.read_text(encoding="utf-8")

    assert "48" in document
    assert "12 × 2 × 2 × 1 × 1" in document
    assert "約250語" in document
    assert "14日前" in document
    assert "6,000円未満" in document
    assert "10,000円未満" in document
    assert "上限と一致するrequestも送らず" in document
    assert "pilot-protocol.json` だけ" in document
    assert "分析再現性はrelease要件として保証" in document
    assert "文字列の完全再生成は保証しない" in document
    assert "APIを呼び出しておらず、費用も発生していない" in document
