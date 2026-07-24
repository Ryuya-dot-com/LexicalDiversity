"""Lexical Diversity & Frequency-Profile Analyzer — Streamlit v1.

Run:  python3 -m streamlit run app.py

Code: MIT. Bundled word-list and lemmatizer data is governed separately by
the manifests under data/.
"""
from __future__ import annotations

import json
import hashlib
import math
import os
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

st.set_page_config(page_title="Lexical Diversity Analyzer", layout="wide")


def _copy_streamlit_secrets_to_env():
    """Expose Streamlit secrets as env vars before the word-list registry loads."""
    names = (
        "LDFREQ_NJ8_CSV_B64",
        "LDFREQ_ANTBNC_TXT_B64",
        "LDFREQ_BNCCOCA_ZIP_B64",
        "LDFREQ_NJ8_PATH",
        "LDFREQ_ANTBNC_PATH",
        "LDFREQ_BNCCOCA_PATH",
        "LDFREQ_BNCCOCA_FAMILIES_PATH",
        "LDFREQ_RANGE_PATH",
        "LDFREQ_RANGE_ZIP_B64",
        "LDFREQ_NGSL_PATH",
        "LDFREQ_NATION_BNCCOCA_INDEX_PATH",
        "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
        "LDFREQ_NATION_BNCCOCA_RUNTIME_ZIP_B64",
        "LDFREQ_SERVER_ONLY_RESOURCE_IDS",
        "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED",
        "LDFREQ_SERVING_MODE",
        "LDFREQ_ANALYSIS_DEADLINE_SECONDS",
        "LDFREQ_REAL_WRITING_APPROVED",
    )
    try:
        sources = [st.secrets]
        if "ldfreq" in st.secrets:
            sources.append(st.secrets["ldfreq"])
    except Exception:
        return

    for source in sources:
        for name in names:
            try:
                value = source.get(name)
            except Exception:
                value = None
            if value and name not in os.environ:
                os.environ[name] = str(value)


_copy_streamlit_secrets_to_env()

from ldfreq import __version__
from ldfreq import analysis as ANALYSIS
from ldfreq import indices as IDX
from ldfreq import frequency as FRQ
from ldfreq import isolated as ISOLATED
from ldfreq import privacy as PRIVACY
from ldfreq import query_guard as QUERY_GUARD
from ldfreq.exporting import (
    clean_deep,
    descriptive_rows,
    payload_to_excel,
    payload_to_json,
    summary_rows,
)
from ldfreq.uploads import documents_from_uploads
from ldfreq import wordlists as WL

# Data lists live server-side; the location is deployment config (env-overridable),
# not an end-user setting.
PROJECT_ROOT = Path(__file__).resolve().parent
ANTBNC_URL = "https://www.laurenceanthony.net/software/antconc/"
UNIT_TOKEN = "token"
MAX_FILE_MB_DEFAULT = 2
MAX_FILE_MB_CEILING = 5
MAX_TOTAL_MB_DEFAULT = 10
MAX_TOTAL_MB_CEILING = 20
MAX_DOCUMENTS_DEFAULT = 100
MAX_DOCUMENTS_CEILING = 200
RESULT_TTL_SECONDS = 30 * 60
ANALYSIS_DEADLINE_SECONDS_DEFAULT = 120.0
SERVER_ONLY_QUERY_GUARD_KEY = "_server_only_query_guard"
SERVER_ONLY_QUERY_GUARD_CONFIG = QUERY_GUARD.QueryGuardConfig(
    capacity=MAX_DOCUMENTS_CEILING,
)
SERVING_MODE = os.environ.get("LDFREQ_SERVING_MODE", "public").strip().lower()
ALLOW_LOCAL_RESTRICTED = (
    SERVING_MODE == "local"
    and os.environ.get("LDFREQ_ALLOW_LOCAL_RESTRICTED") == "1"
)
SERVER_ONLY_RESOURCE_IDS = WL.server_only_resource_ids()
POSIX_ISOLATION_AVAILABLE = os.name == "posix"
TOKENIZER_POLICY = "ASCII letters plus internal apostrophes; numbers, hyphens, and periods split or drop."
SAMPLE = (
    "The study of lexical diversity examines how varied the vocabulary in a text is. "
    "Researchers have proposed many measures, but most simple measures depend heavily on "
    "text length. For this reason scholars developed indices such as MTLD and MATTR that "
    "are more robust to length. A text that uses many different words shows higher "
    "diversity, while a text that repeats the same words again and again shows lower "
    "diversity. This contrast describes the sample's word-use pattern; by itself it does "
    "not establish writing quality, vocabulary knowledge, or writer proficiency."
)


def _analysis_deadline_seconds() -> float:
    """Read a bounded operator setting without surfacing its raw value."""

    try:
        value = float(
            os.environ.get(
                "LDFREQ_ANALYSIS_DEADLINE_SECONDS",
                str(ANALYSIS_DEADLINE_SECONDS_DEFAULT),
            )
        )
    except (TypeError, ValueError):
        return ANALYSIS_DEADLINE_SECONDS_DEFAULT
    if not math.isfinite(value) or not 1.0 <= value <= 300.0:
        return ANALYSIS_DEADLINE_SECONDS_DEFAULT
    return value


ANALYSIS_DEADLINE_SECONDS = _analysis_deadline_seconds()
REAL_WRITING_APPROVED = os.environ.get("LDFREQ_REAL_WRITING_APPROVED") == "1"


def _resolve_antbnc_path() -> str:
    """Prefer configured AntBNC data, but fall back to bundled/local copies."""
    default_path = PROJECT_ROOT / "data" / "antbnc" / "antbnc_lemmas_ver_004.txt"
    candidates: list[Path] = []
    configured = os.environ.get("LDFREQ_ANTBNC_PATH")
    if configured:
        configured_path = Path(configured).expanduser()
        candidates.append(configured_path)
        if not configured_path.is_absolute():
            candidates.append(PROJECT_ROOT / configured_path)
    candidates.extend([
        default_path,
        PROJECT_ROOT / "antbnc_lemmas_ver_004.txt",
    ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate.resolve())
    return str((candidates[0] if candidates else default_path).resolve())


def _file_fingerprint(path: str | None) -> tuple[str, int | None, int | None]:
    if not path:
        return ("", None, None)
    candidate = Path(path).expanduser()
    try:
        stat = candidate.stat()
    except OSError:
        return (str(candidate), None, None)
    return (str(candidate.resolve()), stat.st_size, stat.st_mtime_ns)


def _path_fingerprint(path: str | None):
    if not path:
        return ("", None)
    candidate = Path(path).expanduser()
    if candidate.is_dir():
        files = []
        for fp in sorted(p for p in candidate.rglob("*") if p.is_file()):
            try:
                stat = fp.stat()
            except OSError:
                continue
            files.append((fp.relative_to(candidate).as_posix(), stat.st_size, stat.st_mtime_ns))
        return (str(candidate.resolve()), tuple(files))
    return _file_fingerprint(path)


def _private_resource_fingerprint(path: str | None) -> str | None:
    """Detect resource changes without retaining its private server path."""

    if not path:
        return None
    return _private_digest(repr(_path_fingerprint(path)).encode("utf-8", errors="replace"))


def _signature_key() -> bytes:
    """Return a session-only key used to compare inputs without retaining them."""
    if "_source_signature_key" not in st.session_state:
        st.session_state["_source_signature_key"] = os.urandom(32)
    return st.session_state["_source_signature_key"]


def _private_digest(payload: bytes) -> str:
    return hashlib.blake2b(
        payload,
        key=_signature_key(),
        digest_size=16,
    ).hexdigest()


def _upload_fingerprint(upload) -> dict:
    payload = b""
    if hasattr(upload, "getvalue"):
        payload = upload.getvalue()
    elif hasattr(upload, "read"):
        position = None
        try:
            position = upload.tell()
        except Exception:
            position = None
        payload = upload.read()
        if position is not None:
            try:
                upload.seek(position)
            except Exception:
                pass
    else:
        payload = bytes(upload)

    return {
        "size": len(payload),
        "type": getattr(upload, "type", None),
        "digest": _private_digest(payload),
    }


DEFAULT_ANTBNC = _resolve_antbnc_path()


# --------------------------------------------------------------------------- #
# Missing-value helpers (None/NaN = undefined or non-converged for this text)
# --------------------------------------------------------------------------- #
def _is_missing(v):
    return v is None or (isinstance(v, float) and v != v)


def _clean_deep(obj):
    """Recursively turn NaN floats into None so json.dumps emits valid JSON."""
    return clean_deep(obj)


def _lfp_figure(lfp_df: pd.DataFrame, thresholds):
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(
        go.Bar(
            x=lfp_df["level"],
            y=lfp_df["coverage_%"],
            name="Band coverage",
            marker_color="#4C78A8",
            hovertemplate="%{x}<br>coverage=%{y:.2f}%<extra></extra>",
        ),
        secondary_y=False,
    )
    fig.add_trace(
        go.Scatter(
            x=lfp_df["level"],
            y=lfp_df["cumulative_%"],
            name="Selected-list cumulative coverage",
            mode="lines+markers",
            line={"color": "#F58518", "width": 3},
            hovertemplate="%{x}<br>cumulative=%{y:.2f}%<extra></extra>",
        ),
        secondary_y=True,
    )
    for thr in thresholds:
        fig.add_hline(
            y=thr,
            line_dash="dot",
            line_color="#6B7280",
            opacity=0.45,
            annotation_text=f"{thr}%",
            annotation_position="right",
            secondary_y=True,
        )
    fig.update_layout(
        height=440,
        margin={"l": 12, "r": 12, "t": 24, "b": 12},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
    )
    fig.update_yaxes(title_text="Band coverage (%)", range=[0, 100], secondary_y=False)
    fig.update_yaxes(title_text="Selected-list cumulative coverage (%)", range=[0, 100], secondary_y=True)
    fig.update_xaxes(title_text="Frequency band")
    return fig


def _p_lex_figure(plx: dict):
    obs = plx.get("observed_distribution") or {}
    fit = plx.get("fitted_distribution") or {}
    if not obs:
        return None

    xs = sorted(obs)
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=xs,
            y=[obs[x] for x in xs],
            name="Observed",
            marker_color="#54A24B",
            hovertemplate="hard words=%{x}<br>observed=%{y:.3f}<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=xs,
            y=[fit.get(x, 0.0) for x in xs],
            name="Poisson fit",
            mode="lines+markers",
            line={"color": "#E45756", "width": 3},
            hovertemplate="hard words=%{x}<br>fit=%{y:.3f}<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        margin={"l": 12, "r": 12, "t": 24, "b": 12},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        bargap=0.2,
    )
    fig.update_xaxes(title_text="Hard words per 10-word segment", dtick=1)
    fig.update_yaxes(title_text="Probability", range=[0, 1])
    return fig


