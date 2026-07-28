import hashlib
import json
import platform
from pathlib import Path

import pytest

from ldfreq import OUTPUT_SCHEMA_VERSION, RELEASE_PHASE, __version__
from ldfreq.exporting import payload_to_excel, payload_to_json
from scripts import build_v1_golden_fixtures as golden
from scripts.check_runtime_environment import runtime_environment_violations


ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def test_golden_fixture_inventory_is_self_consistent_and_public_only():
    manifest = json.loads(golden.MANIFEST_PATH.read_text(encoding="utf-8"))

    assert manifest["fixture_id"] == "ldfreq-public-v1-golden"
    assert manifest["license"] == "CC0-1.0"
    assert manifest["human_or_learner_writing"] is False
    assert manifest["external_api_calls"] == 0
    assert manifest["schema_version"] == 3
    assert manifest["release_identity"]["application_version"] == __version__
    assert manifest["release_identity"]["output_schema_version"] == (
        OUTPUT_SCHEMA_VERSION
    )
    assert manifest["release_identity"]["release_phase"] == RELEASE_PHASE
    assert manifest["serialization"]["release_image_digest_frozen"] is False

    for path in golden.INPUT_PATHS:
        recorded = manifest["inputs"][path.name]
        payload = path.read_bytes()
        assert len(payload) == recorded["bytes"]
        assert _sha256(payload) == recorded["sha256"]

    for path in golden.EXPECTED_PATHS.values():
        recorded = manifest["expected_files"][path.name]
        payload = path.read_bytes()
        assert len(payload) == recorded["bytes"]
        assert _sha256(payload) == recorded["sha256"]
        assert str(ROOT) not in payload.decode("utf-8")


def test_canonical_analysis_and_serialization_match_golden_outputs():
    violations, _pins = runtime_environment_violations()
    if violations:
        pytest.skip("requires the audited Python 3.12 clean runtime")

    files, manifest = golden.build_artifacts()

    assert golden.check_artifacts(files, manifest) == []
    single = json.loads(files["single_json"])
    batch = json.loads(files["batch_json"])
    assert batch["documents"][0] == single
    assert single["ldfreq_version"] == __version__
    assert single["output_schema_version"] == OUTPUT_SCHEMA_VERSION
    assert batch["batch"]["n_documents"] == 2
    assert [doc["document"]["name"] for doc in batch["documents"]] == [
        "Document 001",
        "Document 002",
    ]

    single_xlsx_first = payload_to_excel(single)
    single_xlsx_second = payload_to_excel(single)
    assert single_xlsx_first == single_xlsx_second
    assert golden.workbook_snapshot(single_xlsx_first) == json.loads(
        files["single_workbook"]
    )
    assert payload_to_json(batch).encode("utf-8") == files["batch_json"]

    recorded = json.loads(golden.MANIFEST_PATH.read_text(encoding="utf-8"))
    if (
        recorded["generator"]["python"] == platform.python_version()
        and recorded["generator"]["platform"] == platform.platform()
    ):
        assert _sha256(single_xlsx_first) == recorded["provisional_xlsx_binary"][
            "single"
        ]["sha256"]
