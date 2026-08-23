"""Registry of frequency lists the analyzer can profile against.

Each entry is metadata + a loader. A list is *available* only if its data file is
actually present on disk (paths are deployment config via env vars), so the UI can
show installed lists in the selector and not-installed ones with a source link.

Reviewed bundled data and governance manifests live under ``data/``. NJ8 and
AntBNC payloads can be materialized only in explicit local restricted mode from
Streamlit/env configuration or a local path.
"""
from __future__ import annotations

import base64
import binascii
import glob
import io
import os
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from .frequency import (
    load_bnc_coca_families_xlsx,
    load_headword_bands,
    load_ngsl,
    load_ranked_list,
    load_range_baseword_lists,
)
from .nation_bnc_coca import (
    ARTIFACT_NAME as NATION_BNCCOCA_ARTIFACT_NAME,
    load_nation_bnc_coca_index,
    sha256_file,
)
from .server_only_gate import (
    SERVER_ONLY_ELIGIBLE_IDS,
    SERVER_ONLY_RESOURCE_IDS_ENV,
    SERVER_ONLY_RIGHTS_ACK_ENV,
    configured_server_only_ids,
)

# Project root (one level above this package), used to resolve default data paths.
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_RUNTIME_DIR = os.path.join(_ROOT, ".streamlit", "runtime_lists")
MATERIALIZATION_WARNINGS: list[str] = []
MAX_SECRET_ZIP_MEMBERS = 100
MAX_SECRET_ZIP_MEMBER_BYTES = 10 * 1024 * 1024
MAX_SECRET_ZIP_TOTAL_BYTES = 25 * 1024 * 1024
MAX_SECRET_ZIP_COMPRESSION_RATIO = 300.0
NATION_HEADWORD_FILES = {
    "headwords 1st 1000.txt": (6922, "b1bc5d9d797eb1f21cd7724b1c201440d287c5724ec0e4e4d4090d5123cf692f"),
    "headwords 2nd 1000.txt": (7815, "693b7068534d1dd335b50feef1f09c7598afa88dce807d020397b457785073a9"),
    "headwords 3rd 1000.txt": (9122, "d22afa462d426035e24a34c3b400bb629babb0b5a4f571fd54496a0a34a24ffd"),
    "headwords 4th 1000.txt": (8635, "a4c4d76c029a49fdc656c22d91b201523e3f11cf2e3eeb4539f23af037409a55"),
    "headwords 5th 1000.txt": (8648, "0364af40dfa68b2e0f60e9581c02c53e06740e8c138fffaf9fbb96c61eafcdf0"),
    "headwords 6th 1000.txt": (8755, "5e210e85b67e7822e73a8fac3db7c3cc10afb0025c1e235062ca6b2c1bd237c8"),
    "headwords 7th 1000.txt": (8988, "3ad7716d82623b0b868f117080b2b792f1a4ded7b9beac6c7e9aea713a263d20"),
    "headwords 8th 1000.txt": (9081, "96293b09026cc50a125eccdd0806a05773b9c5e0776c0ff55cfe122f7f4f8449"),
    "headwords 9th 1000.txt": (9192, "54955a9a9b53f399276e08044e1b1b977b9307b776b5748aafc4766f67fdc936"),
    "headwords 10th 1000.txt": (9324, "5cefefc18fb4ee4ce7df2bb8a9c9c5cded50a56e8843a9f2b867e294d7179412"),
}
def _configured_server_only_ids() -> frozenset[str]:
    """Read the complete fail-closed gate before materializing private data."""

    return configured_server_only_ids(SERVER_ONLY_ELIGIBLE_IDS)


def _local_restricted_enabled() -> bool:
    """Require an explicit local serving mode as well as the private override."""

    return (
        os.environ.get("LDFREQ_SERVING_MODE") == "local"
        and os.environ.get("LDFREQ_ALLOW_LOCAL_RESTRICTED") == "1"
    )


def _decode_env_b64(env_name: str) -> bytes | None:
    value = os.environ.get(env_name)
    if not value:
        return None
    compact = "".join(value.split())
    try:
        return base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError) as exc:
        MATERIALIZATION_WARNINGS.append(f"Ignored {env_name}: invalid base64 ({exc}).")
        return None


def _write_if_changed(path: str, payload: bytes) -> None:
    if os.path.exists(path):
        if os.name == "posix":
            os.chmod(path, 0o600)
        try:
            with open(path, "rb") as fh:
                if fh.read() == payload:
                    return
        except OSError:
            pass
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "wb") as fh:
        fh.write(payload)
    if os.name == "posix":
        os.chmod(path, 0o600)


def _make_private_dir(path: str) -> None:
    os.makedirs(path, mode=0o700, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)


