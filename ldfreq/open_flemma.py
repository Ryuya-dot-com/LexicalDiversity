"""Deterministic, POS-agnostic English flemma normalization.

This module is deliberately independent of AntBNC and of a statistical model.
It combines three transparent sources of evidence:

* an optional, rights-reviewed form-to-head lexicon (NGSL is supported);
* a small code-owned table of common irregular inflections; and
* conservative English inflection rules whose candidates must be known heads.

The ambiguity policy is intentionally conservative.  If a spelling can map to
more than one known head without part-of-speech or sentence context, the input
spelling is retained.  For example, ``found`` is not forced to ``find`` because
``found`` is also a head, and ``saw`` is not forced to ``see`` because the noun
and verb ``saw`` exist.  This makes the behavior reproducible and prevents a
POS-less normalizer from silently inventing disambiguation accuracy.

The algorithm never stems derivational suffixes such as ``-ly``, ``-ness``, or
``-tion``.  A flemma is an inflectional unit, not a word family.
"""
from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
from pathlib import Path
from types import MappingProxyType
from typing import Iterable, Mapping, Sequence


ALGORITHM_VERSION = "open-flemma-1.0.0"


# Facts about common English inflection are encoded here rather than copied
# from a restricted lemma list.  Multiple heads mean genuine POS-less
# ambiguity and therefore cause surface-form retention.
_IRREGULAR_HEADS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        # Function verbs and high-frequency lexical verbs.
        "am": ("be",),
        "are": ("be",),
        "been": ("be",),
        "being": ("be",),
        "did": ("do",),
        "does": ("do",),
        "done": ("do",),
        "had": ("have",),
        "has": ("have",),
        "is": ("be",),
        "was": ("be",),
        "were": ("be",),
        "went": ("go",),
        "gone": ("go",),
        "made": ("make",),
        "took": ("take",),
        "taken": ("take",),
        "came": ("come",),
        "seen": ("see",),
        "gave": ("give",),
        "given": ("give",),
        "knew": ("know",),
        "known": ("know",),
        "thought": ("think",),
        "told": ("tell",),
        "became": ("become",),
        "shown": ("show",),
        "felt": ("feel",),
        "brought": ("bring",),
        "began": ("begin",),
        "begun": ("begin",),
        "kept": ("keep",),
        "held": ("hold",),
        "wrote": ("write",),
        "written": ("write",),
        "stood": ("stand",),
        "heard": ("hear",),
        "meant": ("mean",),
        "met": ("meet",),
        "ran": ("run",),
        "paid": ("pay",),
        "sat": ("sit",),
        "spoken": ("speak",),
        "led": ("lead",),
        "grew": ("grow",),
        "grown": ("grow",),
        "lost": ("lose",),
        "fallen": ("fall",),
        "sent": ("send",),
        "built": ("build",),
        "understood": ("understand",),
        "drew": ("draw",),
        "drawn": ("draw",),
        "broke": ("break",),
        "broken": ("break",),
        "spent": ("spend",),
        "risen": ("rise",),
        "drove": ("drive",),
        "driven": ("drive",),
        "bought": ("buy",),
        "wore": ("wear",),
        "worn": ("wear",),
        "chose": ("choose",),
        "chosen": ("choose",),
        "swam": ("swim",),
        "swum": ("swim",),
        "drank": ("drink",),
        "drunk": ("drink",),
        "ate": ("eat",),
        "eaten": ("eat",),
        "forgot": ("forget",),
        "forgotten": ("forget",),
        "flew": ("fly",),
        "flown": ("fly",),
        "threw": ("throw",),
        "thrown": ("throw",),
        "won": ("win",),
        "taught": ("teach",),
        "caught": ("catch",),
        "slept": ("sleep",),
        "sold": ("sell",),
        "said": ("say",),
        # Irregular nominal inflection.
        "children": ("child",),
        "feet": ("foot",),
        "geese": ("goose",),
        "men": ("man",),
        "mice": ("mouse",),
        "teeth": ("tooth",),
        "women": ("woman",),
        "analyses": ("analysis",),
        "crises": ("crisis",),
        "criteria": ("criterion",),
        "phenomena": ("phenomenon",),
        "theses": ("thesis",),
        # Suppletive comparison.
        "best": ("good",),
        "better": ("good",),
        "worse": ("bad",),
        "worst": ("bad",),
        # Genuine lexical ambiguity: never choose without POS/context.
        "axes": ("ax", "axe", "axis"),
        "bore": ("bear", "bore"),
        "bound": ("bind", "bound"),
        "fell": ("fall", "fell"),
        "found": ("find", "found"),
        "lay": ("lay", "lie"),
        "left": ("leave", "left"),
        "leaves": ("leaf", "leave"),
        "people": ("people", "person"),
        "rose": ("rise", "rose"),
        "saw": ("saw", "see"),
        "spoke": ("speak", "spoke"),
        "wound": ("wind", "wound"),
    }
)

