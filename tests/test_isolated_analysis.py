import os
import copy
from pathlib import Path

import pytest

from ldfreq import analysis as ANALYSIS
from ldfreq import wordlists as WL
from ldfreq.analysis_worker import EVENT_FD_ENV, _event_stream, _wire_encode
from ldfreq.isolated import (
    AnalysisDeadlineExceeded,
    AnalysisInputTooLarge,
    AnalysisProtocolError,
    AnalysisWorkerError,
    IsolationLimits,
    ResourceSpec,
    _batch_from_message,
    _worker_environment,
    analyze_documents_isolated,
)
from ldfreq.lemmatizers import WordFormLemmatizer
from ldfreq.privacy import sensitive_paths


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


def test_isolated_worker_matches_direct_analysis_and_preserves_integer_keys(capsys):
    canary = "CANARYqzxvplmSECRET"
    texts = [
        f"The student uses varied academic vocabulary in careful written analysis {canary}.",
        "Another learner writes clearly with several different words.",
    ]
    entry = WL.by_id("nj8")
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
        ResourceSpec("nj8", "word_form", False),
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
            ResourceSpec("nj8", "word_form", True),
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
            ResourceSpec("nj8", "word_form", False),
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
            ResourceSpec("nj8", "word_form", False),
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
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "/private/credential.json")
    environment = _worker_environment(17)
    assert environment[EVENT_FD_ENV] == "17"
    assert "LDFREQ_NJ8_CSV_B64" not in environment
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in environment
    assert "CANARYbase64PAYLOAD" not in repr(environment)


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
