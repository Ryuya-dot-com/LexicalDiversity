import ast
import os
import copy
from pathlib import Path

import pytest

from ldfreq import analysis as ANALYSIS
from ldfreq import wordlists as WL
from ldfreq.analysis_worker import (
    EVENT_FD_ENV,
    PROTOCOL_VERSION as WORKER_PROTOCOL_VERSION,
    _event_stream,
    _wire_encode,
)
from ldfreq.isolated import (
    AnalysisDeadlineExceeded,
    AnalysisInputTooLarge,
    AnalysisProtocolError,
    AnalysisWorkerError,
    IsolationLimits,
    PROTOCOL_VERSION as PARENT_PROTOCOL_VERSION,
    ResourceSpec,
    _batch_from_message,
    _worker_environment,
    analyze_documents_isolated,
)
from ldfreq.lemmatizers import WordFormLemmatizer
from ldfreq.privacy import sensitive_paths
from ldfreq.server_only_gate import SERVER_ONLY_CONTROL_PROFILE


pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process isolation")


def _config():
    return ANALYSIS.AnalysisConfig(
        thresholds=(90,),
        min_tokens=1,
        msttr_segment=5,
        mattr_window=5,
        hdd_sample=5,
    )


def _assert_reaped(pid: int) -> None:
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_parent_and_worker_protocol_versions_match_structured_record_envelope():
    assert PARENT_PROTOCOL_VERSION == WORKER_PROTOCOL_VERSION == 2


def test_isolated_worker_matches_direct_analysis_and_preserves_integer_keys(capsys):
    canary = "CANARYqzxvplmSECRET"
    texts = [
        f"The student uses varied academic vocabulary in careful written analysis {canary}.",
        "Another learner writes clearly with several different words.",
    ]
    entry = WL.by_id("ngsl")
    rank, meta = entry["loader"](entry["path"])
    direct_entry = dict(entry)
    direct_entry["delivery_mode"] = "bundled-public"
    direct = ANALYSIS.analyze_documents(
        texts,
        _config(),
        resources=ANALYSIS.AnalysisResources(
            lemmatizer=WordFormLemmatizer(),
            rank_map=rank,
            list_meta=meta,
            list_entry=direct_entry,
            list_path=entry["path"],
        ),
    )

    progress = []
    pids = []
    isolated = analyze_documents_isolated(
        [
            {"name": "student-secret-name.txt", "text": texts[0]},
            {"name": "another-secret-name.txt", "text": texts[1]},
        ],
        _config(),
        ResourceSpec("ngsl", "word_form", False),
        limits=IsolationLimits(deadline_seconds=20),
        progress=lambda *event: progress.append(event),
        _on_worker_started=pids.append,
    )

    assert isolated == direct
    assert progress == [
        (1, 2, "Document 001"),
        (2, 2, "Document 002"),
    ]
    assert list(isolated.results[0]["panel_b"]["coverage_threshold"]) == [90]
    assert sensitive_paths(
        {"results": isolated.results, "payload": isolated.payload, "skipped": isolated.skipped}
    ) == []
    captured = capsys.readouterr()
    for output in (repr(isolated), captured.out, captured.err):
        assert canary not in output
        assert "student-secret-name.txt" not in output
    assert len(pids) == 1
    _assert_reaped(pids[0])


def test_public_default_open_flemma_runs_with_verified_open_resources():
    isolated = analyze_documents_isolated(
        ["The children went home and quizzed one another."],
        _config(),
        ResourceSpec(),
        limits=IsolationLimits(deadline_seconds=20),
    )

    effective = isolated.results[0]["effective_lemmatizer"]
    assert effective["name"] == "open_flemma"
    assert effective["version"].startswith("open-flemma-1.0.0+")
    semantic = isolated.results[0]["semantic_network"]
    assert semantic["normalizer"].startswith("open_flemma open-flemma-1.0.0+")


