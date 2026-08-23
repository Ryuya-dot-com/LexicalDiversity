"""Panel A: list-independent lexical diversity indices.

All measures operate on a materialized sequence of pre-tokenized strings
(already lower-cased and, if requested, lemmatized/flemmatized). Each token
must be non-empty and UTF-8 encodable. Whitespace inside one element is not
split; callers should use the project tokenizer before calling this module.
Implemented from first principles (no heavy dependency) so the app stays light
enough to deploy anywhere.

Direction note:
  - Most indices: higher = more diverse.
  - Maas (a^2) and Yule's K: *lower* = more diverse (they measure repetition).

Each index has a project advisory minimum-token floor.  That floor is reported
separately from the computational domain: it never changes a requested method
parameter and never, by itself, turns a computable value into a missing value.
"""
from __future__ import annotations

import math
import random
from collections import Counter
from collections.abc import Sequence

import numpy as np

NAN = float("nan")

# Project advisory minimum-token floors (see Kyle et al. 2024;
# Kojima & Yamashita 2014).  These are quality-screening metadata, not formula
# domains and not permission to alter a method parameter.
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

# Stable method identities for the standard Panel-A columns.  In particular,
# the Python MTLD implementation closes a factor at TTR <= threshold.  Its ID is
# intentionally Python-specific: it is not a numerical-equivalence claim about
# implementations that use a strict ``<`` boundary.
METHOD_IDS = {
    "ttr": "ttr_v_over_n_v1",
    "rttr": "rttr_guiraud_v_over_sqrt_n_v1",
    "cttr": "cttr_v_over_sqrt_2n_v1",
    "herdan": "herdan_c_logv_over_logn_v1",
    "maas": "maas_a2_ln_v1",
    "msttr": "msttr_nonoverlap_complete_drop_v1",
    "mattr": "mattr_sliding_step1_v1",
    "mtld": "mtld_bidir_mean_leq_min10_python_v1",
    "hdd": "hdd_expected_ttr_scaled_v1",
    "vocd": "vocd_d_monte_carlo_python_v1",
    "yule_k": "yule_k_m2_tokens_v1",
    "yule_i": "yule_i_types_v2_over_m2_minus_v_v1",
}

ADAPTIVE_METHOD_IDS = {
    "msttr_adaptive": "msttr_adaptive_segment_to_n_python_v0",
    "mattr_adaptive": "mattr_adaptive_window_to_n_python_v0",
    "hdd_adaptive": "hdd_adaptive_sample_to_n_python_v0",
    "vocd_adaptive": "vocd_adaptive_range_to_n_python_v0",
}


def _integer_parameter(name, value, *, minimum=1):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "positive" if minimum == 1 else f">= {minimum}"
        raise ValueError(f"{name} must be a {qualifier} integer")
    return value


def _finite_number_parameter(name, value, *, lower, upper=None):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or not float(value) > lower
        or (upper is not None and not float(value) < upper)
    ):
        interval = f"greater than {lower}"
        if upper is not None:
            interval += f" and less than {upper}"
        raise ValueError(f"{name} must be a finite number {interval}")
    return float(value)


def _validate_panel_a_parameters(
    *,
    segment=50,
    window=50,
    mtld_threshold=0.72,
    mtld_min_factor_len=10,
    hdd_sample=42,
    vocd_seed=42,
    vocd_trials=100,
    vocd_lo=35,
    vocd_hi=50,
    vocd_grid_max=200.0,
    vocd_runs=3,
    min_tokens_override=None,
):
    """Validate every public Panel-A parameter before any computation."""
    validated = {
        "segment": _integer_parameter("segment", segment),
        "window": _integer_parameter("window", window),
        "mtld_threshold": _finite_number_parameter(
            "mtld_threshold", mtld_threshold, lower=0.0, upper=1.0
        ),
        "mtld_min_factor_len": _integer_parameter(
            "mtld_min_factor_len", mtld_min_factor_len
        ),
        "hdd_sample": _integer_parameter("hdd_sample", hdd_sample),
        "vocd_seed": _integer_parameter("vocd_seed", vocd_seed, minimum=0),
        "vocd_trials": _integer_parameter("vocd_trials", vocd_trials),
        "vocd_lo": _integer_parameter("vocd_lo", vocd_lo),
        "vocd_hi": _integer_parameter("vocd_hi", vocd_hi),
        "vocd_grid_max": _finite_number_parameter(
            "vocd_grid_max", vocd_grid_max, lower=1.0
        ),
        "vocd_runs": _integer_parameter("vocd_runs", vocd_runs),
    }
    if validated["vocd_lo"] > validated["vocd_hi"]:
        raise ValueError("vocd_lo must be less than or equal to vocd_hi")
    if min_tokens_override is not None:
        validated["min_tokens_override"] = _integer_parameter(
            "min_tokens_override", min_tokens_override
        )
    else:
        validated["min_tokens_override"] = None
    return validated


