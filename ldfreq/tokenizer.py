"""Tokenization for English text.

Kept deliberately simple and transparent: a word is a run of ASCII letters with
optional internal apostrophes (don't, it's). The *unit* (word form / lemma /
flemma) is applied downstream by a Lemmatizer, not here.
"""
import re

_TOKEN_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)?")


def tokenize(text: str, lower: bool = True):
    toks = _TOKEN_RE.findall(text or "")
    return [t.lower() for t in toks] if lower else toks
