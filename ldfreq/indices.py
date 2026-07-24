"""Panel A: list-independent lexical diversity indices.

All measures operate on a list of pre-processed tokens (already lower-cased and,
if requested, lemmatized/flemmatized). Implemented from first principles (no
heavy dependency) so the app stays light enough to deploy anywhere.

Direction note:
  - Most indices: higher = more diverse.
  - Maas (a^2) and Yule's K: *lower* = more diverse (they measure repetition).

Each index has a recommended minimum-token requirement. The UI reports values for
short texts too, but marks values below the recommended floor as unstable.
"""
from __future__ import annotations

import math
import random
from collections import Counter

import numpy as np

NAN = float("nan")

# Minimum token count for each index to be meaningful (see Kyle et al. 2024;
# Kojima & Yamashita 2014). Used by the Tier-degradation logic.
MIN_TOKENS = {
    "ttr": 1, "rttr": 1, "cttr": 1, "herdan": 2, "maas": 2,
    "msttr": 50, "mattr": 50, "mtld": 50, "hdd": 42, "vocd": 50,
    "yule_k": 100, "yule_i": 100,
}

# Direction of each index: +1 → higher means more diverse, -1 → lower means more diverse.
DIRECTION = {
    "ttr": +1, "rttr": +1, "cttr": +1, "herdan": +1, "maas": -1,
    "msttr": +1, "mattr": +1, "mtld": +1, "hdd": +1, "vocd": +1,
    "yule_k": -1, "yule_i": +1,
}


def _basic(tokens):
    n = len(tokens)
    freq = Counter(tokens)
    return n, len(freq), freq


# --- raw ratios -------------------------------------------------------------
def ttr(tokens):
    n, v, _ = _basic(tokens)
    return v / n if n else NAN


def rttr(tokens):  # Guiraud / Root TTR
    n, v, _ = _basic(tokens)
    return v / math.sqrt(n) if n else NAN


def cttr(tokens):  # Corrected TTR
    n, v, _ = _basic(tokens)
    return v / math.sqrt(2 * n) if n else NAN


def herdan(tokens):  # Herdan's C / Log-TTR
    n, v, _ = _basic(tokens)
    return math.log(v) / math.log(n) if (n > 1 and v > 0) else NAN


def maas(tokens):  # Maas a^2 (lower = more diverse)
    n, v, _ = _basic(tokens)
    return (math.log(n) - math.log(v)) / (math.log(n) ** 2) if (n > 1 and v > 0) else NAN


# --- segment / window based -------------------------------------------------
def msttr(tokens, segment=50):
    n = len(tokens)
    if n < segment:
        return NAN
    ratios = []
    for i in range(0, n - segment + 1, segment):
        seg = tokens[i:i + segment]
        ratios.append(len(set(seg)) / segment)
    return sum(ratios) / len(ratios) if ratios else NAN


def mattr(tokens, window=50):
    n = len(tokens)
    if n < window:
        return NAN
    ratios = [len(set(tokens[i:i + window])) / window for i in range(n - window + 1)]
    return sum(ratios) / len(ratios)


# --- MTLD (bidirectional, McCarthy & Jarvis 2010) ---------------------------
def _mtld_pass(tokens, threshold, min_factor_len=10):
    # Canonical (McCarthy & Jarvis 2010; Kyle's lexical_diversity): a factor closes
    # when running TTR <= threshold AND the factor is at least `min_factor_len` tokens.
    factors = 0.0
    types = set()
    count = 0
    for tok in tokens:
        count += 1
        types.add(tok)
        cur_ttr = len(types) / count
        if cur_ttr <= threshold and count >= min_factor_len:
            factors += 1
            types = set()
            count = 0
    if count > 0:  # partial factor for the trailing run
        cur_ttr = len(types) / count
        denom = (1.0 - threshold)
        factors += (1.0 - cur_ttr) / denom if denom else 0.0
    return len(tokens) / factors if factors > 0 else NAN


def mtld(tokens, threshold=0.72, min_factor_len=10):
    if len(tokens) < 1:
        return NAN
    fwd = _mtld_pass(tokens, threshold, min_factor_len)
    bwd = _mtld_pass(list(reversed(tokens)), threshold, min_factor_len)
    vals = [v for v in (fwd, bwd) if v == v]  # drop NaN
    return sum(vals) / len(vals) if vals else NAN


# --- HD-D (McCarthy & Jarvis 2007, hypergeometric) --------------------------
def hdd(tokens, sample=42):
    n = len(tokens)
    if n < sample:
        return None
    freq = Counter(tokens)
    denom = math.comb(n, sample)
    total = 0.0
    for k in freq.values():
        p0 = math.comb(n - k, sample) / denom if (n - k) >= sample else 0.0
        total += (1.0 - p0) / sample
    return min(1.0, total)  # clamp float accumulation spill past the [0,1] range


# --- vocd-D (random sampling + curve fit; reproducible via seed) ------------
def _vocd_model(n, d):
    return (d / n) * (np.sqrt(1.0 + 2.0 * n / d) - 1.0)