def _validated_token_sequence(tokens):
    if (
        isinstance(tokens, (str, bytes, bytearray))
        or not isinstance(tokens, Sequence)
    ):
        raise TypeError("tokens must be a materialized sequence of strings")
    materialized = tuple(tokens)
    if any(not isinstance(token, str) for token in materialized):
        raise TypeError("every token must be a string")
    if any(token == "" for token in materialized):
        raise ValueError("tokens must not contain empty strings")
    try:
        for token in materialized:
            token.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError("every token must be valid UTF-8") from None
    return materialized


def _basic(tokens):
    tokens = _validated_token_sequence(tokens)
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
    segment = _integer_parameter("segment", segment)
    tokens = _validated_token_sequence(tokens)
    n = len(tokens)
    if n < segment:
        return NAN
    ratios = []
    for i in range(0, n - segment + 1, segment):
        seg = tokens[i:i + segment]
        ratios.append(len(set(seg)) / segment)
    return sum(ratios) / len(ratios) if ratios else NAN


def mattr(tokens, window=50):
    window = _integer_parameter("window", window)
    tokens = _validated_token_sequence(tokens)
    n = len(tokens)
    if n < window:
        return NAN
    ratios = [len(set(tokens[i:i + window])) / window for i in range(n - window + 1)]
    return sum(ratios) / len(ratios)


# --- MTLD (bidirectional, McCarthy & Jarvis 2010) ---------------------------
def _mtld_pass(tokens, threshold, min_factor_len=10):
    # Explicit Python variant used here: a factor closes when running TTR <=
    # threshold AND the factor is at least `min_factor_len` tokens.  The method
    # ID distinguishes this from strict-< implementations.
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
    threshold = _finite_number_parameter(
        "threshold", threshold, lower=0.0, upper=1.0
    )
    min_factor_len = _integer_parameter("min_factor_len", min_factor_len)
    tokens = _validated_token_sequence(tokens)
    if len(tokens) < min_factor_len:
        return NAN
    fwd = _mtld_pass(tokens, threshold, min_factor_len)
    bwd = _mtld_pass(list(reversed(tokens)), threshold, min_factor_len)
    vals = [v for v in (fwd, bwd) if v == v]  # drop NaN
    return sum(vals) / len(vals) if vals else NAN


# --- HD-D (McCarthy & Jarvis 2007, hypergeometric) --------------------------
def hdd(tokens, sample=42):
    sample = _integer_parameter("sample", sample)
    tokens = _validated_token_sequence(tokens)
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
    validated = _validate_panel_a_parameters(
        vocd_seed=seed,
        vocd_trials=trials,
        vocd_lo=lo,
        vocd_hi=hi,
        vocd_grid_max=grid_max,
        vocd_runs=runs,
    )
    seed = validated["vocd_seed"]
    trials = validated["vocd_trials"]
    lo = validated["vocd_lo"]
    hi = validated["vocd_hi"]
    grid_max = validated["vocd_grid_max"]
    runs = validated["vocd_runs"]
    tokens = _validated_token_sequence(tokens)
    n = len(tokens)
    if n < hi:
        return None
    ds = [d for r in range(runs)
          if (d := _vocd_single(tokens, seed + r, trials, lo, hi, grid_max)) is not None]
    return sum(ds) / len(ds) if ds else None


# --- Yule's K and I ---------------------------------------------------------
def _spectrum_moments(tokens):
    tokens = _validated_token_sequence(tokens)
    n = len(tokens)
    freq = Counter(tokens)
    v = len(freq)
    spectrum = Counter(freq.values())  # m -> #types occurring m times
    m1 = n                                            # = sum(m * V_m)
    m2 = sum((m * m) * vm for m, vm in spectrum.items())  # = sum(m^2 * V_m)
    return m1, m2, v


def yule_k(tokens):
    m1, m2, _ = _spectrum_moments(tokens)
    if not m1:
        return NAN
    return 1e4 * (m2 - m1) / (m1 * m1) if m1 else NAN


def yule_i(tokens):
    # Yule's I = V^2 / (M2 - V), V = #types, M2 = sum(m^2 * V_m). Higher = more diverse.
    _, m2, v = _spectrum_moments(tokens)
    if not v:
        return NAN
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

