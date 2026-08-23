import json
from pathlib import Path

import pytest

from ldfreq.tokenizer import (
    ASCII_LEGACY_V1,
    DEFAULT_TOKENIZER_POLICY,
    ENGLISH_UNICODE_V1,
    get_tokenizer_policy,
    tokenize,
    tokenizer_policy_ids,
    tokenizer_policy_metadata,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_registry_matches_machine_readable_contract():
    contract = json.loads(
        (PROJECT_ROOT / "docs" / "tokenizer-contract.json").read_text(
            encoding="utf-8"
        )
    )

    assert contract["contract_id"] == "ldfreq-tokenizer"
    assert contract["contract_version"] == "1.0.0"
    assert contract["default_policy_id"] == DEFAULT_TOKENIZER_POLICY
    assert tuple(contract["policies"]) == tokenizer_policy_ids()
    assert contract["provenance"] == {
        "field": "settings.tokenizer_policy",
        "value_kind": "registered policy ID",
        "free_text_allowed": False,
        "validated_before_tokenization": True,
    }
    for policy_id, declared in contract["policies"].items():
        runtime = tokenizer_policy_metadata(policy_id)
        assert runtime == {"policy_id": policy_id, **declared}

    scope = json.loads(
        (PROJECT_ROOT / "docs" / "v1-metric-scope.json").read_text(
            encoding="utf-8"
        )
    )["tokenizer_contract"]
    assert scope == {
        "contract_path": "docs/tokenizer-contract.json",
        "default_policy_id": DEFAULT_TOKENIZER_POLICY,
        "allowed_policy_ids": list(tokenizer_policy_ids()),
        "provenance_field": "settings.tokenizer_policy",
        "free_text_policy_values_allowed": False,
        "masc_derived_aggregates_policy_id": ASCII_LEGACY_V1,
    }


def test_policy_lookup_rejects_free_text_and_unknown_identifiers():
    assert get_tokenizer_policy(ENGLISH_UNICODE_V1).status == "default"
    assert get_tokenizer_policy(ASCII_LEGACY_V1).status == "legacy-opt-in"
    with pytest.raises(ValueError, match="tokenizer_policy must be one of"):
        get_tokenizer_policy("Unicode words with apostrophes")
    with pytest.raises(ValueError, match="tokenizer_policy must be one of"):
        tokenize("text", policy="english_unicode_latest")


def test_unicode_policy_applies_nfc_letters_marks_and_apostrophe_mapping():
    text = "Café cafe\u0301 naïve Don’t Johnʼs rock‘n’roll"

    assert tokenize(text) == [
        "café",
        "café",
        "naïve",
        "don't",
        "john's",
        "rock'n'roll",
    ]
    assert tokenize("E\u0301COLE Don’t", lower=False) == ["ÉCOLE", "Don't"]


def test_unicode_policy_has_explicit_hyphen_numeric_and_alphanumeric_rules():
    assert tokenize("well-known 42 abc123def ３ cats") == [
        "well",
        "known",
        "abc",
        "def",
        "cats",
    ]
    assert tokenize("'quoted' trailing' 'leading") == [
        "quoted",
        "trailing",
        "leading",
    ]


def test_ascii_legacy_policy_preserves_the_historical_regular_expression():
    text = "Café don’t well-known 42 abc123def rock'n'roll"

    assert tokenize(text, policy=ASCII_LEGACY_V1) == [
        "caf",
        "don",
        "t",
        "well",
        "known",
        "abc",
        "def",
        "rock'n",
        "roll",
    ]


def test_none_is_empty_and_non_string_input_is_rejected():
    assert tokenize(None) == []
    with pytest.raises(TypeError, match="text must be a string or None"):
        tokenize(123)  # type: ignore[arg-type]
    for invalid in (1, 0, "false", None):
        with pytest.raises(TypeError, match="lower must be boolean"):
            tokenize("TEXT", lower=invalid)  # type: ignore[arg-type]