def _materialize_file_b64(env_name: str, output_name: str, path_env: str) -> None:
    if os.environ.get(path_env) and os.path.exists(os.environ[path_env]):
        return
    payload = _decode_env_b64(env_name)
    if payload is None:
        return
    _make_private_dir(_RUNTIME_DIR)
    path = os.path.join(_RUNTIME_DIR, output_name)
    _write_if_changed(path, payload)
    os.environ[path_env] = path


def _materialize_zip_b64(env_name: str, output_dir_name: str, path_env: str) -> None:
    if os.environ.get(path_env) and os.path.exists(os.environ[path_env]):
        return
    payload = _decode_env_b64(env_name)
    if payload is None:
        return
    target = os.path.join(_RUNTIME_DIR, output_dir_name)
    _make_private_dir(_RUNTIME_DIR)
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as zf:
            members: list[tuple[zipfile.ZipInfo, str]] = []
            seen: set[str] = set()
            total_bytes = 0
            if len(zf.infolist()) > MAX_SECRET_ZIP_MEMBERS:
                raise ValueError("too many ZIP members")
            for member in zf.infolist():
                if member.is_dir():
                    continue
                candidate = PurePosixPath(member.filename.replace("\\", "/"))
                if (
                    candidate.is_absolute()
                    or not candidate.parts
                    or any(part in {"", ".", ".."} for part in candidate.parts)
                ):
                    raise ValueError("unsafe ZIP member path")
                norm = candidate.as_posix()
                if norm in seen:
                    raise ValueError("duplicate ZIP member")
                seen.add(norm)
                if member.file_size > MAX_SECRET_ZIP_MEMBER_BYTES:
                    raise ValueError("ZIP member is too large")
                total_bytes += member.file_size
                if total_bytes > MAX_SECRET_ZIP_TOTAL_BYTES:
                    raise ValueError("ZIP expands beyond the total size limit")
                ratio = member.file_size / max(member.compress_size, 1)
                if ratio > MAX_SECRET_ZIP_COMPRESSION_RATIO:
                    raise ValueError("ZIP member compression ratio is too high")
                members.append((member, norm))

            with tempfile.TemporaryDirectory(
                prefix=f".{output_dir_name}-", dir=_RUNTIME_DIR
            ) as staging:
                _make_private_dir(staging)
                for member, norm in members:
                    staged_path = os.path.join(staging, norm)
                    _make_private_dir(os.path.dirname(staged_path))
                    with zf.open(member) as src:
                        content = src.read(MAX_SECRET_ZIP_MEMBER_BYTES + 1)
                    if len(content) != member.file_size:
                        raise ValueError("ZIP member size does not match its metadata")
                    _write_if_changed(staged_path, content)

                _make_private_dir(target)
                for _member, norm in members:
                    staged_path = os.path.join(staging, norm)
                    out_path = os.path.join(target, norm)
                    _make_private_dir(os.path.dirname(out_path))
                    os.replace(staged_path, out_path)
                    if os.name == "posix":
                        os.chmod(out_path, 0o600)
    except (ValueError, zipfile.BadZipFile) as exc:
        MATERIALIZATION_WARNINGS.append(f"Ignored {env_name}: invalid ZIP data ({exc}).")
        return
    os.environ[path_env] = target


def _materialize_deployment_data() -> None:
    private_restricted = _local_restricted_enabled()
    server_only_ids = _configured_server_only_ids()
    if private_restricted:
        _materialize_file_b64(
            "LDFREQ_NJ8_CSV_B64",
            "NJ8.csv",
            "LDFREQ_NJ8_PATH",
        )
        _materialize_file_b64(
            "LDFREQ_ANTBNC_TXT_B64",
            "antbnc_lemmas_ver_004.txt",
            "LDFREQ_ANTBNC_PATH",
        )
        _materialize_zip_b64(
            "LDFREQ_RANGE_ZIP_B64",
            "RangeBaseword",
            "LDFREQ_RANGE_PATH",
        )
    if private_restricted or "bnc_coca" in server_only_ids:
        _materialize_zip_b64(
            "LDFREQ_BNCCOCA_ZIP_B64",
            "NationBNCCOCA",
            "LDFREQ_BNCCOCA_PATH",
        )
    if private_restricted or "nation_bnc_coca_families" in server_only_ids:
        _materialize_zip_b64(
            "LDFREQ_NATION_BNCCOCA_RUNTIME_ZIP_B64",
            "nation_bnc_coca_25000",
            "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
        )


_materialize_deployment_data()


def _path(env: str, default_rel: str) -> str:
    return os.environ.get(env, os.path.join(_ROOT, default_rel))