ADAPTIVE_PRETTY = {
    "msttr_adaptive": "MSTTR (adaptive segment)",
    "mattr_adaptive": "MATTR (adaptive window)",
    "hdd_adaptive": "HD-D (adaptive sample)",
    "vocd_adaptive": "vocd-D (adaptive range)",
}


def effective_min_tokens(key, *, segment=50, window=50, hdd_sample=42,
                         vocd_hi=50, mtld_threshold=0.72,
                         min_tokens_override=None):
    """Return the advisory quality floor for an index call.

    The returned value is display metadata.  It does not suppress a
    formula-domain value or authorize shrinking ``segment``, ``window``,
    ``hdd_sample``, or the vocd sampling range.
    """
    if key not in MIN_TOKENS:
        raise KeyError(key)
    segment = _integer_parameter("segment", segment)
    window = _integer_parameter("window", window)
    hdd_sample = _integer_parameter("hdd_sample", hdd_sample)
    vocd_hi = _integer_parameter("vocd_hi", vocd_hi)
    _finite_number_parameter(
        "mtld_threshold", mtld_threshold, lower=0.0, upper=1.0
    )
    if min_tokens_override is not None:
        min_tokens_override = _integer_parameter(
            "min_tokens_override", min_tokens_override
        )
    floor = MIN_TOKENS[key]
    if key == "msttr":
        floor = max(floor, segment)
    elif key == "mattr":
        floor = max(floor, window)
    elif key == "hdd":
        floor = max(floor, hdd_sample)
    elif key == "vocd":
        floor = max(floor, vocd_hi)

    if min_tokens_override is not None and floor > 1:
        floor = max(floor, min_tokens_override)
    return floor


def computational_min_tokens(key, *, segment=50, window=50, hdd_sample=42,
                             vocd_hi=50, mtld_min_factor_len=10):
    """Return the actual minimum N required by a requested method call."""
    if key not in _FUNCS:
        raise KeyError(key)
    segment = _integer_parameter("segment", segment)
    window = _integer_parameter("window", window)
    hdd_sample = _integer_parameter("hdd_sample", hdd_sample)
    vocd_hi = _integer_parameter("vocd_hi", vocd_hi)
    mtld_min_factor_len = _integer_parameter(
        "mtld_min_factor_len", mtld_min_factor_len
    )
    if key in {"ttr", "rttr", "cttr", "yule_k", "yule_i"}:
        return 1
    if key in {"herdan", "maas"}:
        return 2
    if key == "msttr":
        return segment
    if key == "mattr":
        return window
    if key == "mtld":
        return mtld_min_factor_len
    if key == "hdd":
        return hdd_sample
    if key == "vocd":
        return vocd_hi
    raise KeyError(key)


def _missing(value):
    return value is None or (
        isinstance(value, (float, np.floating)) and math.isnan(float(value))
    )


def _missing_reason(key, value, n, computational_floor):
    if not _missing(value):
        return None
    if n == 0:
        return "empty_input"
    if n < computational_floor:
        if key in {"msttr", "mattr", "hdd", "vocd"}:
            return "too_short_for_requested_parameter"
        return "insufficient_tokens_for_formula"
    if key == "vocd":
        return "no_convergence"
    if key == "yule_i":
        return "zero_denominator"
    if key == "mtld":
        return "no_factor"
    return "undefined_for_text"


def _record(*, key, value, n, requested_parameters, effective_parameters,
            method_id, quality_key=None, segment=50, window=50, hdd_sample=42,
            vocd_hi=50, mtld_min_factor_len=10, min_tokens_override=None):
    quality_key = quality_key or key
    quality_floor = effective_min_tokens(
        quality_key,
        segment=segment,
        window=window,
        hdd_sample=hdd_sample,
        vocd_hi=vocd_hi,
        min_tokens_override=min_tokens_override,
    )
    computational_floor = computational_min_tokens(
        quality_key,
        segment=effective_parameters.get("segment_length", segment),
        window=effective_parameters.get("window_length", window),
        hdd_sample=effective_parameters.get("sample_size", hdd_sample),
        vocd_hi=effective_parameters.get("sample_size_max", vocd_hi),
        mtld_min_factor_len=effective_parameters.get(
            "minimum_factor_length", mtld_min_factor_len
        ),
    )
    reason = _missing_reason(quality_key, value, n, computational_floor)
    return {
        "value": None if _missing(value) else float(value),
        "status": "missing" if reason is not None else "available",
        "missing_reason": reason,
        "method_id": method_id,
        "requested_parameters": dict(requested_parameters),
        "effective_parameters": (
            {} if reason is not None else dict(effective_parameters)
        ),
        "advisory_quality_floor_tokens": quality_floor,
        "advisory_quality_status": (
            "below_advisory_floor" if n < quality_floor else "meets_advisory_floor"
        ),
    }