def test_public_tubelex_axis_loads_only_verified_aggregate_artifact():
    isolated = analyze_documents_isolated(
        ["Don't repeat common words; choose well-being and analysis."],
        _config(),
        ResourceSpec(tubelex_enabled=True),
        limits=IsolationLimits(deadline_seconds=30),
    )

    tubelex = isolated.results[0]["tubelex"]
    assert tubelex["tokens"] > 0
    assert 0.0 <= tubelex["token_coverage"] <= 1.0
    assert tubelex["frequency_zipf_token_mean"] is not None
    assert tubelex["metadata"]["method_id"].startswith(
        "tubelex-en-treebank-7cb5fb36"
    )
    assert tubelex["metadata"]["source_asset"] == "tubelex-en-treebank.tsv.xz"
    assert "common" not in repr(tubelex)


def test_deadline_kills_and_reaps_the_worker():
    pids = []
    with pytest.raises(AnalysisDeadlineExceeded, match="processing deadline"):
        analyze_documents_isolated(
            ["alpha beta gamma"],
            _config(),
            ResourceSpec("ngsl", "word_form", True),
            limits=IsolationLimits(
                deadline_seconds=0.1,
                poll_seconds=0.005,
                termination_grace_seconds=0.01,
            ),
            _on_worker_started=pids.append,
        )
    assert len(pids) == 1
    _assert_reaped(pids[0])


def test_base_exception_from_progress_callback_reaps_before_propagating():
    class StreamlitStyleRerun(BaseException):
        pass

    pids = []

    def stop_after_progress(_completed, _total, _label):
        raise StreamlitStyleRerun

    with pytest.raises(StreamlitStyleRerun):
        analyze_documents_isolated(
            ["alpha beta gamma", "delta epsilon zeta"],
            _config(),
            ResourceSpec("ngsl", "word_form", False),
            limits=IsolationLimits(deadline_seconds=20),
            progress=stop_after_progress,
            _on_worker_started=pids.append,
        )
    assert len(pids) == 1
    _assert_reaped(pids[0])


def test_source_limit_rejects_before_starting_a_worker():
    pids = []
    with pytest.raises(AnalysisInputTooLarge, match="byte limit"):
        analyze_documents_isolated(
            ["eleven-bytes"],
            _config(),
            ResourceSpec("ngsl", "word_form", False),
            limits=IsolationLimits(max_source_bytes=10),
            _on_worker_started=pids.append,
        )
    assert pids == []


def test_unavailable_resource_returns_only_a_fixed_error(capsys):
    canary = "CANARYunavailableRESOURCE"
    with pytest.raises(AnalysisWorkerError) as caught:
        analyze_documents_isolated(
            [canary],
            _config(),
            ResourceSpec("not_installed", "word_form", False),
            limits=IsolationLimits(deadline_seconds=20),
        )
    assert caught.value.code == "resource-unavailable"
    captured = capsys.readouterr()
    for output in (str(caught.value), repr(caught.value), captured.out, captured.err):
        assert canary not in output
        assert "not_installed" not in output


def test_result_protocol_rejects_extra_fields_and_request_derived_label():
    item = {
        "name": "Document 001",
        "n_tokens": 2,
        "n_types": 2,
        "indices": {},
        "index_records": {},
        "panel_b": {"coverage_threshold": {90: None}},
        "semantic_network": None,
        "tubelex": None,
        "list_meta": {},
        "list_entry": None,
        "list_path": None,
        "effective_lemmatizer": {"name": "word_form", "version": "-"},
        "payload": {"document": {"name": "Document 001"}},
    }

    def message(result):
        return {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode([result]),
            "payload": _wire_encode({}),
            "skipped": _wire_encode([]),
        }

    decoded = _batch_from_message(message(item), expected_documents=1)
    assert decoded.results[0]["panel_b"]["coverage_threshold"] == {90: None}

    leaked = dict(item, name="CANARYqzxvplmSECRET")
    leaked["payload"] = {"document": {"name": leaked["name"]}}
    with pytest.raises(AnalysisProtocolError, match="label"):
        _batch_from_message(message(leaked), expected_documents=1)

    extra = dict(item, source_copy="CANARYqzxvplmSECRET")
    with pytest.raises(AnalysisProtocolError, match="schema"):
        _batch_from_message(message(extra), expected_documents=1)