def _s_curve_figure(sidx: dict):
    emp = sidx.get("empirical_coverage_pct") or {}
    s_val = sidx.get("S")
    if not emp or _is_missing(s_val):
        return None

    ranks = sorted(emp)
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=ranks,
            y=[emp[x] for x in ranks],
            name="Empirical",
            mode="markers+lines",
            line={"color": "#4C78A8", "width": 3},
            hovertemplate="rank=%{x}<br>coverage=%{y:.2f}%<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=ranks,
            y=[100 * (math.log(x) / math.log(s_val)) for x in ranks],
            name="Fitted S curve",
            mode="lines",
            line={"color": "#B279A2", "width": 3, "dash": "dash"},
            hovertemplate="rank=%{x}<br>fit=%{y:.2f}%<extra></extra>",
        )
    )
    fig.update_layout(
        height=300,
        margin={"l": 12, "r": 12, "t": 24, "b": 12},
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
    )
    fig.update_xaxes(title_text="Frequency rank")
    fig.update_yaxes(title_text="Coverage (%)")
    return fig


def _index_profile_figure(rows: list[dict]):
    usable = [
        row for row in rows
        if isinstance(row.get("score"), (int, float)) and not _is_missing(row.get("score"))
    ]
    if len(usable) < 3:
        return None

    theta = [row["dimension"] for row in usable]
    scores = [row["score"] for row in usable]
    hover = [
        f"{row['dimension']}<br>score={row['score']:.1f}<br>{row['components']}"
        + (f"<br>{row['warning']}" if row.get("warning") else "")
        for row in usable
    ]
    theta_closed = [*theta, theta[0]]
    scores_closed = [*scores, scores[0]]
    hover_closed = [*hover, hover[0]]

    fig = go.Figure(go.Scatterpolar(
        r=scores_closed,
        theta=theta_closed,
        text=hover_closed,
        mode="lines+markers",
        fill="toself",
        line={"color": "#4C78A8", "width": 3},
        marker={"size": 7, "color": "#4C78A8"},
        hovertemplate="%{text}<extra></extra>",
    ))
    fig.update_layout(
        height=430,
        margin={"l": 40, "r": 40, "t": 30, "b": 30},
        polar={
            "radialaxis": {
                "visible": True,
                "range": [0, 100],
                "tickvals": [0, 25, 50, 75, 100],
            }
        },
        showlegend=False,
    )
    return fig


def build_excel(payload_json: str):
    """Build a user export without placing user-derived output in global cache."""
    return payload_to_excel(json.loads(payload_json))


# --------------------------------------------------------------------------- #
# Sidebar — controls
# --------------------------------------------------------------------------- #
st.sidebar.header("Settings")
unit = UNIT_TOKEN

_lemmatizer_options = ["open_flemma", "simplemma"]
if ALLOW_LOCAL_RESTRICTED or WL.server_only_enabled("antbnc"):
    _lemmatizer_options.append("antbnc")
_lemmatizer_labels = {
    "open_flemma": "Open flemma (project default)",
    "simplemma": "simplemma (comparison)",
    "antbnc": "AntBNC (authorized local comparison)",
}
lem_name = st.sidebar.selectbox(
    "Panel B fallback normalizer",
    _lemmatizer_options,
    index=0,
    format_func=lambda value: _lemmatizer_labels[value],
    help="Used when a token is not directly present in the selected frequency list. "
         "Maps tokens to POS-agnostic flemmas/head forms before retrying lookup. "
         "The project normalizer uses deterministic rules plus NGSL and Open English "
         "WordNet, and keeps unresolved homographs unchanged. AntBNC is available only "
         "in an authorized local/private deployment.")
if lem_name == "antbnc":
    st.sidebar.caption(f"AntBNC Lemma List by Laurence Anthony — download from "
                       f"[laurenceanthony.net]({ANTBNC_URL}). Used for *NWLC近似* "
                       f"(approximate, not identical).")
antbnc_path = DEFAULT_ANTBNC  # deployment config (env LDFREQ_ANTBNC_PATH), not a UI field

# Frequency-list selector — choose which reference list to profile against.
_avail = WL.available(include_restricted=ALLOW_LOCAL_RESTRICTED)
if _avail:
    _choice = st.sidebar.selectbox(
        "Frequency list", [e["name"] for e in _avail], index=0,
        help="The reference list defines the frequency bands for Panel B.")
    list_entry = next(e for e in _avail if e["name"] == _choice)
    list_path = list_entry["path"]
    st.sidebar.caption(f"License: {list_entry['license']}")
    if list_entry.get("source_url"):
        attribution = f"[Source]({list_entry['source_url']})"
        if list_entry.get("license_url"):
            attribution += f" · [License terms]({list_entry['license_url']})"
        st.sidebar.markdown(attribution)
    if list_entry.get("modification_notice"):
        st.sidebar.caption(f"Changes: {list_entry['modification_notice']}")
else:
    list_entry, list_path = None, None
    st.sidebar.error("No frequency list installed.")

_available_ids = {entry["id"] for entry in _avail}
_missing = [
    entry
    for entry in WL.REGISTRY
    if entry["id"] not in _available_ids
    and (
        entry.get("public_web", False)
        or ALLOW_LOCAL_RESTRICTED
        or entry["id"] in SERVER_ONLY_RESOURCE_IDS
    )
]
if _missing:
    with st.sidebar.expander("➕ Add another list"):
        st.caption("Install the data with env vars, local paths, or Streamlit secrets.")
        for e in _missing:
            tag = "bundled-capable" if e["redistributable"] else "external data"
            st.markdown(f"- **{e['name']}** — not installed · {tag} · "
                        f"[source]({e['source_url']})")