def _bnc_coca_families_path() -> str:
    if os.environ.get("LDFREQ_BNCCOCA_FAMILIES_PATH"):
        return os.environ["LDFREQ_BNCCOCA_FAMILIES_PATH"]
    base_dir = os.environ.get("LDFREQ_BNCCOCA_PATH", os.path.join(_ROOT, "data", "bnc_coca"))
    return os.path.join(base_dir, "BNC_COCA_lists.xlsx")


def _nation_bnc_coca_index_path() -> str:
    configured_file = os.environ.get("LDFREQ_NATION_BNCCOCA_INDEX_PATH")
    if configured_file:
        return configured_file
    configured_dir = os.environ.get(
        "LDFREQ_NATION_BNCCOCA_INDEX_DIR",
        os.path.join(_ROOT, ".streamlit", "runtime_lists", "nation_bnc_coca_25000"),
    )
    return os.path.join(configured_dir, NATION_BNCCOCA_ARTIFACT_NAME)


def _file_available(path: str) -> bool:
    return os.path.isfile(path)


def _ngsl_available(path: str) -> bool:
    if os.path.isdir(path):
        return os.path.isfile(os.path.join(path, "NGSL_1.2_stats.csv"))
    return os.path.isfile(path)


def _bnc_coca_headwords_available(path: str) -> bool:
    if not os.path.isdir(path):
        return False
    direct = glob.glob(os.path.join(path, "headwords*1000.txt"))
    nested = glob.glob(os.path.join(path, "**", "headwords*1000.txt"), recursive=True)
    return bool(direct or nested)


def _verify_nation_headwords(path: str) -> Path:
    root = Path(path)
    if not root.is_dir():
        raise FileNotFoundError(f"Nation headword directory does not exist: {root}")
    files = {candidate.name: candidate for candidate in root.glob("headwords*1000.txt")}
    if set(files) != set(NATION_HEADWORD_FILES):
        raise ValueError("Nation headword directory does not contain the exact pinned file set")
    for name, (expected_bytes, expected_sha256) in NATION_HEADWORD_FILES.items():
        candidate = files[name]
        if candidate.stat().st_size != expected_bytes:
            raise ValueError(f"Nation headword size mismatch: {name}")
        if sha256_file(candidate) != expected_sha256:
            raise ValueError(f"Nation headword SHA-256 mismatch: {name}")
    return root


def load_verified_nation_headwords(path: str):
    """Load only the ten code-pinned files from Nation's official archive."""

    return load_headword_bands(str(_verify_nation_headwords(path)))


def _verified_nation_headwords_available(path: str) -> bool:
    try:
        _verify_nation_headwords(path)
    except (OSError, ValueError):
        return False
    return True


def _range_available(path: str) -> bool:
    if os.path.isfile(path):
        return True
    if not os.path.isdir(path):
        return False
    patterns = ("basewrd*", "baseword*", "[0-9]*.txt", "*.txt")
    return any(glob.glob(os.path.join(path, pattern)) for pattern in patterns)