def test_strict_payload_rejects_canary_hidden_in_allowed_metadata_field():
    config = _config()
    resources = ResourceSpec(None, "word_form", False)
    direct = ANALYSIS.analyze_documents(
        ["alpha beta"],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )

    def message(results, payload):
        return {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode(results),
            "payload": _wire_encode(payload),
            "skipped": _wire_encode([]),
        }

    valid_results = list(direct.results)
    decoded = _batch_from_message(
        message(valid_results, direct.payload),
        expected_documents=1,
        config=config,
        resources=resources,
    )
    assert decoded == direct

    for field, value in (
        ("tokenizer_policy", "ascii_legacy_v1"),
        ("mattr_window", config.mattr_window + 1),
        ("list_id", "forged-resource-id"),
    ):
        tampered_results = copy.deepcopy(valid_results)
        tampered_results[0]["payload"]["settings"][field] = value
        tampered_payload = tampered_results[0]["payload"]
        with pytest.raises(AnalysisProtocolError, match="settings metadata"):
            _batch_from_message(
                message(tampered_results, tampered_payload),
                expected_documents=1,
                config=config,
                resources=resources,
            )

    leaked_results = copy.deepcopy(valid_results)
    leaked_results[0]["payload"]["method_notes"][0] = "CANARYqzxvplmSECRET"
    leaked_payload = leaked_results[0]["payload"]
    with pytest.raises(AnalysisProtocolError, match="method metadata"):
        _batch_from_message(
            message(leaked_results, leaked_payload),
            expected_documents=1,
            config=config,
            resources=resources,
        )

    for field, value in (
        ("list_path", "CANARY-safe-filename.csv"),
        ("list_meta", {"note": "CANARYresourceSECRET"}),
        ("list_entry", {"name": "CANARYresourceSECRET"}),
        (
            "effective_lemmatizer",
            {"name": "word_form", "version": "CANARYresourceSECRET"},
        ),
    ):
        tampered_results = copy.deepcopy(valid_results)
        tampered_results[0][field] = value
        with pytest.raises(
            AnalysisProtocolError,
            match="retained resource metadata",
        ):
            _batch_from_message(
                message(tampered_results, tampered_results[0]["payload"]),
                expected_documents=1,
                config=config,
                resources=resources,
            )


def test_strict_payload_rejects_self_consistent_panel_a_formula_and_domain_tampering():
    config = _config()
    resources = ResourceSpec(None, "word_form", False)
    direct = ANALYSIS.analyze_documents(
        ["alpha beta alpha gamma"],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )

    def message(result):
        return {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode([result]),
            "payload": _wire_encode(result["payload"]),
            "skipped": _wire_encode([]),
        }

    def tamper_metric(key, *, value, status, reason):
        result = copy.deepcopy(direct.results[0])
        result["indices"][key] = value
        result["payload"]["panel_a"][key] = value
        record = result["index_records"][key]
        payload_record = result["payload"]["panel_a_records"][key]
        for target in (record, payload_record):
            target["value"] = value
            target["status"] = status
            target["missing_reason"] = reason
            target["effective_parameters"] = (
                target["requested_parameters"] if status == "available" else {}
            )
        return result

    attacks = (
        tamper_metric(
            "ttr",
            value=None,
            status="missing",
            reason="undefined_for_text",
        ),
        tamper_metric("ttr", value=0.5, status="available", reason=None),
        tamper_metric("msttr", value=1.0, status="available", reason=None),
        tamper_metric(
            "mtld",
            value=None,
            status="missing",
            reason="no_factor",
        ),
        tamper_metric(
            "yule_i",
            value=None,
            status="missing",
            reason="zero_denominator",
        ),
    )
    for result in attacks:
        with pytest.raises(AnalysisProtocolError, match="formula/domain invariants"):
            _batch_from_message(
                message(result),
                expected_documents=1,
                config=config,
                resources=resources,
            )

    hapax_direct = ANALYSIS.analyze_documents(
        ["alpha beta gamma delta"],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )
    hapax_result = copy.deepcopy(hapax_direct.results[0])
    hapax_result["indices"]["yule_i"] = 1.0
    hapax_result["payload"]["panel_a"]["yule_i"] = 1.0
    for target in (
        hapax_result["index_records"]["yule_i"],
        hapax_result["payload"]["panel_a_records"]["yule_i"],
    ):
        target["value"] = 1.0
        target["status"] = "available"
        target["missing_reason"] = None
        target["effective_parameters"] = target["requested_parameters"]
    with pytest.raises(AnalysisProtocolError, match="formula/domain invariants"):
        _batch_from_message(
            message(hapax_result),
            expected_documents=1,
            config=config,
            resources=resources,
        )