def _vocd_single(tokens, seed, trials, lo, hi, grid_max):
    """One vocd sample-and-fit pass; returns D or None (non-converged)."""
    rng = random.Random(seed)
    sizes = list(range(lo, hi + 1))
    means = []
    for s in sizes:
        ttrs = [len(set(rng.sample(tokens, s))) / s for _ in range(trials)]
        means.append(sum(ttrs) / len(ttrs))
    sizes_a = np.asarray(sizes, dtype=float)
    means_a = np.asarray(means, dtype=float)

    def sse(d):
        return float(np.sum((_vocd_model(sizes_a, d) - means_a) ** 2))

    grid = np.linspace(1.0, grid_max, 400)            # coarse grid…
    best_d = float(min(grid, key=sse))
    if best_d >= grid_max - 0.5:                       # pinned to ceiling → not converged
        return None
    lo_b, hi_b = max(1.0, best_d - 1.0), min(grid_max, best_d + 1.0)
    fine = np.linspace(lo_b, hi_b, 200)                # …then local refine
    return float(min([best_d, *fine], key=sse))


def vocd(tokens, seed=42, trials=100, lo=35, hi=50, grid_max=200.0, runs=3):
    """vocd-D, averaged over ``runs`` independent samplings (CLAN/VOCD default = 3).

    Each run fits the curve TTR(n) = (D/n)(sqrt(1+2n/D)-1) to mean TTR over sample
    sizes 35-50. Returns ``None`` if the text is too short or no run converges
    (near-maximal-diversity texts whose TTR stays ~1; prefer HD-D there).
    """
    n = len(tokens)
    if n < hi:
        return None
    ds = [d for r in range(runs)
          if (d := _vocd_single(tokens, seed + r, trials, lo, hi, grid_max)) is not None]
    return sum(ds) / len(ds) if ds else None


# --- Yule's K and I ---------------------------------------------------------
def _spectrum_moments(tokens):
    n = len(tokens)
    freq = Counter(tokens)
    v = len(freq)
    spectrum = Counter(freq.values())  # m -> #types occurring m times
    m1 = n                                            # = sum(m * V_m)
    m2 = sum((m * m) * vm for m, vm in spectrum.items())  # = sum(m^2 * V_m)
    return m1, m2, v


def yule_k(tokens):
    if not tokens:
        return NAN
    m1, m2, _ = _spectrum_moments(tokens)
    return 1e4 * (m2 - m1) / (m1 * m1) if m1 else NAN


def yule_i(tokens):
    # Yule's I = V^2 / (M2 - V), V = #types, M2 = sum(m^2 * V_m). Higher = more diverse.
    if not tokens:
        return NAN
    _, m2, v = _spectrum_moments(tokens)
    denom = (m2 - v)
    return (v * v) / denom if denom != 0 else NAN


_FUNCS = {
    "ttr": ttr, "rttr": rttr, "cttr": cttr, "herdan": herdan, "maas": maas,
    "msttr": msttr, "mattr": mattr, "mtld": mtld, "hdd": hdd, "vocd": vocd,
    "yule_k": yule_k, "yule_i": yule_i,
}

PRETTY = {
    "ttr": "TTR", "rttr": "RTTR (Guiraud)", "cttr": "CTTR", "herdan": "Herdan C",
    "maas": "Maas a² (↓)", "msttr": "MSTTR", "mattr": "MATTR", "mtld": "MTLD",
    "hdd": "HD-D", "vocd": "vocd-D", "yule_k": "Yule's K (↓)", "yule_i": "Yule's I",
}


def effective_min_tokens(key, *, segment=50, window=50, hdd_sample=42,
                         vocd_hi=50, mtld_threshold=0.72,
                         min_tokens_override=None):
    """Return the token floor actually used for an index call."""
    del mtld_threshold  # threshold affects values, not the minimum token count.
    floor = MIN_TOKENS[key]
    if key == "msttr":
        floor = max(floor, int(segment))
    elif key == "mattr":
        floor = max(floor, int(window))
    elif key == "hdd":
        floor = max(floor, int(hdd_sample))
    elif key == "vocd":
        floor = max(floor, int(vocd_hi))

    if min_tokens_override is not None and floor > 1:
        floor = max(floor, int(min_tokens_override))
    return floor


def all_indices(tokens, *, segment=50, window=50, mtld_threshold=0.72,
                hdd_sample=42, vocd_seed=42, min_tokens_override=None,
                compute_below_floor=True):
    """Return ``{key: value_or_None}`` for every Panel-A index.

    Short texts are still computed where the formula allows it. For indices with
    a fixed segment/window/sample size, the runtime size is reduced to ``N`` when
    ``compute_below_floor`` is true. ``None`` means the formula is still
    undefined or did not converge for the text.
    """
    n = len(tokens)
    out = {}
    for key, fn in _FUNCS.items():
        floor = effective_min_tokens(
            key,
            segment=segment,
            window=window,
            hdd_sample=hdd_sample,
            min_tokens_override=min_tokens_override,
        )
        if n < floor and not compute_below_floor:
            out[key] = None
            continue
        if key == "msttr":
            runtime_segment = min(int(segment), n) if n else int(segment)
            out[key] = fn(tokens, segment=runtime_segment)
        elif key == "mattr":
            runtime_window = min(int(window), n) if n else int(window)
            out[key] = fn(tokens, window=runtime_window)
        elif key == "mtld":
            out[key] = fn(tokens, threshold=mtld_threshold)
        elif key == "hdd":
            runtime_sample = min(int(hdd_sample), n) if n else int(hdd_sample)
            out[key] = fn(tokens, sample=runtime_sample)
        elif key == "vocd":
            if compute_below_floor and n < floor and n > 0:
                runtime_hi = min(50, n)
                runtime_lo = min(35, runtime_hi)
                out[key] = fn(tokens, seed=vocd_seed, lo=runtime_lo, hi=runtime_hi)
            else:
                out[key] = fn(tokens, seed=vocd_seed)
        else:
            out[key] = fn(tokens)
    return out