for warning in getattr(WL, "MATERIALIZATION_WARNINGS", []):
    st.sidebar.warning(warning)
if SERVER_ONLY_RESOURCE_IDS:
    st.sidebar.caption(
        "Operator-provisioned server-only resources are active. Their payloads "
        "are not included in user downloads."
    )

thresholds = st.sidebar.multiselect("Coverage thresholds (%)", [90, 95, 98],
                                    default=[90, 95, 98])
min_tokens = st.sidebar.slider("Min tokens for band-wise indices", 20, 200, 50, 10,
                               help="Below this, band-wise index values are still shown "
                                    "but marked with a warning.")

with st.sidebar.expander("Advanced index parameters"):
    seg = st.number_input("MSTTR segment", 10, 200, 50, 10)
    win = st.number_input("MATTR window", 10, 200, 50, 10)
    mtld_thr = st.number_input("MTLD threshold", 0.50, 0.95, 0.72, 0.01)
    hdd_n = st.number_input("HD-D sample size", 20, 100, 42, 1)
    vocd_seed = st.number_input("vocd-D seed", 0, 9999, 42, 1)
    adv_cut = st.number_input("Advanced-Guiraud cutoff (beyond K…)", 1, 7, 2, 1)

with st.sidebar.expander("Input limits"):
    max_file_mb = st.number_input(
        "Max text file size (MB)",
        1,
        MAX_FILE_MB_CEILING,
        MAX_FILE_MB_DEFAULT,
        1,
    )
    max_total_mb = st.number_input(
        "Max total extracted text (MB)",
        1,
        MAX_TOTAL_MB_CEILING,
        MAX_TOTAL_MB_DEFAULT,
        1,
    )
    max_documents = st.number_input(
        "Max documents",
        1,
        MAX_DOCUMENTS_CEILING,
        MAX_DOCUMENTS_DEFAULT,
        1,
        help="The operator-enforced ceiling protects shared public deployments. "
             "Total extracted text size is capped separately.",
    )


# --------------------------------------------------------------------------- #
# Main — input
# --------------------------------------------------------------------------- #
st.title("Lexical Diversity & Frequency-Profile Analyzer")
st.caption(f"ldfreq v{__version__} · Panel A = list-independent diversity · "
           f"Panel B = frequency profile against the selected list")

if "upload_widget_generation" not in st.session_state:
    st.session_state["upload_widget_generation"] = 0


def _clear_source_widgets() -> None:
    old_upload_key = f"uploads_{st.session_state['upload_widget_generation']}"
    st.session_state.pop(old_upload_key, None)
    st.session_state["input_text"] = ""
    st.session_state["upload_widget_generation"] += 1


def _delete_user_data() -> None:
    st.session_state.pop("analysis_state", None)
    st.session_state.pop("_source_signature_key", None)
    st.session_state.pop(SERVER_ONLY_QUERY_GUARD_KEY, None)
    _clear_source_widgets()
    st.session_state["privacy_message"] = {
        "kind": "success",
        "text": "Input and retained analysis results were deleted.",
    }


if st.session_state.pop("clear_source_after_analysis", False):
    _clear_source_widgets()

@st.fragment(run_every="60s")
def _result_ttl_guard() -> None:
    """Expire aggregate results while a browser session remains connected."""
    retained = st.session_state.get("analysis_state")
    if not retained:
        return
    created_at = float(retained.get("created_at", time.time()))
    if time.time() - created_at >= RESULT_TTL_SECONDS:
        st.session_state.pop("analysis_state", None)
        st.session_state["privacy_message"] = {
            "kind": "info",
            "text": "Retained aggregate results expired after 30 minutes.",
        }
        st.rerun()


_result_ttl_guard()

if "input_text" not in st.session_state:
    st.session_state["input_text"] = SAMPLE


def _load_sample():
    st.session_state["input_text"] = SAMPLE


st.caption(
    "Privacy default: source text and uploads are cleared after analysis. Only aggregate "
    "results with pseudonymous document labels are retained in this session. Batch labels "
    "follow upload/member order; original filenames are not retained. Each analysis runs "
    f"in a one-use process with a {ANALYSIS_DEADLINE_SECONDS:g}-second hard deadline."
)
if not REAL_WRITING_APPROVED:
    st.error(
        "Deployment status: research prototype. Submit only synthetic or already-public "
        "text; real learner writing is blocked pending institutional and infrastructure "
        "approval."
    )
if not POSIX_ISOLATION_AVAILABLE:
    st.error(
        "Analysis is disabled on this host: the privacy-preserving one-shot worker "
        "requires a POSIX host. Run the application in Linux, macOS, Docker, or WSL."
    )
st.warning(
    "Before submitting learner writing, remove names, student numbers, contact details, "
    "and unnecessary personal references. Do not submit sensitive information about "
    "health, disability, ethnicity, religion, politics, sexuality, or family circumstances."
)
col_in, col_btn = st.columns([5, 1])
text = col_in.text_area("English text", key="input_text", height=180)
col_btn.button("Load sample", on_click=_load_sample)
col_btn.button("Delete data", on_click=_delete_user_data, help="Clear input and retained results.")
uploads = st.file_uploader("…or upload .txt file(s) or .zip archives", type=["txt", "zip"],
                           accept_multiple_files=True,
                           key=f"uploads_{st.session_state['upload_widget_generation']}")
if uploads:
    st.caption("Uploaded .txt files and .txt files inside ZIP archives are analyzed separately; pasted text is ignored while files are selected.")
if message := st.session_state.pop("privacy_message", None):
    if isinstance(message, dict) and message.get("kind") == "warning":
        st.warning(message.get("text"))
    elif isinstance(message, dict) and message.get("kind") == "info":
        st.info(message.get("text"))
    else:
        st.success(message.get("text") if isinstance(message, dict) else message)

analyze_clicked = st.button(
    "Analyze",
    type="primary",
    disabled=not POSIX_ISOLATION_AVAILABLE,
    help=(
        None
        if POSIX_ISOLATION_AVAILABLE
        else "The isolated analysis worker requires a POSIX host."
    ),
)


# --------------------------------------------------------------------------- #
# Analysis
# --------------------------------------------------------------------------- #
def _documents_from_input(text_value: str, uploaded_files) -> list[dict]:
    if not uploaded_files:
        try:
            pasted_bytes = len(text_value.encode("utf-8"))
        except UnicodeEncodeError:
            st.warning("Pasted text is not valid UTF-8 and was not accepted.")
            return []
        if pasted_bytes > int(max_total_mb * 1024 * 1024):
            st.warning("Pasted text exceeds the configured total input limit.")
            return []
        return PRIVACY.pseudonymize_documents([{"name": "Pasted text", "text": text_value}])
    docs, warnings = documents_from_uploads(
        uploaded_files,
        max_file_bytes=int(max_file_mb * 1024 * 1024),
        max_total_bytes=int(max_total_mb * 1024 * 1024),
        max_documents=int(max_documents),
        max_archive_bytes=int(max_total_mb * 1024 * 1024),
        redact_names=True,
    )
    for warning in warnings:
        st.warning(warning)
    return PRIVACY.pseudonymize_documents(docs)


def _analysis_signature(text_value: str, uploaded_files) -> dict:
    uploads_signature = [_upload_fingerprint(upload) for upload in uploaded_files or []]
    return {
        "input_digest": (
            None
            if uploaded_files
            else _private_digest(text_value.encode("utf-8", errors="replace"))
        ),
        "uploads": uploads_signature,
        "unit": unit,
        "lemmatizer": lem_name,
        "lemmatizer_fingerprint": (
            _private_resource_fingerprint(antbnc_path)
            if lem_name == "antbnc"
            else None
        ),
        "frequency_list_id": list_entry["id"] if list_entry else None,
        "frequency_list_fingerprint": _private_resource_fingerprint(list_path),
        "thresholds": list(thresholds or [95]),
        "min_tokens": min_tokens,
        "msttr_segment": seg,
        "mattr_window": win,
        "mtld_threshold": mtld_thr,
        "hdd_sample": hdd_n,
        "vocd_seed": vocd_seed,
        "advanced_cutoff": adv_cut,
        "max_file_mb": max_file_mb,
        "max_total_mb": max_total_mb,
        "max_documents": max_documents,
    }