def test_strict_payload_rejects_available_values_outside_mathematical_domains():
    config = _config()
    resources = ResourceSpec(None, "word_form", False)
    text = " ".join(["alpha", "beta", "gamma"] * 20)
    direct = ANALYSIS.analyze_documents(
        [text],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )

    for key, forged_value in (
        ("msttr", 999.0),
        ("mattr", 999.0),
        ("mtld", -7.0),
        ("hdd", 999.0),
        ("yule_k", -5.0),
    ):
        result = copy.deepcopy(direct.results[0])
        result["indices"][key] = forged_value
        result["payload"]["panel_a"][key] = forged_value
        result["index_records"][key]["value"] = forged_value
        result["payload"]["panel_a_records"][key]["value"] = forged_value
        message = {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode([result]),
            "payload": _wire_encode(result["payload"]),
            "skipped": _wire_encode([]),
        }
        with pytest.raises(AnalysisProtocolError, match="mathematical domain"):
            _batch_from_message(
                message,
                expected_documents=1,
                config=config,
                resources=resources,
            )


def test_strict_payload_rejects_unexpected_panel_b_and_semantic_content():
    config = _config()
    resources = ResourceSpec(None, "word_form", False)
    direct = ANALYSIS.analyze_documents(
        ["alpha beta alpha"],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )

    for field, forged, error in (
        (
            "panel_b",
            {"mapping_diagnostics": {"submitted_term": "CANARYpanelSECRET"}},
            "Panel B payload was unexpected",
        ),
        (
            "semantic_network",
            {"term": "CANARYsemanticSECRET"},
            "semantic payload was unexpected",
        ),
    ):
        result = copy.deepcopy(direct.results[0])
        result[field] = forged
        result["payload"][field] = forged
        message = {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode([result]),
            "payload": _wire_encode(result["payload"]),
            "skipped": _wire_encode([]),
        }
        with pytest.raises(AnalysisProtocolError, match=error):
            _batch_from_message(
                message,
                expected_documents=1,
                config=config,
                resources=resources,
            )


