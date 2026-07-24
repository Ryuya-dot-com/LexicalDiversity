"""Pluggable normalization back-ends (the "lemmatizer" selector).

A lemmatizer maps a surface token to a head form. Because the units we support
for the frequency lists are POS-agnostic (flemma / word form), ``normalize``
returns a single head string regardless of part of speech.

Back-ends
---------
* OpenFlemmaLemmatizer — project-owned deterministic rules plus cleared open
  lexical resources (POS-agnostic flemma). Public default.
* WordFormLemmatizer — no change beyond lower-casing (unit = word form).
* SimplemmaLemmatizer — optional comparison back-end.
* AntBNCLemmatizer   — static form→head lookup. Use for *approximate* parity
  with Mizumoto's New Word Level Checker (NWLC). NOTE: NWLC uses an AntBNC list
  *manually matched to JACET headwords*; the raw AntBNC list gets you close but
  not bit-identical — label results "NWLC近似 (AntBNC)", never "identical".

Every back-end exposes ``name`` and ``version`` so the analysis metadata can
record exactly what produced the numbers (reproducibility).
"""
from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Iterable

from .open_flemma import OpenFlemmaLemmatizer, load_ngsl_flemma_lexicon


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NGSL_FLEMMA_PATH = (
    PROJECT_ROOT / "data" / "ngsl" / "NGSL_1.2_lemmatized_for_research.csv"
)
NGSL_1_2_FLEMMA_SHA256 = (
    "d814f2a0a3c61479a2c5ad037661719a0cc6e7dbcde31f181b54f12d0f1e11a4"
)


class WordFormLemmatizer:
    name = "word_form"

    def __init__(self):
        self.version = "-"

    def normalize(self, token: str) -> str:
        return token.lower()


SurfaceLemmatizer = WordFormLemmatizer


class SimplemmaLemmatizer:
    name = "simplemma"

    def __init__(self, lang: str = "en"):
        import simplemma  # imported lazily so the package is optional
        self._s = simplemma
        self._lang = lang
        self.version = getattr(simplemma, "__version__", "?")

    def normalize(self, token: str) -> str:
        t = token.lower()
        try:
            return self._s.lemmatize(t, lang=self._lang)
        except Exception:
            return t


class AntBNCLemmatizer:
    """Static form→head map (Laurence Anthony's AntBNC lemma list).

    If no file is configured the back-end degrades gracefully to word form,
    so the app still runs; the UI should show that AntBNC is "not loaded".
    """

    name = "antbnc"

    def __init__(self, path: str | None = None, list_version: str = "unknown"):
        self.map: dict[str, str] = {}
        self.version = "not-loaded"
        if path and os.path.exists(path):
            self._load(path)
            self.version = list_version if list_version != "unknown" else os.path.basename(path)

    def _load(self, path: str) -> None:
        # AntConc lemma-list format: "head -> form1, form2, ..." (one per line);
        # also tolerate tab-separated "head\tform1\tform2".
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                if "->" in line:
                    head, rest = line.split("->", 1)
                    forms = re.split(r"[,\s]+", rest.strip())
                else:
                    parts = re.split(r"\t+", line)
                    head, forms = parts[0], parts[1:]
                head = head.strip().lower()
                self.map[head] = head
                for f in forms:
                    f = f.strip().lower()
                    if f:
                        self.map[f] = head

    @property
    def loaded(self) -> bool:
        return bool(self.map)

    def normalize(self, token: str) -> str:
        t = token.lower()
        return self.map.get(t, t)


def _build_project_open_flemma(
    ngsl_path: str | os.PathLike[str] = DEFAULT_NGSL_FLEMMA_PATH,
    *,
    extra_heads: Iterable[str] = (),
) -> OpenFlemmaLemmatizer:
    """Build the public normalizer and fail closed on NGSL source drift."""

    lexicon = load_ngsl_flemma_lexicon(ngsl_path)
    if lexicon.source_sha256 != NGSL_1_2_FLEMMA_SHA256:
        raise ValueError("NGSL flemma resource SHA-256 mismatch")
    return OpenFlemmaLemmatizer(lexicon, extra_heads=extra_heads)


def build(
    name: str | None,
    *,
    antbnc_path: str | None = None,
    ngsl_path: str | os.PathLike[str] = DEFAULT_NGSL_FLEMMA_PATH,
    extra_heads: Iterable[str] = (),
):
    name = (name or "open_flemma").lower()
    if name in {"open_flemma", "open-flemma", "project_flemma"}:
        return _build_project_open_flemma(ngsl_path, extra_heads=extra_heads)
    if name in {"surface", "surface_form", "word_form"}:
        return WordFormLemmatizer()
    if name == "antbnc":
        return AntBNCLemmatizer(antbnc_path)
    if name == "simplemma":
        return SimplemmaLemmatizer()
    raise ValueError(f"Unknown lemmatizer: {name}")


__all__ = [
    "AntBNCLemmatizer",
    "DEFAULT_NGSL_FLEMMA_PATH",
    "NGSL_1_2_FLEMMA_SHA256",
    "OpenFlemmaLemmatizer",
    "SimplemmaLemmatizer",
    "SurfaceLemmatizer",
    "WordFormLemmatizer",
    "build",
]