def requested_parameters(
    *,
    segment=50,
    window=50,
    mtld_threshold=0.72,
    mtld_min_factor_len=10,
    hdd_sample=42,
    vocd_seed=42,
    vocd_trials=100,
    vocd_lo=35,
    vocd_hi=50,
    vocd_grid_max=200.0,
    vocd_runs=3,
):
    """Return the frozen parameter maps for the standard Panel-A methods."""
    validated = _validate_panel_a_parameters(
        segment=segment,
        window=window,
        mtld_threshold=mtld_threshold,
        mtld_min_factor_len=mtld_min_factor_len,
        hdd_sample=hdd_sample,
        vocd_seed=vocd_seed,
        vocd_trials=vocd_trials,
        vocd_lo=vocd_lo,
        vocd_hi=vocd_hi,
        vocd_grid_max=vocd_grid_max,
        vocd_runs=vocd_runs,
    )
    if validated["mtld_min_factor_len"] != 10:
        raise ValueError(
            "requested_parameters fixes mtld_min_factor_len at 10 for its method_id"
        )
    return {
        "ttr": {},
        "rttr": {},
        "cttr": {},
        "herdan": {},
        "maas": {},
        "msttr": {"segment_length": validated["segment"]},
        "mattr": {"window_length": validated["window"]},
        "mtld": {
            "threshold": validated["mtld_threshold"],
            "minimum_factor_length": validated["mtld_min_factor_len"],
            "factor_boundary_comparator": "<=",
            "directions": ["forward", "reverse"],
            "direction_aggregation": "arithmetic_mean_available",
        },
        "hdd": {"sample_size": validated["hdd_sample"]},
        "vocd": {
            "seed": validated["vocd_seed"],
            "trials_per_sample_size": validated["vocd_trials"],
            "sample_size_min": validated["vocd_lo"],
            "sample_size_max": validated["vocd_hi"],
            "grid_max": validated["vocd_grid_max"],
            "runs": validated["vocd_runs"],
        },
        "yule_k": {},
        "yule_i": {},
    }