def test_strict_payload_rejects_panel_b_schema_and_aggregate_tampering():
    config = _config()
    resources = ResourceSpec("ngsl", "word_form", False)
    entry = WL.by_id("ngsl")
    rank, meta = entry["loader"](entry["path"])
    direct_entry = dict(entry)
    direct_entry["delivery_mode"] = "bundled-public"
    direct = ANALYSIS.analyze_documents(
        ["alpha beta gamma delta epsilon alpha beta gamma delta epsilon"],
        config,
        resources=ANALYSIS.AnalysisResources(
            lemmatizer=WordFormLemmatizer(),
            rank_map=rank,
            list_meta=meta,
            list_entry=direct_entry,
            list_path=entry["path"],
        ),
    )

    valid_result = copy.deepcopy(direct.results[0])
    valid_message = {
        "type": "result",
        "encoding": "typed-map-v1",
        "results": _wire_encode([valid_result]),
        "payload": _wire_encode(valid_result["payload"]),
        "skipped": _wire_encode([]),
    }
    assert _batch_from_message(
        valid_message,
        expected_documents=1,
        config=config,
        resources=resources,
    ) == direct

    attacks = []
    unknown_key = copy.deepcopy(valid_result)
    for target in (
        unknown_key["panel_b"]["mapping_diagnostics"],
        unknown_key["payload"]["panel_b"]["mapping_diagnostics"],
    ):
        target["submitted_term"] = "CANARYpanelSECRET"
    attacks.append((unknown_key, "mapping schema"))

    bad_rate = copy.deepcopy(valid_result)
    for target in (
        bad_rate["panel_b"]["mapping_diagnostics"],
        bad_rate["payload"]["panel_b"]["mapping_diagnostics"],
    ):
        target["identity_fallback_rate"] = 0.25
    attacks.append((bad_rate, "mapping rates"))

    bad_advanced = copy.deepcopy(valid_result)
    bad_advanced["panel_b"]["advanced_guiraud"] = 999.0
    bad_advanced["payload"]["panel_b"]["advanced_guiraud"] = 999.0
    attacks.append((bad_advanced, "richness values"))

    for result, error in attacks:
        message = {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode([result]),
            "payload": _wire_encode(result["payload"]),
            "skipped": _wire_encode([]),
        }
        with pytest.raises(AnalysisProtocolError, match=error):
            _batch_from_message(
                message,
                expected_documents=1,
                config=config,
                resources=resources,
            )


def test_strict_payload_rejects_unknown_or_content_bearing_batch_diagnostics():
    config = _config()
    resources = ResourceSpec(None, "word_form", False)
    direct = ANALYSIS.analyze_documents(
        ["alpha beta alpha", "beta gamma delta"],
        config,
        resources=ANALYSIS.AnalysisResources(lemmatizer=WordFormLemmatizer()),
    )

    def message(payload):
        return {
            "type": "result",
            "encoding": "typed-map-v1",
            "results": _wire_encode(list(direct.results)),
            "payload": _wire_encode(payload),
            "skipped": _wire_encode([]),
        }

    assert _batch_from_message(
        message(direct.payload),
        expected_documents=2,
        config=config,
        resources=resources,
    ) == direct

    extra = copy.deepcopy(direct.payload)
    extra["batch_diagnostics"]["submitted_terms"] = ["CANARYbatchSECRET"]
    with pytest.raises(AnalysisProtocolError, match="batch diagnostics schema"):
        _batch_from_message(
            message(extra),
            expected_documents=2,
            config=config,
            resources=resources,
        )

    nested = copy.deepcopy(direct.payload)
    nested["batch_diagnostics"]["overlap_matrix"][0]["term"] = (
        "CANARYbatchSECRET"
    )
    with pytest.raises(AnalysisProtocolError, match="overlap matrix schema"):
        _batch_from_message(
            message(nested),
            expected_documents=2,
            config=config,
            resources=resources,
        )


@pytest.mark.parametrize("field,value", [("deadline_seconds", True), ("poll_seconds", "1")])
def test_time_limits_reject_non_numeric_values(field, value):
    with pytest.raises(ValueError, match=field):
        IsolationLimits(**{field: value})


def test_worker_disables_core_dumps_before_reading_source():
    source = Path("ldfreq/analysis_worker.py").read_text(encoding="utf-8")
    assert source.index("resource.setrlimit(resource.RLIMIT_CORE, (0, 0))") < source.index(
        "request = _read_request()"
    )


def test_worker_environment_omits_payloads_and_cloud_credentials(monkeypatch):
    monkeypatch.setenv("LDFREQ_NJ8_CSV_B64", "CANARYbase64PAYLOAD")
    monkeypatch.setenv("LDFREQ_NJ8_PATH", "/private/local-nj8.csv")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/private/credential.json")
    environment = _worker_environment(17)
    assert environment[EVENT_FD_ENV] == "17"
    assert "LDFREQ_NJ8_CSV_B64" not in environment
    assert "LDFREQ_NJ8_PATH" not in environment
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
    assert "CANARYbase64PAYLOAD" not in repr(environment)