def _list_label(meta, entry=None, path=None):
    if not meta:
        return "None installed (Panel B unavailable)"
    entry = entry or list_entry
    path = path or list_path
    location = (
        "server-only resource"
        if entry.get("delivery_mode") == "server-side-only"
        else f"`{Path(path).name}`"
    )
    return (f"{entry['name']} ({location}, "
            f"{meta['n_levels']}×1000, {meta['entries']} entries, "
            f"{meta['variants']} variants)")


def _panel_a_rows(result):
    n_tok = result["n_tokens"]
    settings = (result.get("payload") or {}).get("settings") or {}
    segment = settings.get("msttr_segment", seg)
    window = settings.get("mattr_window", win)
    hdd_sample = settings.get("hdd_sample", hdd_n)
    rows = []
    for k in IDX._FUNCS:
        v = result["indices"][k]
        eff = IDX.effective_min_tokens(k, segment=segment, window=window, hdd_sample=hdd_sample)
        warning = f"N={n_tok} < {eff}; interpret cautiously" if n_tok < eff else ""
        if _is_missing(v):
            value = "— (NA)"
            warning = "; ".join(
                part for part in [warning, "undefined for this text (all-distinct / no convergence)"]
                if part
            )
        else:
            value = f"{v:.4f}"
        rows.append({
            "Index": IDX.PRETTY[k],
            "Value": value,
            "↑=diverse": "↑" if IDX.DIRECTION[k] > 0 else "↓",
            "Required tokens": eff,
            "Warning": warning,
        })
    return rows


def _tubelex_rows(tubelex):
    """Format the open-corpus aggregate without inventing values for misses."""

    def _pair(token_keys, type_keys, *, percent=False):
        values = []
        for keys in (token_keys, type_keys):
            value = next(
                (tubelex.get(key) for key in keys if not _is_missing(tubelex.get(key))),
                None,
            )
            if _is_missing(value):
                values.append("— (NA)")
            elif percent:
                values.append(f"{100 * float(value):.2f}%")
            else:
                values.append(f"{float(value):.4f}")
        return values

    coverage = _pair(("token_coverage",), ("type_coverage",), percent=True)
    zipf = _pair(
        ("frequency_zipf_token_mean",),
        ("frequency_zipf_type_mean",),
    )
    video_prevalence = _pair(
        ("video_log10_prevalence_token_mean",),
        ("video_log10_prevalence_type_mean",),
    )
    channel_prevalence = _pair(
        ("channel_log10_prevalence_token_mean",),
        ("channel_log10_prevalence_type_mean",),
    )
    return [
        {
            "Index": "TUBELEX-EN Treebank lookup units",
            "Token-weighted value": tubelex.get("tokens", "— (NA)"),
            "Type-weighted value": tubelex.get("types", "— (NA)"),
            "Interpretation": "Token/type counts after corpus-specific Treebank tokenization",
        },
        {
            "Index": "TUBELEX-EN Treebank coverage",
            "Token-weighted value": coverage[0],
            "Type-weighted value": coverage[1],
            "Interpretation": "Share of input tokens or types found in the reference table",
        },
        {
            "Index": "Mean smoothed Zipf frequency",
            "Token-weighted value": zipf[0],
            "Type-weighted value": zipf[1],
            "Interpretation": "Lower = rarer in TUBELEX-EN Treebank; unseen units use a floor",
        },
        {
            "Index": "Mean log10 video prevalence",
            "Token-weighted value": video_prevalence[0],
            "Type-weighted value": video_prevalence[1],
            "Interpretation": "Closer to zero = found across more distinct videos",
        },
        {
            "Index": "Mean log10 channel prevalence",
            "Token-weighted value": channel_prevalence[0],
            "Type-weighted value": channel_prevalence[1],
            "Interpretation": "Closer to zero = found across more distinct channels",
        },
    ]


def _clamp_score(value, low=0.0, high=100.0):
    if _is_missing(value):
        return None
    return max(low, min(high, float(value)))


def _score_ratio(value):
    return _clamp_score(float(value) * 100) if not _is_missing(value) else None


def _score_cap(value, cap):
    if _is_missing(value) or cap <= 0:
        return None
    return _clamp_score(100 * float(value) / cap)


def _score_inverse_cap(value, cap):
    if _is_missing(value) or cap <= 0:
        return None
    return _clamp_score(100 * (1 - min(float(value), cap) / cap))


def _mean_available(values):
    usable = [float(v) for v in values if not _is_missing(v)]
    return sum(usable) / len(usable) if usable else None


def _warning_for_index(result, key):
    settings = (result.get("payload") or {}).get("settings") or {}
    n_tok = int(result.get("n_tokens") or 0)
    required = IDX.effective_min_tokens(
        key,
        segment=settings.get("msttr_segment", seg),
        window=settings.get("mattr_window", win),
        hdd_sample=settings.get("hdd_sample", hdd_n),
    )
    return f"N={n_tok} < {required}" if n_tok < required else ""


def _join_warnings(*warnings):
    return "; ".join(dict.fromkeys(w for w in warnings if w))


def _index_profile_rows(result):
    idx = result.get("indices") or {}
    pb = result.get("panel_b") or {}
    meta = result.get("list_meta") or {}
    mean_rank = pb.get("mean_rank") or {}
    max_rank = meta.get("max_rank") or 0

    rows = [
        {
            "dimension": "MATTR",
            "score": _score_ratio(idx.get("mattr")),
            "components": "MATTR x 100",
            "warning": _warning_for_index(result, "mattr"),
        },
        {
            "dimension": "HD-D",
            "score": _score_ratio(idx.get("hdd")),
            "components": "HD-D x 100",
            "warning": _warning_for_index(result, "hdd"),
        },
        {
            "dimension": "MTLD",
            "score": _score_cap(idx.get("mtld"), 150),
            "components": "MTLD scaled to 0-100 with cap at 150",
            "warning": _warning_for_index(result, "mtld"),
        },
        {
            "dimension": "vocd-D",
            "score": _score_cap(idx.get("vocd"), 100),
            "components": "vocd-D scaled to 0-100 with cap at 100",
            "warning": _warning_for_index(result, "vocd"),
        },
        {
            "dimension": "Low repetition",
            "score": _mean_available([
                _score_inverse_cap(idx.get("maas"), 0.20),
                _score_inverse_cap(idx.get("yule_k"), 1000),
            ]),
            "components": "Inverse Maas cap=.20; inverse Yule's K cap=1000",
            "warning": _join_warnings(
                _warning_for_index(result, "maas"),
                _warning_for_index(result, "yule_k"),
            ),
        },
    ]

    if pb:
        mean_log_rank_score = None
        if max_rank and not _is_missing(mean_rank.get("mean_log_rank")):
            mean_log_rank_score = _clamp_score(
                100 * float(mean_rank["mean_log_rank"]) / math.log(max_rank)
            )
        rows.extend([
            {
                "dimension": "Sophistication",
                "score": _mean_available([
                    _score_cap(pb.get("advanced_guiraud"), 5),
                    _clamp_score(pb.get("pct_beyond_k")),
                    mean_log_rank_score,
                ]),
                "components": "Advanced Guiraud cap=5; % beyond K; mean log-rank/list max",
                "warning": "Depends on the selected frequency list and cutoff",
            },
            {
                "dimension": "In-list coverage",
                "score": (None if _is_missing(mean_rank.get("pct_off_list"))
                          else _clamp_score(100 - float(mean_rank["pct_off_list"]))),
                "components": "100 - % off-list",
                "warning": "Coverage is not a diversity score; off-list policy matters",
            },
        ])
    else:
        rows.append({
            "dimension": "Panel B unavailable",
            "score": None,
            "components": "No installed frequency list",
            "warning": "Frequency-based profile dimensions omitted",
        })

    table_rows = []
    for row in rows:
        score = row["score"]
        table_rows.append({
            "Dimension": row["dimension"],
            "Profile score": "— (NA)" if _is_missing(score) else round(float(score), 1),
            "Components": row["components"],
            "Warning": row["warning"],
        })
    return rows, table_rows