# Loader registry: each entry's ``loader(path) -> (rank_map, meta)``.
REGISTRY = [
    {
        "id": "nj8",
        "registry_id": "nj8",
        "name": "New JACET8000",
        "path": _path("LDFREQ_NJ8_PATH", "data/NJ8/NJ8.csv"),
        "loader": load_ranked_list,
        "available": _file_available,
        "redistributable": False,
        "public_web": False,
        "license": "© JACET; independent permission review pending (local-only)",
        "source_url": "https://mizumot.com/nwlc/",
        "unit": "ranked spelling entries with listed variants (POS-less; not pre-grouped flemmas or families)",
    },
    {
        "id": "ngsl",
        "registry_id": "ngsl-1.2",
        "name": "NGSL (New General Service List)",
        "path": _path("LDFREQ_NGSL_PATH", "data/ngsl"),
        "loader": load_ngsl,
        "available": _ngsl_available,
        "redistributable": True,
        "public_web": True,
        "license": "CC BY-SA 4.0 (Browne, Culligan & Phillips)",
        "source_url": "https://www.newgeneralservicelist.com/",
        "unit": "lemma ranks with supplied inflected-form aliases",
    },
    {
        "id": "nation_bnc_coca_families",
        "registry_id": "nation-bnc-coca-families-25000",
        "name": "BNC/COCA 25,000 word families (Nation)",
        "path": _nation_bnc_coca_index_path(),
        "loader": load_nation_bnc_coca_index,
        "available": _file_available,
        "redistributable": False,
        "public_web": False,
        "server_only_eligible": True,
        "license": "CC BY-SA 4.0 (Paul Nation / Te Herenga Waka)",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": (
            "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/"
            "vocabulary-analysis-programs/range/BNC_COCA_25000.zip"
        ),
        "modification_notice": (
            "Derived by selecting official basewrd1-25, normalizing case and the "
            "Range trailing-zero marker, and mapping forms to family heads."
        ),
        "unit": "word families with related forms (official basewrd1-25)",
    },
    {
        "id": "bnc_coca_families",
        "registry_id": "eapfoundation-bnc-coca-v2",
        "name": "BNC/COCA word families (EAPFoundation v2)",
        "path": _bnc_coca_families_path(),
        "loader": load_bnc_coca_families_xlsx,
        "available": _file_available,
        "redistributable": False,
        "public_web": False,
        "server_only_eligible": False,
        "license": "EAPFoundation educational/non-commercial terms (permission-pending for public SaaS)",
        "source_url": "https://www.eapfoundation.com/vocab/general/bnccoca/index.php?type=v2",
        "unit": "word families with related forms",
    },
    {
        "id": "bnc_coca",
        "registry_id": "nation-bnc-coca-headwords-10000",
        "name": "BNC/COCA 10,000 headwords (Nation)",
        "path": _path("LDFREQ_BNCCOCA_PATH", "data/bnc_coca"),
        "loader": load_verified_nation_headwords,
        "available": _verified_nation_headwords_available,
        "redistributable": False,
        "public_web": False,
        "server_only_eligible": True,
        "license": "CC BY-SA 4.0 (Paul Nation / Te Herenga Waka)",
        "license_url": "https://creativecommons.org/licenses/by-sa/4.0/",
        "source_url": "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/paul-nations-publications/publications/documents/10000-headwords.zip",
        "modification_notice": "Loaded from the official ten-band archive without content changes.",
        "unit": "ranked headword entries only (not flemmas or word families)",
    },
    {
        "id": "range_baseword",
        "registry_id": None,
        "name": "AntWordProfiler/Range baseword lists",
        "path": _path("LDFREQ_RANGE_PATH", "data/range"),
        "loader": load_range_baseword_lists,
        "available": _range_available,
        "redistributable": False,
        "public_web": False,
        "license": "User-supplied Range/AntWordProfiler level-list files",
        "source_url": "https://www.laurenceanthony.net/software/antwordprofiler/",
        "unit": "Range-style baseword families",
    },
]


def by_id(list_id: str):
    return next((e for e in REGISTRY if e["id"] == list_id), None)


def _entry_available(entry) -> bool:
    checker = entry.get("available")
    if checker:
        return bool(checker(entry["path"]))
    return os.path.exists(entry["path"])


def server_only_resource_ids() -> frozenset[str]:
    """Return explicitly enabled non-deliverable server resources.

    Enabling requires an eligible allow-list, rights acknowledgement, the fixed
    control profile, and a valid external-evidence reference. This engineering
    gate validates declarations only; it does not verify rights or shared
    infrastructure.
    """

    return _configured_server_only_ids()


def server_only_enabled(resource_id: str) -> bool:
    """Whether the operator explicitly enabled one eligible server resource."""

    return resource_id in server_only_resource_ids()


def available(*, include_restricted: bool | None = None):
    """Return installed entries allowed by the deployment's activation mode."""
    if include_restricted is None:
        include_restricted = _local_restricted_enabled()
    else:
        include_restricted = bool(include_restricted) and _local_restricted_enabled()
    server_only_ids = server_only_resource_ids()
    return [
        entry
        for entry in REGISTRY
        if (
            entry.get("public_web", False)
            or include_restricted
            or (
                entry.get("server_only_eligible", False)
                and entry["id"] in server_only_ids
            )
        )
        and _entry_available(entry)
    ]


def available_by_id(list_id: str, *, include_restricted: bool = False):
    """Return one installed, rights-gated entry without probing other data.

    This targeted lookup is used by the isolated Web worker so requesting NJ8,
    for example, never hashes or parses an unrelated operator-only resource.
    """

    include_restricted = bool(include_restricted) and _local_restricted_enabled()
    entry = by_id(list_id)
    if entry is None:
        return None
    server_only_ids = server_only_resource_ids()
    allowed = (
        entry.get("public_web", False)
        or include_restricted
        or (
            entry.get("server_only_eligible", False)
            and entry["id"] in server_only_ids
        )
    )
    if not allowed or not _entry_available(entry):
        return None
    return entry


def restricted_installed():
    """Return installed resources blocked from the public-Web selector."""
    return [
        entry
        for entry in REGISTRY
        if _entry_available(entry) and not entry.get("public_web", False)
    ]


def not_installed():
    return [e for e in REGISTRY if not _entry_available(e)]