def test_worker_environment_omits_partial_server_only_gate_and_paths(monkeypatch):
    monkeypatch.setenv("LDFREQ_SERVER_ONLY_RESOURCE_IDS", "bnc_coca")
    monkeypatch.setenv("LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED", "1")
    monkeypatch.setenv(
        "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION",
        SERVER_ONLY_CONTROL_PROFILE,
    )
    monkeypatch.setenv(
        "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID",
        "",
    )
    monkeypatch.setenv("LDFREQ_BNCCOCA_PATH", "/private/nation")

    environment = _worker_environment(17)

    assert "LDFREQ_SERVER_ONLY_RESOURCE_IDS" not in environment
    assert "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED" not in environment
    assert "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION" not in environment
    assert "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID" not in environment
    assert "LDFREQ_BNCCOCA_PATH" not in environment


def test_worker_environment_forwards_complete_gate_and_enabled_path(monkeypatch):
    monkeypatch.setenv("LDFREQ_SERVER_ONLY_RESOURCE_IDS", "bnc_coca")
    monkeypatch.setenv("LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED", "1")
    monkeypatch.setenv(
        "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION",
        SERVER_ONLY_CONTROL_PROFILE,
    )
    monkeypatch.setenv(
        "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID",
        "GRC-2026-08-24-001",
    )
    monkeypatch.setenv("LDFREQ_BNCCOCA_PATH", "/private/nation")
    monkeypatch.setenv(
        "LDFREQ_NATION_BNCCOCA_INDEX_PATH",
        "/private/nation-family.csv.gz",
    )

    environment = _worker_environment(17)

    assert environment["LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION"] == (
        SERVER_ONLY_CONTROL_PROFILE
    )
    assert environment["LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID"] == (
        "GRC-2026-08-24-001"
    )
    assert environment["LDFREQ_BNCCOCA_PATH"] == "/private/nation"
    assert "LDFREQ_NATION_BNCCOCA_INDEX_PATH" not in environment


def test_worker_environment_keeps_local_override_distinct_from_public_gate(
    monkeypatch,
):
    monkeypatch.setenv("LDFREQ_SERVING_MODE", "local")
    monkeypatch.setenv("LDFREQ_ALLOW_LOCAL_RESTRICTED", "1")
    monkeypatch.setenv("LDFREQ_NJ8_PATH", "/private/local-nj8.csv")
    monkeypatch.setenv("LDFREQ_ANTBNC_PATH", "/private/local-antbnc")
    monkeypatch.setenv("LDFREQ_BNCCOCA_PATH", "/private/local-nation")

    environment = _worker_environment(17)

    assert environment["LDFREQ_NJ8_PATH"] == "/private/local-nj8.csv"
    assert environment["LDFREQ_ANTBNC_PATH"] == "/private/local-antbnc"
    assert environment["LDFREQ_BNCCOCA_PATH"] == "/private/local-nation"
    assert "LDFREQ_SERVER_ONLY_RESOURCE_IDS" not in environment
    assert "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION" not in environment
    assert "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID" not in environment


def test_worker_event_fd_is_close_on_exec_and_removed_from_environment(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        os.set_inheritable(write_fd, True)
        monkeypatch.setenv(EVENT_FD_ENV, str(write_fd))
        stream = _event_stream()
        try:
            assert not os.get_inheritable(write_fd)
            assert EVENT_FD_ENV not in os.environ
        finally:
            stream.close()
    finally:
        os.close(read_fd)


def test_streamlit_path_uses_isolation_instead_of_direct_core_call():
    source = Path("app.py").read_text(encoding="utf-8")
    assert "ISOLATED.analyze_documents_isolated(" in source
    assert "ANALYSIS.analyze_documents(" not in source
    assert "@st.cache_resource" not in source


def test_streamlit_panel_a_renderer_does_not_invent_record_fields():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_panel_a_rows"
    )
    function_source = ast.get_source_segment(source, function)

    assert function_source is not None
    assert "_validated_panel_a_records(" in function_source
    assert "record.get(" not in function_source
    assert "IDX.effective_min_tokens(" not in function_source
    assert 'result["indices"][k]' not in function_source