def _threshold_rows(pb):
    return [
        {
            "threshold_%": thr,
            "selected_list_band_needed": f"K{band}" if band else "not reached by selected list",
        }
        for thr, band in pb["coverage_threshold"].items()
    ]


def _p_lex_s_rows(pb):
    plx, sidx = pb["p_lex"], pb["s_index"]
    lam = plx["lambda"]
    return [
        {
            "measure": "P_Lex λ",
            "value": (round(lam, 3) if not _is_missing(lam) else "— (NA)"),
            "note": "Poisson fit over complete 10-word segments",
        },
        {
            "measure": "P_Lex segments",
            "value": plx["n_segments"],
            "note": "Complete 10-word segments used",
        },
        {
            "measure": "S (fitted rank)",
            "value": sidx.get("S") if not _is_missing(sidx.get("S")) else "— (NA)",
            "note": "Selected-list rank where fitted coverage reaches 100%",
        },
        {
            "measure": "S capped",
            "value": sidx.get("capped", "—"),
            "note": sidx.get("note") or sidx.get("reference_list_note", ""),
        },
    ]


def _band_wise_rows(pb):
    def _fmt(x):
        if _is_missing(x):
            return "— (NA)"
        return f"{x:.2f}" if isinstance(x, float) else x

    rows = []
    for row in pb["band_wise"]:
        d = {k: _fmt(v) for k, v in row.items()}
        d["Required tokens"] = d.pop("Min N")
        d["Warning"] = ("" if row["tokens"] >= row["Min N"] else
                        f"{row['tokens']} tokens < {row['Min N']}; interpret cautiously")
        rows.append(d)
    return rows


def _lookup_labels(meta):
    if meta and meta.get("lookup_unit") in {"word_family", "range_word_family"}:
        return {
            "singular": "word family",
            "plural": "families",
            "head_column": "family_head",
            "profile_title": "Word family profile",
        }
    return {
        "singular": "flemma",
        "plural": "flemmas",
        "head_column": "flemma",
        "profile_title": "Flemma profile",
    }


def _flemma_rows(result):
    pb = result.get("panel_b") or {}
    mapped = pb.get("_mapped") or []
    if not mapped:
        return []

    labels = _lookup_labels(result.get("list_meta"))
    head_column = labels["head_column"]
    raw_surfaces = result.get("raw_surfaces") or result.get("raw_tokens") or []
    raw_tokens = result.get("raw_tokens") or [str(surface).lower() for surface in raw_surfaces]
    total = len(mapped)
    buckets = {}
    for surface, token, (head, rank) in zip(raw_surfaces, raw_tokens, mapped):
        level_num = FRQ.level_of(rank)
        level = f"K{level_num}" if level_num else "off-list"
        key = (head, rank, level)
        if key not in buckets:
            buckets[key] = {
                head_column: head,
                "rank": rank,
                "level": level,
                "tokens": 0,
                "token_forms": Counter(),
                "surface_forms": Counter(),
            }
        buckets[key]["tokens"] += 1
        buckets[key]["token_forms"][token] += 1
        buckets[key]["surface_forms"][surface] += 1

    def _surface_summary(counter: Counter, limit: int = 8) -> str:
        parts = []
        for form, count in counter.most_common(limit):
            parts.append(f"{form} ({count})" if count > 1 else str(form))
        if len(counter) > limit:
            parts.append(f"+{len(counter) - limit} more")
        return ", ".join(parts)

    rows = []
    for item in buckets.values():
        rank = item["rank"]
        rows.append({
            head_column: item[head_column],
            "level": item["level"],
            "rank": rank if rank is not None else "off-list",
            "tokens": item["tokens"],
            "coverage_%": round(100 * item["tokens"] / total, 2) if total else 0.0,
            "token_forms": _surface_summary(item["token_forms"]),
            "surface_forms": _surface_summary(item["surface_forms"]),
        })
    return sorted(
        rows,
        key=lambda row: (
            row["rank"] == "off-list",
            row["rank"] if row["rank"] != "off-list" else math.inf,
            -row["tokens"],
            row[head_column],
        ),
    )


def _band_order(level):
    if isinstance(level, str) and level.startswith("K"):
        try:
            return int(level[1:])
        except ValueError:
            return 999
    return 999


def _file_band_heatmap(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    order = sorted(df["level"].unique(), key=_band_order)
    pivot = df.pivot_table(
        index="document",
        columns="level",
        values="coverage_%",
        aggfunc="first",
        fill_value=0.0,
    ).reindex(columns=order)
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=list(pivot.columns),
        y=list(pivot.index),
        zmin=0,
        zmax=100,
        colorscale="Blues",
        colorbar={"title": "coverage %"},
        hovertemplate="document=%{y}<br>band=%{x}<br>coverage=%{z:.2f}%<extra></extra>",
    ))
    fig.update_layout(height=max(320, 34 * len(pivot.index) + 120),
                      margin={"l": 12, "r": 12, "t": 24, "b": 12})
    fig.update_xaxes(title_text="Frequency band")
    fig.update_yaxes(title_text="Document")
    return fig


def _reliability_heatmap(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    index_order = [IDX.PRETTY[key] for key in IDX._FUNCS]
    code = df.pivot_table(
        index="document",
        columns="index",
        values="status_code",
        aggfunc="first",
    ).reindex(columns=index_order)
    status = df.pivot_table(
        index="document",
        columns="index",
        values="status",
        aggfunc="first",
    ).reindex(columns=index_order)
    fig = go.Figure(go.Heatmap(
        z=code.values,
        x=list(code.columns),
        y=list(code.index),
        text=status.values,
        texttemplate="%{text}",
        zmin=0,
        zmax=2,
        colorscale=[
            [0.0, "#E45756"], [0.33, "#E45756"],
            [0.34, "#F2CF5B"], [0.66, "#F2CF5B"],
            [0.67, "#54A24B"], [1.0, "#54A24B"],
        ],
        colorbar={"tickvals": [0, 1, 2],
                  "ticktext": ["too short", "undefined", "available"]},
        hovertemplate="document=%{y}<br>index=%{x}<br>status=%{text}<extra></extra>",
    ))
    fig.update_layout(height=max(320, 34 * len(code.index) + 140),
                      margin={"l": 12, "r": 12, "t": 24, "b": 12})
    fig.update_xaxes(title_text="Index")
    fig.update_yaxes(title_text="Document")
    return fig


def _offlist_bar(rows, top_n=25):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    top = (df.groupby("head", as_index=False)["count"].sum()
             .sort_values("count", ascending=False)
             .head(top_n)
             .sort_values("count", ascending=True))
    fig = go.Figure(go.Bar(
        x=top["count"],
        y=top["head"],
        orientation="h",
        marker_color="#E45756",
        hovertemplate="head=%{y}<br>count=%{x}<extra></extra>",
    ))
    fig.update_layout(height=max(320, 22 * len(top) + 120),
                      margin={"l": 12, "r": 12, "t": 24, "b": 12})
    fig.update_xaxes(title_text="Off-list token count")
    fig.update_yaxes(title_text="Off-list head")
    return fig


def _offlist_cause_bar(rows):
    df = pd.DataFrame(rows)
    if df.empty or "cause" not in df.columns:
        return None
    summary = (
        df.groupby("cause", as_index=False)["count"].sum()
          .sort_values("count", ascending=True)
    )
    fig = go.Figure(go.Bar(
        x=summary["count"],
        y=summary["cause"],
        orientation="h",
        marker_color="#72B7B2",
        hovertemplate="cause=%{y}<br>count=%{x}<extra></extra>",
    ))
    fig.update_layout(height=max(300, 42 * len(summary) + 100),
                      margin={"l": 12, "r": 12, "t": 24, "b": 12})
    fig.update_xaxes(title_text="Off-list token count")
    fig.update_yaxes(title_text="Likely cause")
    return fig


def _overlap_heatmap(rows):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    df["jaccard_%"] = pd.to_numeric(df["jaccard"], errors="coerce") * 100
    pivot = df.pivot_table(
        index="document_a",
        columns="document_b",
        values="jaccard_%",
        aggfunc="first",
    )
    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=list(pivot.columns),
        y=list(pivot.index),
        zmin=0,
        zmax=100,
        colorscale="Viridis",
        colorbar={"title": "Jaccard %"},
        hovertemplate="document A=%{y}<br>document B=%{x}<br>overlap=%{z:.2f}%<extra></extra>",
    ))
    fig.update_layout(height=max(320, 34 * len(pivot.index) + 120),
                      margin={"l": 12, "r": 12, "t": 24, "b": 12})
    fig.update_xaxes(title_text="Document B")
    fig.update_yaxes(title_text="Document A")
    return fig