def all_index_records(
    tokens,
    *,
    segment=50,
    window=50,
    mtld_threshold=0.72,
    mtld_min_factor_len=10,
    hdd_sample=42,
    vocd_seed=42,
    vocd_trials=100,
    vocd_lo=35,
    vocd_hi=50,
    vocd_grid_max=200.0,
    vocd_runs=3,
    min_tokens_override=None,
    include_adaptive=False,
):
    """Return explicit computation records for every Panel-A method.

    Standard method IDs always use the requested parameters.  A short sequence
    therefore produces a missing standard value when a requested segment,
    window, sample, or sampling range cannot be evaluated.  The optional legacy
    adaptive calculations are separate method IDs and keys; they never replace
    a standard value. Missing records use an empty ``effective_parameters`` map;
    only an available value reports parameters as actually applied.
    """
    if type(include_adaptive) is not bool:
        raise TypeError("include_adaptive must be boolean")
    validated = _validate_panel_a_parameters(
        segment=segment,
        window=window,
        mtld_threshold=mtld_threshold,
        mtld_min_factor_len=mtld_min_factor_len,
        hdd_sample=hdd_sample,
        vocd_seed=vocd_seed,
        vocd_trials=vocd_trials,
        vocd_lo=vocd_lo,
        vocd_hi=vocd_hi,
        vocd_grid_max=vocd_grid_max,
        vocd_runs=vocd_runs,
        min_tokens_override=min_tokens_override,
    )
    if validated["mtld_min_factor_len"] != 10:
        raise ValueError(
            "all_index_records fixes mtld_min_factor_len at 10 for its method_id"
        )
    segment = validated["segment"]
    window = validated["window"]
    mtld_threshold = validated["mtld_threshold"]
    mtld_min_factor_len = validated["mtld_min_factor_len"]
    hdd_sample = validated["hdd_sample"]
    vocd_seed = validated["vocd_seed"]
    vocd_trials = validated["vocd_trials"]
    vocd_lo = validated["vocd_lo"]
    vocd_hi = validated["vocd_hi"]
    vocd_grid_max = validated["vocd_grid_max"]
    vocd_runs = validated["vocd_runs"]
    min_tokens_override = validated["min_tokens_override"]
    tokens = _validated_token_sequence(tokens)
    n = len(tokens)
    requested = requested_parameters(
        segment=segment,
        window=window,
        mtld_threshold=mtld_threshold,
        mtld_min_factor_len=mtld_min_factor_len,
        hdd_sample=hdd_sample,
        vocd_seed=vocd_seed,
        vocd_trials=vocd_trials,
        vocd_lo=vocd_lo,
        vocd_hi=vocd_hi,
        vocd_grid_max=vocd_grid_max,
        vocd_runs=vocd_runs,
    )
    values = {
        "ttr": ttr(tokens),
        "rttr": rttr(tokens),
        "cttr": cttr(tokens),
        "herdan": herdan(tokens),
        "maas": maas(tokens),
        "msttr": msttr(tokens, segment=segment),
        "mattr": mattr(tokens, window=window),
        "mtld": mtld(
            tokens,
            threshold=mtld_threshold,
            min_factor_len=mtld_min_factor_len,
        ),
        "hdd": hdd(tokens, sample=hdd_sample),
        "vocd": vocd(
            tokens,
            seed=vocd_seed,
            trials=vocd_trials,
            lo=vocd_lo,
            hi=vocd_hi,
            grid_max=vocd_grid_max,
            runs=vocd_runs,
        ),
        "yule_k": yule_k(tokens),
        "yule_i": yule_i(tokens),
    }
    records = {
        key: _record(
            key=key,
            value=values[key],
            n=n,
            requested_parameters=requested[key],
            effective_parameters=requested[key],
            method_id=METHOD_IDS[key],
            segment=segment,
            window=window,
            hdd_sample=hdd_sample,
            vocd_hi=vocd_hi,
            mtld_min_factor_len=mtld_min_factor_len,
            min_tokens_override=min_tokens_override,
        )
        for key in _FUNCS
    }

    if not include_adaptive:
        return records

    adaptive_segment = min(segment, n) if n else segment
    adaptive_window = min(window, n) if n else window
    adaptive_sample = min(hdd_sample, n) if n else hdd_sample
    adaptive_hi = min(vocd_hi, n) if n else vocd_hi
    adaptive_lo = min(vocd_lo, adaptive_hi)
    adaptive = {
        "msttr_adaptive": (
            msttr(tokens, segment=adaptive_segment),
            {"segment_length": adaptive_segment},
            "msttr",
        ),
        "mattr_adaptive": (
            mattr(tokens, window=adaptive_window),
            {"window_length": adaptive_window},
            "mattr",
        ),
        "hdd_adaptive": (
            hdd(tokens, sample=adaptive_sample),
            {"sample_size": adaptive_sample},
            "hdd",
        ),
        "vocd_adaptive": (
            vocd(
                tokens,
                seed=vocd_seed,
                trials=vocd_trials,
                lo=adaptive_lo,
                hi=adaptive_hi,
                grid_max=vocd_grid_max,
                runs=vocd_runs,
            ),
            {
                **requested["vocd"],
                "sample_size_min": adaptive_lo,
                "sample_size_max": adaptive_hi,
            },
            "vocd",
        ),
    }
    for key, (value, effective, quality_key) in adaptive.items():
        records[key] = _record(
            key=key,
            value=value,
            n=n,
            requested_parameters=requested[quality_key],
            effective_parameters=effective,
            method_id=ADAPTIVE_METHOD_IDS[key],
            quality_key=quality_key,
            segment=segment,
            window=window,
            hdd_sample=hdd_sample,
            vocd_hi=vocd_hi,
            mtld_min_factor_len=mtld_min_factor_len,
            min_tokens_override=min_tokens_override,
        )
    return records


def all_indices(tokens, *, segment=50, window=50, mtld_threshold=0.72,
                hdd_sample=42, vocd_seed=42, min_tokens_override=None,
                compute_below_floor=False):
    """Return the backward-compatible scalar projection of Panel-A records.

    ``compute_below_floor`` now defaults to false.  If explicitly true, legacy
    adaptive computations are retained only under distinct ``*_adaptive`` keys;
    standard keys never silently shrink their requested parameters.  Advisory
    floors do not suppress otherwise computable standard metrics.
    """
    if type(compute_below_floor) is not bool:
        raise TypeError("compute_below_floor must be boolean")
    records = all_index_records(
        tokens,
        segment=segment,
        window=window,
        mtld_threshold=mtld_threshold,
        hdd_sample=hdd_sample,
        vocd_seed=vocd_seed,
        min_tokens_override=min_tokens_override,
        include_adaptive=compute_below_floor,
    )
    return {key: record["value"] for key, record in records.items()}
