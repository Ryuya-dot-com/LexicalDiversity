"""Upload decoding helpers for text and ZIP batches."""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
import io
import zipfile


DEFAULT_MAX_ARCHIVE_MEMBERS = 1_000


def _upload_bytes(upload: Any) -> bytes:
    if hasattr(upload, "getvalue"):
        return upload.getvalue()
    if hasattr(upload, "read"):
        return upload.read()
    return bytes(upload)


def _safe_member_name(name: str) -> str:
    normalized = name.replace("\\", "/")
    parts = [
        part for part in PurePosixPath(normalized).parts
        if part not in {"", "/", ".", ".."}
    ]
    return "/".join(parts) or "file.txt"


def documents_from_uploads(
    uploads,
    *,
    max_file_bytes: int,
    max_total_bytes: int,
    max_documents: int,
    max_archive_bytes: int | None = None,
    max_compression_ratio: float = 200.0,
    max_archive_members: int = DEFAULT_MAX_ARCHIVE_MEMBERS,
    max_archive_uncompressed_bytes: int | None = None,
    redact_names: bool = False,
) -> tuple[list[dict], list[str]]:
    """Return decoded text documents and warnings from uploaded .txt/.zip files."""
    docs: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, int] = {}
    total_bytes = 0

    def unique_name(name: str) -> str:
        seen[name] = seen.get(name, 0) + 1
        return name if seen[name] == 1 else f"{name} ({seen[name]})"

    def add_doc(
        name: str,
        payload: bytes,
        source: str,
        *,
        warning_name: str | None = None,
    ) -> None:
        nonlocal total_bytes
        label = warning_name or name
        if len(docs) >= max_documents:
            warnings.append(f"Skipped {label}: document limit reached ({max_documents}).")
            return
        if len(payload) > max_file_bytes:
            warnings.append(
                f"Skipped {label}: {len(payload):,} bytes exceeds per-file limit "
                f"({max_file_bytes:,} bytes)."
            )
            return
        if total_bytes + len(payload) > max_total_bytes:
            warnings.append(
                f"Skipped {label}: total extracted text limit would exceed "
                f"{max_total_bytes:,} bytes."
            )
            return
        total_bytes += len(payload)
        docs.append({
            "name": unique_name(name),
            "text": payload.decode("utf-8", errors="replace"),
            "source": source,
            "size_bytes": len(payload),
        })

    archive_limit = max_archive_bytes
    archive_uncompressed_limit = (
        max_total_bytes
        if max_archive_uncompressed_bytes is None
        else max_archive_uncompressed_bytes
    )

    for upload_index, upload in enumerate(uploads or [], start=1):
        upload_name = getattr(upload, "name", None) or "uploaded"
        upload_label = f"Upload {upload_index:03d}" if redact_names else upload_name
        upload_data = _upload_bytes(upload)
        lower_name = upload_name.lower()

        if lower_name.endswith(".txt"):
            add_doc(
                upload_name,
                upload_data,
                "upload",
                warning_name=upload_label,
            )
            continue

        if lower_name.endswith(".zip"):
            if archive_limit is not None and len(upload_data) > archive_limit:
                warnings.append(
                    f"Skipped {upload_label}: archive size {len(upload_data):,} bytes "
                    f"exceeds limit ({archive_limit:,} bytes)."
                )
                continue
            try:
                with zipfile.ZipFile(io.BytesIO(upload_data)) as zf:
                    members = [info for info in zf.infolist() if not info.is_dir()]
                    if len(members) > max_archive_members:
                        warnings.append(
                            f"Skipped {upload_label}: ZIP member count {len(members):,} "
                            f"exceeds safety limit ({max_archive_members:,})."
                        )
                        continue
                    txt_members = [
                        info for info in members
                        if _safe_member_name(info.filename).lower().endswith(".txt")
                    ]
                    if not txt_members:
                        warnings.append(f"Skipped {upload_label}: ZIP contains no .txt files.")
                        continue
                    declared_uncompressed_bytes = sum(
                        max(0, info.file_size) for info in txt_members
                    )
                    if declared_uncompressed_bytes > archive_uncompressed_limit:
                        warnings.append(
                            f"Skipped {upload_label}: declared uncompressed text size "
                            f"{declared_uncompressed_bytes:,} bytes exceeds safety limit "
                            f"({archive_uncompressed_limit:,} bytes)."
                        )
                        continue
                    for member_index, info in enumerate(txt_members, start=1):
                        member_name = _safe_member_name(info.filename)
                        member_label = (
                            f"{upload_label}:text member {member_index:03d}"
                            if redact_names
                            else f"{upload_name}:{member_name}"
                        )
                        if len(docs) >= max_documents:
                            warnings.append(
                                f"Stopped {upload_label}: document limit reached "
                                f"({max_documents}); remaining ZIP members were not opened."
                            )
                            break
                        if info.file_size > max_file_bytes:
                            warnings.append(
                                f"Skipped {member_label}: "
                                f"{info.file_size:,} bytes exceeds per-file limit "
                                f"({max_file_bytes:,} bytes)."
                            )
                            continue
                        if total_bytes + info.file_size > max_total_bytes:
                            warnings.append(
                                f"Skipped {member_label}: total extracted text limit would "
                                f"exceed {max_total_bytes:,} bytes; member was not opened."
                            )
                            continue
                        ratio = info.file_size / max(info.compress_size, 1)
                        if ratio > max_compression_ratio:
                            warnings.append(
                                f"Skipped {member_label}: ZIP compression ratio "
                                f"{ratio:.1f}:1 exceeds limit "
                                f"({max_compression_ratio:.1f}:1)."
                            )
                            continue
                        with zf.open(info) as fh:
                            payload = fh.read(max_file_bytes + 1)
                            add_doc(
                                f"{upload_name}:{member_name}",
                                payload,
                                "zip",
                                warning_name=member_label,
                            )
            except zipfile.BadZipFile:
                warnings.append(f"Skipped {upload_label}: invalid ZIP file.")
            continue

        warnings.append(f"Skipped {upload_label}: only .txt and .zip files are supported.")

    return docs, warnings