def _render_export_buttons(payload, key_prefix):
    multi = "documents" in payload
    stem = "lexdiv_results_batch" if multi else "lexdiv_results"
    payload_json = payload_to_json(payload)
    c_json, c_xlsx = st.columns(2)
    with c_json:
        st.download_button(
            "Download results (JSON)",
            payload_json,
            file_name=f"{stem}.json",
            mime="application/json",
            key=f"{key_prefix}_json",
        )
    with c_xlsx:
        st.download_button(
            "Download results (Excel)",
            build_excel(payload_json),
            file_name=f"{stem}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"{key_prefix}_xlsx",
        )


def _render_result(result, payload):
    pb = result["panel_b"]
    meta = result["list_meta"]
    settings = (result.get("payload") or {}).get("settings") or {}
    effective_lem = result.get("effective_lemmatizer") or {}
    effective_lem_name = effective_lem.get(
        "name", settings.get("lemmatizer", "unknown")
    )
    effective_lem_version = effective_lem.get(
        "version", settings.get("lemmatizer_version", "unknown")
    )
    min_tokens_label = settings.get("min_tokens", min_tokens)
    thresholds_label = settings.get("thresholds") or thresholds or [95]
    adv_cut_label = settings.get("advanced_cutoff", adv_cut)
    lookup_labels = _lookup_labels(meta)

    st.markdown(
        f"**Document:** {result['name']} · "
        f"**List:** {_list_label(meta, result.get('list_entry'), result.get('list_path'))} · "
        f"**Panel B normalizer:** {effective_lem_name} {effective_lem_version} · "
        f"**Tokens (N):** {result['n_tokens']} · **Types (V):** {result['n_types']} · "
        f"**Band-wise Min-N:** {min_tokens_label}"
    )
    st.caption(f"Tokenizer: {TOKENIZER_POLICY}")
    st.info(
        "Panel A and Panel B start from the same tokenized text but answer different "
        "questions. Panel A measures surface-token diversity without a reference list; "
        "Panel B maps tokens to the selected list/normalizer to summarize frequency "
        "bands, off-list rate, and selected-list coverage."
    )

    panel_a_view = "Panel A: Diversity"
    panel_b_view = "Panel B: List coverage"
    tubelex_view = "Open corpus frequency"
    semantic_view = "Semantic network"
    detail_options = [panel_a_view, panel_b_view]
    if result.get("tubelex"):
        detail_options.append(tubelex_view)
    if result.get("semantic_network"):
        detail_options.append(semantic_view)
    detail_options.extend(["Profile", "Export"])
    detail_view = st.segmented_control(
        "Detail view",
        detail_options,
        default=panel_a_view,
        key=f"detail_view_{result['name']}",
        label_visibility="collapsed",
    )

    if detail_view == panel_a_view:
        st.caption(
            "Panel A is list-independent: it uses lower-cased surface tokens only. "
            "No frequency list, rank, word family, or Panel B normalizer is applied."
        )
        st.dataframe(pd.DataFrame(_panel_a_rows(result)), width="stretch", hide_index=True)
        st.caption("Required tokens = recommended minimum for stable interpretation. "
                   "Values below that floor are shown with a warning.")
        st.caption("↓ indices (Maas, Yule's K) decrease with greater diversity. "
                   "“— (NA)” = no computable value; the **Warning** column says why "
                   "(see the Help / Formulas page).")

    elif detail_view == panel_b_view:
        if pb is None:
            st.warning("No frequency list is installed — Panel B is unavailable.")
        else:
            st.caption(
                "Panel B is reference-list dependent: each token is matched to a "
                "flemma/head or word-family head, then assigned a rank, K-band, or "
                "off-list status in the selected list."
            )
            lfp_df = pd.DataFrame(pb["lfp"]).rename(columns={"types": lookup_labels["plural"]})
            st.subheader("Lexical Frequency Profile")
            st.dataframe(lfp_df, width="stretch", hide_index=True)

            st.info(
                "Token- and word-level lookup rows are intentionally not retained in "
                "privacy mode. Aggregate band coverage includes off-list tokens in its "
                "denominator."
            )

            st.subheader("Selected-list coverage profile")
            st.plotly_chart(
                _lfp_figure(lfp_df, thresholds_label),
                width="stretch",
                config={"displaylogo": False},
            )

            st.subheader("Coverage thresholds — selected-list band needed")
            st.dataframe(pd.DataFrame(_threshold_rows(pb)), width="stretch", hide_index=True)
            st.caption(
                "These thresholds use selected-list matched text coverage. Proper nouns, "
                "marginal words, acronyms, transparent compounds, and other potentially "
                "known items are not automatically credited unless they match the selected "
                "list/normalizer; review off-list diagnostics before reporting reader-known "
                "lexical coverage."
            )

            def _metric(v, digits):
                return "—" if _is_missing(v) else round(v, digits)

            c3, c4, c5 = st.columns(3)
            c3.metric("Advanced Guiraud", _metric(pb["advanced_guiraud"], 3))
            c4.metric(f"% beyond K{adv_cut_label}", _metric(pb["pct_beyond_k"], 2))
            c5.metric("% off-list", _metric(pb["mean_rank"]["pct_off_list"], 2))
            c3.metric("Mean rank", _metric(pb["mean_rank"]["mean_rank"], 1))
            c4.metric("Mean log-rank", _metric(pb["mean_rank"]["mean_log_rank"], 3))

            st.subheader("P_Lex & S")
            st.info("P_Lex λ = Poisson curve-fit over 10-word segments (Meara & Bell 2001). "
                    "S = coverage-curve fit C(x)=ln(x)/ln(S)·100 (Kojima & Yamashita 2014). "
                    "⚠ S uses the **selected list's ranks**, not K&Y's BNC-spoken lists, so it "
                    "is method-faithful but not numerically comparable to published S values. "
                    "Both need ~150–200+ words for stable estimates; S is NA below 50.")
            plx, sidx = pb["p_lex"], pb["s_index"]
            st.dataframe(pd.DataFrame(_p_lex_s_rows(pb)), width="stretch", hide_index=True)
            emp_cov = sidx.get("empirical_coverage_pct") or {}
            if emp_cov:
                emp_rows = [
                    {"rank": rank, "empirical_coverage_%": cov}
                    for rank, cov in emp_cov.items()
                ]
                with st.expander("S empirical coverage by rank"):
                    st.dataframe(pd.DataFrame(emp_rows), width="stretch", hide_index=True)
            pfig = _p_lex_figure(plx)
            sfig = _s_curve_figure(sidx)
            c6, c7 = st.columns(2)
            with c6:
                st.subheader("P_Lex fit")
                if pfig is not None:
                    st.plotly_chart(pfig, width="stretch",
                                    config={"displaylogo": False})
                else:
                    st.caption("P_Lex fit is unavailable without complete 10-word segments.")
            with c7:
                st.subheader("S curve fit")
                if sfig is not None:
                    st.plotly_chart(sfig, width="stretch",
                                    config={"displaylogo": False})
                else:
                    st.caption("S curve fit is unavailable below 50 tokens.")

            st.subheader("Band-wise diversity")
            st.dataframe(pd.DataFrame(_band_wise_rows(pb)), width="stretch", hide_index=True)
            st.caption(
                "Band-wise diversity reuses Panel A-style diversity indices inside each "
                "Panel B frequency band. It is a within-band diagnostic, not a duplicate "
                "of the overall Panel A table."
            )
            st.caption("Low-frequency bands rarely reach the recommended token floor. "
                       "Values are still shown, and the **Warning** column marks short bands.")

    elif detail_view == tubelex_view:
        tubelex = result.get("tubelex") or {}
        st.subheader(
            "TUBELEX-EN Treebank everyday-exposure frequency and contextual diversity"
        )
        st.dataframe(
            pd.DataFrame(_tubelex_rows(tubelex)),
            width="stretch",
            hide_index=True,
        )
        if (tubelex.get("tokens") or 0) < 50 or (tubelex.get("types") or 0) < 20:
            st.warning(
                "This TUBELEX profile has fewer than 50 lookup tokens or 20 lookup "
                "types. Values are retained for reproducibility but should be "
                "interpreted cautiously."
            )
        metadata = tubelex.get("metadata") or tubelex.get("resource_metadata") or {}
        resource = (
            tubelex.get("resource")
            or metadata.get("name")
            or "TUBELEX-EN Treebank"
        )
        version = (
            tubelex.get("resource_version")
            or tubelex.get("version")
            or tubelex.get("source_commit")
            or metadata.get("version")
            or "version recorded in the bundled manifest"
        )
        license_name = (
            tubelex.get("license")
            or metadata.get("license_spdx")
            or metadata.get("license")
            or "BSD-3-Clause"
        )
        st.caption(
            f"Resource: {resource} · Version: {version} · License: {license_name} · "
            "Lookup unit: lower-cased Treebank surface/clitic token"
        )
        st.info(
            "TUBELEX-EN Treebank is an open YouTube-derived frequency reference. Lower mean "
            "smoothed Zipf frequency indicates words that are rarer in that reference; "
            "log prevalence closer to zero indicates wider video/channel distribution. "
            "These values "
            "do not use COCA and are not numerically comparable with TAALES COCA "
            "indices. YouTube captions and transcripts are also not the same construct "
            "as spontaneous conversation."
        )
        st.caption(
            "Unseen units remain in the corpus means at a documented smoothing floor, "
            "so interpret the means together with token and type coverage. Names, "
            "spelling variants, and malformed forms can lower both without demonstrating "
            "lexical sophistication. TUBELEX lookup-unit counts may differ "
            "from Panel A because they use a separate corpus-specific NFKC, "
            "typographic-apostrophe, lower-case, sentence-pre-segmentation, and "
            "Treebank-tokenization pipeline."
        )

    elif detail_view == semantic_view:
        semantic = result.get("semantic_network") or {}
        rows = [
            {
                "Index": "OEWN token coverage",
                "Value": semantic.get("token_coverage"),
                "Interpretation": "Proportion of normalized tokens found in OEWN",
            },
            {
                "Index": "OEWN type coverage",
                "Value": semantic.get("type_coverage"),
                "Interpretation": "Proportion of normalized types found in OEWN",
            },
            {
                "Index": "Polysemy, token mean",
                "Value": semantic.get("polysemy_token_mean"),
                "Interpretation": "Mean number of OEWN senses, token weighted",
            },
            {
                "Index": "Polysemy, type mean",
                "Value": semantic.get("polysemy_type_mean"),
                "Interpretation": "Mean number of OEWN senses, type weighted",
            },
            {
                "Index": "Hypernym depth, token mean",
                "Value": semantic.get("hypernym_depth_token_mean"),
                "Interpretation": "Mean longest noun/verb hypernym path, token weighted",
            },
            {
                "Index": "Hypernym depth, type mean",
                "Value": semantic.get("hypernym_depth_type_mean"),
                "Interpretation": "Mean longest noun/verb hypernym path, type weighted",
            },
        ]
        st.subheader("Open semantic-network indices")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
        st.caption(
            f"Resource: {semantic.get('resource')} · License: "
            f"{semantic.get('license')} · Normalizer: {semantic.get('normalizer')}"
        )
        st.info(
            "These are an open reconstruction, not numerical replicas of TAALES. "
            "The current baseline is POS-agnostic and aggregates senses across parts "
            "of speech; POS-aware and contextualized variants remain a later research phase."
        )

    elif detail_view == "Profile":
        st.subheader("Index profile radar")
        profile_rows, profile_table = _index_profile_rows(result)
        profile_fig = _index_profile_figure(profile_rows)
        st.info(
            "Profile scores are heuristic 0-100 rescalings for visual comparison within "
            "the same settings. They are not corpus norms, and higher is not always "
            "better for sophistication or coverage-risk dimensions."
        )
        if profile_fig is not None:
            st.plotly_chart(profile_fig, width="stretch", config={"displaylogo": False})
        else:
            st.warning("Not enough computable profile dimensions for a radar chart.")
        st.dataframe(pd.DataFrame(profile_table), width="stretch", hide_index=True)
        st.caption("Use the raw Panel A and Panel B tables for reporting. The radar is a "
                   "summary view; short-text warnings and selected-list effects still apply.")

    elif detail_view == "Export":
        _render_export_buttons(payload, f"detail_{result['name']}")
        st.json(payload, expanded=False)