_APOSTROPHE_S_CONTRACTION_BASES = frozenset(
    {
        "he",
        "here",
        "how",
        "it",
        "she",
        "that",
        "there",
        "this",
        "what",
        "when",
        "where",
        "who",
        "why",
    }
)

_SINGULAR_S_EXCEPTIONS = frozenset(
    {
        "as",
        "bias",
        "bus",
        "canvas",
        "chaos",
        "cosmos",
        "gas",
        "lens",
        "news",
        "offs",
        "physics",
        "politics",
        "series",
        "species",
        "status",
        "this",
        "thus",
    }
)


def _canonical(value: str) -> str:
    """Return the same lowercase spelling convention as the tokenizer."""

    return str(value).strip().lower().replace("\u2019", "'").replace("\u02bc", "'")


def _content_digest(form_to_heads: Mapping[str, Sequence[str]], heads: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for form in sorted(form_to_heads):
        digest.update(form.encode("utf-8"))
        digest.update(b"\0")
        for head in sorted(set(form_to_heads[form])):
            digest.update(head.encode("utf-8"))
            digest.update(b"\0")
        digest.update(b"\n")
    digest.update(b"--heads--\n")
    for head in sorted(set(heads)):
        digest.update(head.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class FlemmaLexicon:
    """Immutable form-to-head evidence used by :class:`OpenFlemmaLemmatizer`."""

    form_to_heads: Mapping[str, tuple[str, ...]]
    heads: frozenset[str]
    source_label: str = "operator-supplied"
    source_sha256: str | None = None

    def __post_init__(self) -> None:
        normalized: dict[str, tuple[str, ...]] = {}
        all_heads = {_canonical(head) for head in self.heads if _canonical(head)}
        for raw_form, raw_heads in self.form_to_heads.items():
            form = _canonical(raw_form)
            heads = tuple(sorted({_canonical(head) for head in raw_heads if _canonical(head)}))
            if not form or not heads:
                continue
            normalized[form] = heads
            all_heads.update(heads)
        object.__setattr__(self, "form_to_heads", MappingProxyType(normalized))
        object.__setattr__(self, "heads", frozenset(all_heads))

    @property
    def content_sha256(self) -> str:
        """Fingerprint normalized content, independent of source file ordering."""

        return _content_digest(self.form_to_heads, self.heads)

    @property
    def ambiguous_forms(self) -> frozenset[str]:
        return frozenset(
            form for form, heads in self.form_to_heads.items() if len(heads) > 1
        )


def flemma_lexicon_from_rows(
    rows: Iterable[Sequence[str]],
    *,
    source_label: str = "operator-supplied",
    source_sha256: str | None = None,
) -> FlemmaLexicon:
    """Build a lexicon from rows of ``head, form1, form2, ...``.

    Duplicate surface forms are retained as multiple candidates instead of
    resolving by file order.  This is essential for deterministic treatment of
    homographs such as ``found`` and ``left``.
    """

    form_sets: dict[str, set[str]] = {}
    heads: set[str] = set()
    for row in rows:
        if not row:
            continue
        head = _canonical(row[0])
        if not head or head.startswith("##"):
            continue
        heads.add(head)
        values = tuple(row) if len(row) > 1 else (head,)
        for raw_form in values:
            form = _canonical(raw_form)
            if form:
                form_sets.setdefault(form, set()).add(head)
        form_sets.setdefault(head, set()).add(head)
    return FlemmaLexicon(
        form_to_heads={form: tuple(sorted(values)) for form, values in form_sets.items()},
        heads=frozenset(heads),
        source_label=source_label,
        source_sha256=source_sha256,
    )


def load_ngsl_flemma_lexicon(path: str | Path) -> FlemmaLexicon:
    """Load the rights-reviewed NGSL research lemmatization CSV.

    ``path`` may name the CSV itself or its containing directory.  The original
    file SHA-256 and a normalized-content SHA-256 are both retained so runtime
    metadata can identify exactly which evidence produced a result.
    """

    source = Path(path)
    if source.is_dir():
        source = source / "NGSL_1.2_lemmatized_for_research.csv"
    raw = source.read_bytes()
    source_sha256 = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8-sig")
    return flemma_lexicon_from_rows(
        csv.reader(text.splitlines()),
        source_label="NGSL research lemmatization",
        source_sha256=source_sha256,
    )


def _add(candidates: list[str], value: str) -> None:
    value = _canonical(value)
    if len(value) >= 2 and value not in candidates:
        candidates.append(value)


def inflection_candidates(token: str) -> tuple[str, ...]:
    """Generate deterministic orthographic candidates without choosing one.

    The caller must validate candidates against known lemma heads.  No rule here
    is treated as sufficient evidence on its own.
    """

    word = _canonical(token)
    candidates: list[str] = []
    if len(word) < 3 or "'" in word:
        return ()

    # Nominal plurals and third-person singular verbs.
    if word.endswith("ies") and len(word) > 4:
        _add(candidates, word[:-3] + "y")
        _add(candidates, word[:-1])  # ties -> tie, movies -> movie
    if word.endswith("ves") and len(word) > 4:
        _add(candidates, word[:-3] + "f")
        _add(candidates, word[:-3] + "fe")
        _add(candidates, word[:-1])  # leaves -> leave
    if word.endswith("es") and len(word) > 3:
        _add(candidates, word[:-2])
        _add(candidates, word[:-1])
    if (
        word.endswith("s")
        and len(word) > 3
        and not word.endswith(("ss", "us", "is"))
        and word not in _SINGULAR_S_EXCEPTIONS
    ):
        _add(candidates, word[:-1])

    # Past tense and past participle.
    if word.endswith("ied") and len(word) > 4:
        _add(candidates, word[:-3] + "y")
    if word.endswith("ed") and len(word) > 4:
        stem = word[:-2]
        _add(candidates, stem)
        _add(candidates, word[:-1])  # loved -> love
        if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            _add(candidates, stem[:-1])  # stopped -> stop

    # Present participle and gerund.
    if word.endswith("ying") and len(word) > 5:
        _add(candidates, word[:-4] + "ie")  # lying -> lie
    if word.endswith("ing") and len(word) > 5:
        stem = word[:-3]
        _add(candidates, stem)
        _add(candidates, stem + "e")  # making -> make
        if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            _add(candidates, stem[:-1])  # running -> run

    # Comparison.  Plain -er/-est removal is intentionally omitted because it
    # confuses agent nouns (teacher, writer) with comparatives.  The productive
    # spelling-change patterns below are much less ambiguous.
    if word.endswith("ier") and len(word) > 4:
        _add(candidates, word[:-3] + "y")
    if word.endswith("iest") and len(word) > 5:
        _add(candidates, word[:-4] + "y")
    if word.endswith("er") and len(word) > 4:
        stem = word[:-2]
        if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            _add(candidates, stem[:-1])  # bigger -> big
        _add(candidates, stem + "e")  # nicer -> nice (validated by head set)
    if word.endswith("est") and len(word) > 5:
        stem = word[:-3]
        if len(stem) >= 4 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            _add(candidates, stem[:-1])  # biggest -> big
        _add(candidates, stem + "e")  # nicest -> nice

    return tuple(candidates)


class OpenFlemmaLemmatizer:
    """High-precision, POS-independent flemma normalizer.

    ``extra_heads`` should contain head spellings from the selected ranked list
    and, when semantic lookup is enabled, from the open semantic lexicon.  Rule
    candidates are accepted only when exactly one known head remains.
    """

    name = "open_flemma"

    def __init__(
        self,
        lexicon: FlemmaLexicon | None = None,
        *,
        extra_heads: Iterable[str] = (),
    ) -> None:
        self._lexicon = lexicon
        self._form_to_heads = lexicon.form_to_heads if lexicon else MappingProxyType({})
        self._lexicon_heads = frozenset(lexicon.heads if lexicon else ())
        self._extra_heads = frozenset(
            _canonical(head) for head in extra_heads if _canonical(head)
        )
        heads = set(self._lexicon_heads)
        heads.update(self._extra_heads)
        self._heads = frozenset(heads)
        behavior_digest = _content_digest(self._form_to_heads, self._heads)
        self.version = f"{ALGORITHM_VERSION}+{behavior_digest[:12]}"

    @property
    def known_head_count(self) -> int:
        return len(self._heads)

    @property
    def lexicon_form_count(self) -> int:
        return len(self._form_to_heads)

    def normalize(self, token: str) -> str:
        word = _canonical(token)
        if not word:
            return word

        direct = set(self._form_to_heads.get(word, ()))
        irregular = set(_IRREGULAR_HEADS.get(word, ()))
        # Code-reviewed irregular evidence has the highest priority.  A unique
        # entry is adopted (won -> win; better -> good); a multi-head entry is
        # explicitly ambiguous and therefore retained (saw; found; lay).
        if irregular:
            return next(iter(irregular)) if len(irregular) == 1 else word

        # The rights-reviewed form table is stronger evidence than a generic
        # suffix rule. Its recorded collisions remain unresolved; a unique
        # mapping is used directly. Additional common ambiguities absent from
        # that table belong in the explicit irregular table above.
        if direct:
            return next(iter(direct)) if len(direct) == 1 else word

        possessive: set[str] = set()

        # Possessives are considered only when they cannot be a common 's
        # contraction.  Plural possessives recurse once through the normalizer.
        if word.endswith("s'") and len(word) > 3:
            possessed = self.normalize(word[:-1])
            if possessed != word[:-1] or possessed in self._heads:
                possessive.add(possessed)
        elif word.endswith("'s") and len(word) > 3:
            base = word[:-2]
            if base not in _APOSTROPHE_S_CONTRACTION_BASES and base in self._heads:
                possessive.add(base)

        # A spelling that is itself a known open-lexicon head is not
        # reinterpreted by suffix alone (worker, species, meeting, ...).
        if word in self._heads:
            return word
        candidates = possessive | {
            candidate
            for candidate in inflection_candidates(word)
            if candidate in self._heads
        }

        # Choosing between multiple candidates would amount to POS or sense
        # disambiguation that this normalizer explicitly does not perform.
        if len(candidates) == 1:
            return next(iter(candidates))
        return word


def build_open_flemma(
    ngsl_path: str | Path,
    *,
    extra_heads: Iterable[str] = (),
) -> OpenFlemmaLemmatizer:
    """Convenience factory for the project's redistributable NGSL resource."""

    return OpenFlemmaLemmatizer(
        load_ngsl_flemma_lexicon(ngsl_path),
        extra_heads=extra_heads,
    )


__all__ = [
    "ALGORITHM_VERSION",
    "FlemmaLexicon",
    "OpenFlemmaLemmatizer",
    "build_open_flemma",
    "flemma_lexicon_from_rows",
    "inflection_candidates",
    "load_ngsl_flemma_lexicon",
]
