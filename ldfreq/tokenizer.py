"""Fixed, versioned tokenization policies for English lexical analysis.

The policy identifier is part of result provenance. Callers therefore select
from this module's registry rather than supplying a prose description that can
drift away from the tokenizer that actually ran.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from types import MappingProxyType
import unicodedata
from typing import Any


ENGLISH_UNICODE_V1 = "english_unicode_v1"
ASCII_LEGACY_V1 = "ascii_legacy_v1"
DEFAULT_TOKENIZER_POLICY = ENGLISH_UNICODE_V1


@dataclass(frozen=True, slots=True)
class TokenizerPolicy:
    """Machine-readable semantics for one immutable tokenizer policy."""

    policy_id: str
    algorithm: str
    pattern: str | None
    normalization: str
    letters: str
    marks: str
    apostrophe_input_code_points: tuple[str, ...]
    apostrophe_output: str
    hyphens_and_dashes: str
    numeric_only_runs: str
    alphanumerics: str
    lowercase: str
    status: str


_POLICIES = MappingProxyType(
    {
        ENGLISH_UNICODE_V1: TokenizerPolicy(
            policy_id=ENGLISH_UNICODE_V1,
            algorithm="unicode_category_scanner_v1",
            pattern=None,
            normalization="NFC",
            letters="Unicode General Category L*",
            marks="Unicode General Category M* following a letter or included mark",
            apostrophe_input_code_points=(
                "U+0027",
                "U+02BC",
                "U+2018",
                "U+2019",
                "U+201B",
            ),
            apostrophe_output=(
                "U+0027 retained only internally between letter components"
            ),
            hyphens_and_dashes="split tokens",
            numeric_only_runs="excluded",
            alphanumerics="digits split adjacent alphabetic components",
            lowercase="Python str.lower when lower=true; no casefold",
            status="default",
        ),
        ASCII_LEGACY_V1: TokenizerPolicy(
            policy_id=ASCII_LEGACY_V1,
            algorithm="ascii_regex_findall_v1",
            pattern="[A-Za-z]+(?:'[A-Za-z]+)?",
            normalization="none",
            letters="ASCII A-Z and a-z only",
            marks="excluded",
            apostrophe_input_code_points=("U+0027",),
            apostrophe_output="one optional internal alphabetic segment",
            hyphens_and_dashes="split tokens",
            numeric_only_runs="excluded",
            alphanumerics="digits split adjacent ASCII alphabetic components",
            lowercase="Python str.lower when lower=true",
            status="legacy-opt-in",
        ),
    }
)

_ASCII_LEGACY_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")
_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "\u02bc": "'",  # modifier letter apostrophe
        "\u2018": "'",  # left single quotation mark
        "\u2019": "'",  # right single quotation mark
        "\u201b": "'",  # single high-reversed-9 quotation mark
    }
)


def tokenizer_policy_ids() -> tuple[str, ...]:
    """Return valid policy identifiers in stable presentation order."""

    return tuple(_POLICIES)


def get_tokenizer_policy(policy_id: str) -> TokenizerPolicy:
    """Return a registered policy, rejecting prose and unknown identifiers."""

    if not isinstance(policy_id, str) or policy_id not in _POLICIES:
        allowed = ", ".join(tokenizer_policy_ids())
        raise ValueError(f"tokenizer_policy must be one of: {allowed}")
    return _POLICIES[policy_id]


def tokenizer_policy_metadata(policy_id: str) -> dict[str, Any]:
    """Return a JSON-ready copy of the fixed policy definition."""

    metadata = asdict(get_tokenizer_policy(policy_id))
    metadata["apostrophe_input_code_points"] = list(
        metadata["apostrophe_input_code_points"]
    )
    return metadata


def _is_letter(character: str) -> bool:
    return unicodedata.category(character).startswith("L")


def _is_mark(character: str) -> bool:
    return unicodedata.category(character).startswith("M")


def _unicode_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFC", text).translate(
        _APOSTROPHE_TRANSLATION
    )
    tokens: list[str] = []
    index = 0
    while index < len(normalized):
        if not _is_letter(normalized[index]):
            index += 1
            continue

        start = index
        index += 1
        while index < len(normalized) and (
            _is_letter(normalized[index]) or _is_mark(normalized[index])
        ):
            index += 1

        # Permit multiple internal apostrophe-delimited letter components, but
        # never keep a leading/trailing apostrophe or use it to join digits.
        while (
            index + 1 < len(normalized)
            and normalized[index] == "'"
            and _is_letter(normalized[index + 1])
        ):
            index += 2
            while index < len(normalized) and (
                _is_letter(normalized[index]) or _is_mark(normalized[index])
            ):
                index += 1

        tokens.append(normalized[start:index])
    return tokens


def tokenize(
    text: str | None,
    lower: bool = True,
    *,
    policy: str = DEFAULT_TOKENIZER_POLICY,
) -> list[str]:
    """Tokenize *text* under one registered, versioned policy.

    ``english_unicode_v1`` is the default for new analyses. Select
    ``ascii_legacy_v1`` explicitly only when reproducing artifacts built with
    the historical ASCII regular expression.
    """

    if type(lower) is not bool:
        raise TypeError("lower must be boolean")
    selected = get_tokenizer_policy(policy)
    if text is None:
        text = ""
    if not isinstance(text, str):
        raise TypeError("text must be a string or None")

    if selected.policy_id == ASCII_LEGACY_V1:
        tokens = _ASCII_LEGACY_RE.findall(text)
    else:
        tokens = _unicode_tokens(text)
    if not lower:
        return tokens
    lowered = [token.lower() for token in tokens]
    if selected.policy_id == ENGLISH_UNICODE_V1:
        return [unicodedata.normalize("NFC", token) for token in lowered]
    return lowered


__all__ = [
    "ASCII_LEGACY_V1",
    "DEFAULT_TOKENIZER_POLICY",
    "ENGLISH_UNICODE_V1",
    "TokenizerPolicy",
    "get_tokenizer_policy",
    "tokenize",
    "tokenizer_policy_ids",
    "tokenizer_policy_metadata",
]