def _render_batch_results(results, payload):
    diagnostics = payload.get("batch_diagnostics") or {}
    view = st.segmented_control(
        "Batch view",
        [
            "Overview",
            "Bands",
            "Reliability",
            "Overlap",
            "Detail",
        ],
        default="Overview",
        key="batch_view",
        label_visibility="collapsed",
    )

    if view == "Overview":
        st.subheader("Batch summary")
        st.dataframe(pd.DataFrame(summary_rows(payload)), width="stretch", hide_index=True)
        st.subheader("Descriptive statistics")
        st.dataframe(pd.DataFrame(descriptive_rows(payload)), width="stretch", hide_index=True)
        st.subheader("Export")
        _render_export_buttons(payload, "batch_overview")

    elif view == "Bands":
        st.subheader("File x band heatmap")
        rows = diagnostics.get("bands") or []
        fig = _file_band_heatmap(rows)
        if fig is None:
            st.warning("Frequency-band data is unavailable because Panel B is unavailable.")
        else:
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
            with st.expander("Band coverage table"):
                st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)

    elif view == "Reliability":
        st.subheader("Index reliability heatmap")
        rows = diagnostics.get("reliability") or []
        fig = _reliability_heatmap(rows)
        if fig is not None:
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        table = pd.DataFrame(rows)
        if not table.empty:
            table = table.drop(columns=["status_code"])
        st.dataframe(table, width="stretch", hide_index=True)

    elif view == "Off-list":
        st.subheader("Off-list diagnostics")
        rows = diagnostics.get("off_list") or []
        if not rows:
            if any(result.get("panel_b") for result in results):
                st.success("No off-list tokens were found in the selected frequency list.")
            else:
                st.warning("Off-list diagnostics require Panel B frequency-list analysis.")
        else:
            df = pd.DataFrame(rows)
            c1, c2, c3 = st.columns(3)
            c1.metric("Off-list tokens", int(df["count"].sum()))
            c2.metric("Off-list heads", int(df["head"].nunique()))
            c3.metric("Documents with off-list", int(df["document"].nunique()))
            cause_fig = _offlist_cause_bar(rows)
            if cause_fig is not None:
                st.subheader("Likely cause distribution")
                st.plotly_chart(cause_fig, width="stretch", config={"displaylogo": False})
                st.caption("Cause labels are surface-form heuristics for review, not POS tagging or named-entity recognition.")
            top_n = st.slider("Top off-list heads", 5, 50, 25, 5)
            fig = _offlist_bar(rows, top_n=top_n)
            if fig is not None:
                st.subheader("Top off-list heads")
                st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
            doc_options = ["All documents", *[result["name"] for result in results]]
            selected_doc = st.selectbox("Off-list table scope", doc_options)
            table = df if selected_doc == "All documents" else df[df["document"] == selected_doc]
            st.dataframe(table, width="stretch", hide_index=True)

    elif view == "Overlap":
        st.subheader("Lexical overlap")
        matrix_rows = diagnostics.get("overlap_matrix") or []
        fig = _overlap_heatmap(matrix_rows)
        if fig is not None:
            st.plotly_chart(fig, width="stretch", config={"displaylogo": False})
        pair_rows = diagnostics.get("overlap_pairs") or []
        pair_df = pd.DataFrame(pair_rows)
        if not pair_df.empty:
            pair_df["jaccard_%"] = (pd.to_numeric(pair_df["jaccard"], errors="coerce") * 100).round(2)
        st.dataframe(pair_df, width="stretch", hide_index=True)

    elif view == "Detail":
        selected_name = st.selectbox("Detailed document", [r["name"] for r in results])
        selected = next(r for r in results if r["name"] == selected_name)
        _render_result(selected, selected.get("payload") or payload)


