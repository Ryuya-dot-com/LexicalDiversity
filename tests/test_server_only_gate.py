import pytest

from ldfreq.server_only_gate import (
    SERVER_ONLY_CONTROL_ATTESTATION_ENV,
    SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV,
    SERVER_ONLY_CONTROL_PROFILE,
    SERVER_ONLY_RESOURCE_IDS_ENV,
    SERVER_ONLY_RIGHTS_ACK_ENV,
    configured_server_only_ids,
    controls_attested,
    valid_control_evidence_id,
)


ELIGIBLE = {"bnc_coca", "nation_bnc_coca_families"}
VALID_EVIDENCE_ID = "GRC-2026-08-24-001"


def _complete_environment() -> dict[str, str]:
    return {
        SERVER_ONLY_RESOURCE_IDS_ENV: (
            "bnc_coca,nation_bnc_coca_families,unreviewed_resource"
        ),
        SERVER_ONLY_RIGHTS_ACK_ENV: "1",
        SERVER_ONLY_CONTROL_ATTESTATION_ENV: SERVER_ONLY_CONTROL_PROFILE,
        SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV: VALID_EVIDENCE_ID,
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        " GRC-2026-08-24-001",
        "grc-2026-08-24-001",
        "https://evidence.example/record/1",
        "../../private/evidence",
        "GRC-TODO-2026",
        "GRC-SECRET-2026",
        "GRC-PASSWORD-2026",
        "GRC-SAMPLE-2026",
        "GRC-TEST123-2026",
        "GRC-MYSECRET-2026",
        "SHA-256-ABCD",
        "ABCDEF01-23456789-ABCDEF01-23456789",
        "PLACEHOLDER-2026-001",
        "A-" + "B" * 63,
    ],
)
def test_control_evidence_id_rejects_paths_secrets_and_placeholders(value):
    assert not valid_control_evidence_id(value)


def test_control_evidence_id_accepts_only_a_short_opaque_reference():
    assert valid_control_evidence_id(VALID_EVIDENCE_ID)
    assert valid_control_evidence_id("SECOPS-2026-042")


@pytest.mark.parametrize(
    "missing_or_invalid",
    [
        SERVER_ONLY_RESOURCE_IDS_ENV,
        SERVER_ONLY_RIGHTS_ACK_ENV,
        SERVER_ONLY_CONTROL_ATTESTATION_ENV,
        SERVER_ONLY_CONTROL_EVIDENCE_ID_ENV,
    ],
)
def test_every_activation_input_is_required(missing_or_invalid):
    environment = _complete_environment()
    environment[missing_or_invalid] = ""

    assert configured_server_only_ids(ELIGIBLE, environment) == frozenset()


def test_wrong_profile_and_mixed_unreviewed_allowlist_fail_closed():
    environment = _complete_environment()
    environment[SERVER_ONLY_CONTROL_ATTESTATION_ENV] = "shared-controls-latest"
    assert not controls_attested(environment)
    assert configured_server_only_ids(ELIGIBLE, environment) == frozenset()

    environment = _complete_environment()
    assert controls_attested(environment)
    assert configured_server_only_ids(ELIGIBLE, environment) == frozenset()

    environment[SERVER_ONLY_RESOURCE_IDS_ENV] = ",".join(sorted(ELIGIBLE))
    assert configured_server_only_ids(ELIGIBLE, environment) == frozenset(ELIGIBLE)


def test_evidence_reference_is_not_returned_as_runtime_resource_metadata():
    environment = _complete_environment()
    enabled = configured_server_only_ids(ELIGIBLE, environment)

    assert VALID_EVIDENCE_ID not in repr(enabled)