def _render_analysis_state(state, *, replay: bool = False):
    results = state["results"]
    payload = state["payload"]
    elapsed = state.get("elapsed")
    if replay:
        st.caption(f"Showing last analysis for {len(results)} document(s).")
    elif elapsed is not None:
        st.caption(f"Analysis completed in {elapsed:.2f}s for {len(results)} document(s).")

    if len(results) > 1:
        _render_batch_results(results, payload)
    else:
        _render_result(results[0], payload)


def run(documents, signature):
    start_time = time.perf_counter()
    st.session_state.pop("analysis_state", None)
    if not documents:
        st.warning("No valid text documents to analyze. Check the upload type and input limits.")
        return False

    guard_applied = False
    guard_succeeded = False
    short_rejections = 0
    if list_entry and list_entry["id"] in SERVER_ONLY_RESOURCE_IDS:
        guard_state = QUERY_GUARD.state_from_mapping(
            st.session_state.get(SERVER_ONLY_QUERY_GUARD_KEY),
            SERVER_ONLY_QUERY_GUARD_CONFIG,
        )
        decision = QUERY_GUARD.authorize(
            guard_state,
            len(documents),
            SERVER_ONLY_QUERY_GUARD_CONFIG,
        )
        st.session_state[SERVER_ONLY_QUERY_GUARD_KEY] = decision.state.to_mapping()
        if not decision.allowed:
            raise QUERY_GUARD.QueryBudgetExceeded(
                decision.retry_after_seconds,
                decision.reason or "session query budget",
            )
        guard_applied = True

    try:
        progress_bar = st.progress(0, text="Preparing analysis...")
        progress_status = st.empty()
        config = ANALYSIS.AnalysisConfig(
            thresholds=tuple(thresholds or [95]),
            min_tokens=int(min_tokens),
            msttr_segment=int(seg),
            mattr_window=int(win),
            mtld_threshold=float(mtld_thr),
            hdd_sample=int(hdd_n),
            vocd_seed=int(vocd_seed),
            advanced_cutoff=int(adv_cut),
            unit=unit,
            tokenizer_policy=TOKENIZER_POLICY,
        )
        if lem_name == "antbnc" and ALLOW_LOCAL_RESTRICTED:
            # Operator-controlled path only; source text is never placed in env.
            os.environ["LDFREQ_ANTBNC_PATH"] = antbnc_path
        resource_spec = ISOLATED.ResourceSpec(
            list_id=list_entry["id"] if list_entry else None,
            lemmatizer_name=lem_name,
            semantic_enabled=True,
            tubelex_enabled=True,
        )
        isolation_limits = ISOLATED.IsolationLimits(
            deadline_seconds=ANALYSIS_DEADLINE_SECONDS,
            max_source_bytes=int(max_total_mb * 1024 * 1024),
            max_documents=int(max_documents),
        )

        def _show_progress(completed, total, label):
            progress_status.caption(f"Analyzing {completed}/{total}: {label}")
            progress_bar.progress(
                int(completed / total * 90),
                text=f"Analyzed {completed}/{total} document(s)",
            )

        outcome = ISOLATED.analyze_documents_isolated(
            documents,
            config,
            resource_spec,
            limits=isolation_limits,
            progress=_show_progress,
        )
        short_rejections = sum(
            1
            for item in outcome.skipped
            if item.get("error") == "No tokens found."
            or str(item.get("error", "")).startswith(
                "Server-only list analysis requires at least"
            )
        )

        for item in outcome.skipped:
            st.warning(f"Skipped {item['name']}: {item['error']}")

        if not outcome.results:
            progress_bar.empty()
            progress_status.empty()
            st.warning("No tokens found.")
            return False

        progress_status.caption("Preparing batch diagnostics and export payload...")
        payload = outcome.payload
        retained_results = list(outcome.results)
        elapsed = time.perf_counter() - start_time
        progress_bar.progress(100, text=f"Analysis complete in {elapsed:.2f}s")
        progress_status.empty()
        state = {
            "results": retained_results,
            "payload": payload,
            "elapsed": elapsed,
            "signature": signature,
            "created_at": time.time(),
        }
        unsafe_paths = PRIVACY.sensitive_paths(state)
        if unsafe_paths:
            raise RuntimeError("Privacy invariant failed before retaining analysis state.")
        st.session_state["analysis_state"] = state
        guard_succeeded = True
        _render_analysis_state(state)
        return True
    finally:
        if guard_applied:
            current_guard_state = QUERY_GUARD.state_from_mapping(
                st.session_state.get(SERVER_ONLY_QUERY_GUARD_KEY),
                SERVER_ONLY_QUERY_GUARD_CONFIG,
            )
            st.session_state[SERVER_ONLY_QUERY_GUARD_KEY] = QUERY_GUARD.record_outcome(
                current_guard_state,
                success=guard_succeeded,
                short_rejections=short_rejections,
                config=SERVER_ONLY_QUERY_GUARD_CONFIG,
            ).to_mapping()


if analyze_clicked:
    completed = False
    failure_message = None
    try:
        completed = run(
            _documents_from_input(text, uploads),
            _analysis_signature(text, uploads),
        )
    except QUERY_GUARD.QueryBudgetExceeded as exc:
        failure_message = (
            "Server-only session query budget is temporarily unavailable. "
            f"Retry-After: {exc.retry_after_seconds} seconds. The source input was cleared."
        )
    except ISOLATED.AnalysisDeadlineExceeded:
        failure_message = (
            f"Analysis exceeded the {ANALYSIS_DEADLINE_SECONDS:g}-second processing "
            "deadline. The isolated worker was stopped and the source input was cleared."
        )
    except ISOLATED.AnalysisInputInvalid:
        failure_message = (
            "The input exceeded an analysis boundary or was not valid UTF-8. "
            "The source input was cleared."
        )
    except ISOLATED.AnalysisBusy:
        failure_message = (
            "The analysis worker is busy. No analysis was started, and the source "
            "input was cleared."
        )
    except ISOLATED.AnalysisWorkerError as exc:
        if exc.code == "resource-unavailable":
            failure_message = (
                "A rights-gated analysis resource is unavailable. Ask the operator to "
                "verify the deployment; the source input was cleared."
            )
        else:
            failure_message = (
                "The isolated analysis worker failed. The source input was cleared, "
                "and no request content was written to its output streams."
            )
    except (ISOLATED.AnalysisProtocolError, ISOLATED.AnalysisIsolationError):
        failure_message = (
            "The isolated analysis boundary rejected the result. The source input "
            "was cleared."
        )
    except FileNotFoundError:
        failure_message = (
            "A configured frequency resource is unavailable. The source input was cleared."
        )
    except Exception:  # noqa: BLE001
        failure_message = (
            "Analysis failed and the source input was cleared. No source text was "
            "written to the application log."
        )
    if completed:
        st.session_state["analysis_state"]["signature"] = _analysis_signature("", [])
    else:
        st.session_state.pop("analysis_state", None)
        st.session_state["privacy_message"] = {
            "kind": "warning",
            "text": failure_message or "No result was produced; the source input was cleared.",
        }
    st.session_state["clear_source_after_analysis"] = True
    st.rerun()
elif "analysis_state" in st.session_state:
    current_signature = _analysis_signature(text, uploads)
    if current_signature != st.session_state["analysis_state"].get("signature"):
        st.warning("Inputs or settings have changed since the last analysis. Press Analyze to refresh the results below.")
    _render_analysis_state(st.session_state["analysis_state"], replay=True)
else:
    st.info("Set options in the sidebar, paste text, and press **Analyze**.")
