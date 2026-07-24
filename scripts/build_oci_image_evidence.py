#!/usr/bin/env python3
"""Validate one BuildKit OCI archive and emit canonical image evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_CONFIG = "application/vnd.oci.image.config.v1+json"
OCI_LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
}


class OCIImageEvidenceError(ValueError):
    """Raised when an OCI archive or its BuildKit metadata is inconsistent."""


def _regular_file(path: Path, *, label: str) -> Path:
    if not path.is_file() or path.is_symlink():
        raise OCIImageEvidenceError(f"{label} is not a regular file: {path}")
    return path


def _hash_stream(stream: BinaryIO) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        size += len(chunk)
        digest.update(chunk)
    return size, digest.hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    path = _regular_file(path, label="evidence input")
    with path.open("rb") as stream:
        size, digest = _hash_stream(stream)
    return {"filename": path.name, "bytes": size, "sha256": digest}


def _json_object(payload: bytes, *, label: str) -> dict[str, Any]:
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OCIImageEvidenceError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise OCIImageEvidenceError(f"{label} must contain one JSON object")
    return document


def _digest(value: object, *, label: str) -> str:
    normalized = str(value)
    if not DIGEST.fullmatch(normalized):
        raise OCIImageEvidenceError(f"{label} must be one SHA-256 digest")
    return normalized


def _blob_name(digest: str) -> str:
    return f"blobs/sha256/{digest.removeprefix('sha256:')}"


def _safe_members(archive: tarfile.TarFile) -> dict[str, tarfile.TarInfo]:
    members: dict[str, tarfile.TarInfo] = {}
    for member in archive.getmembers():
        path = PurePosixPath(member.name)
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise OCIImageEvidenceError(f"unsafe OCI archive member: {member.name}")
        name = path.as_posix().rstrip("/")
        if name in members:
            raise OCIImageEvidenceError(f"duplicate OCI archive member: {name}")
        if member.issym() or member.islnk() or member.isdev():
            raise OCIImageEvidenceError(f"unsupported OCI archive member: {name}")
        members[name] = member
    return members


def _member_bytes(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    name: str,
    *,
    label: str,
) -> bytes:
    member = members.get(name)
    if member is None or not member.isfile():
        raise OCIImageEvidenceError(f"OCI archive lacks {label}: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise OCIImageEvidenceError(f"OCI archive cannot read {label}: {name}")
    return stream.read()


def _verify_blob(
    archive: tarfile.TarFile,
    members: dict[str, tarfile.TarInfo],
    digest: str,
    *,
    label: str,
) -> int:
    name = _blob_name(digest)
    member = members.get(name)
    if member is None or not member.isfile():
        raise OCIImageEvidenceError(f"OCI archive lacks {label} blob: {name}")
    stream = archive.extractfile(member)
    if stream is None:
        raise OCIImageEvidenceError(f"OCI archive cannot read {label} blob: {name}")
    size, actual = _hash_stream(stream)
    if f"sha256:{actual}" != digest:
        raise OCIImageEvidenceError(f"{label} blob digest mismatch")
    return size


def _epoch(value: object, *, label: str) -> int:
    text = str(value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OCIImageEvidenceError(f"{label} is not an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise OCIImageEvidenceError(f"{label} must include a timezone")
    return int(parsed.astimezone(timezone.utc).timestamp())


def inspect_oci_image(
    archive_path: Path,
    metadata_path: Path,
    *,
    source_date_epoch: int,
) -> dict[str, Any]:
    if source_date_epoch <= 0:
        raise OCIImageEvidenceError("SOURCE_DATE_EPOCH must be a positive integer")
    archive_identity = file_identity(archive_path)
    metadata_identity = file_identity(metadata_path)
    metadata = _json_object(metadata_path.read_bytes(), label="BuildKit metadata")
    manifest_digest = _digest(
        metadata.get("containerimage.digest"),
        label="BuildKit image manifest digest",
    )
    config_digest = _digest(
        metadata.get("containerimage.config.digest"),
        label="BuildKit image config digest",
    )
    provenance = metadata.get("buildx.build.provenance")
    if not isinstance(provenance, dict):
        raise OCIImageEvidenceError("BuildKit metadata lacks provenance")
    invocation = provenance.get("invocation")
    if not isinstance(invocation, dict):
        raise OCIImageEvidenceError("BuildKit provenance lacks invocation")
    parameters = invocation.get("parameters")
    environment = invocation.get("environment")
    if not isinstance(parameters, dict) or not isinstance(environment, dict):
        raise OCIImageEvidenceError("BuildKit invocation is incomplete")
    arguments = parameters.get("args")
    if not isinstance(arguments, dict):
        raise OCIImageEvidenceError("BuildKit invocation lacks build arguments")
    if str(arguments.get("build-arg:SOURCE_DATE_EPOCH")) != str(source_date_epoch):
        raise OCIImageEvidenceError("BuildKit SOURCE_DATE_EPOCH differs from evidence")
    if environment.get("platform") != "linux/amd64":
        raise OCIImageEvidenceError("BuildKit platform must be linux/amd64")

    archive_path = _regular_file(archive_path, label="OCI archive")
    try:
        opened = tarfile.open(archive_path, mode="r:*")
    except (OSError, tarfile.TarError) as exc:
        raise OCIImageEvidenceError("OCI archive is unreadable") from exc
    with opened as archive:
        members = _safe_members(archive)
        layout = _json_object(
            _member_bytes(archive, members, "oci-layout", label="OCI layout"),
            label="OCI layout",
        )
        if layout.get("imageLayoutVersion") != "1.0.0":
            raise OCIImageEvidenceError("OCI layout version must be 1.0.0")
        index = _json_object(
            _member_bytes(archive, members, "index.json", label="OCI index"),
            label="OCI index",
        )
        descriptors = index.get("manifests")
        if index.get("schemaVersion") != 2 or not isinstance(descriptors, list):
            raise OCIImageEvidenceError("OCI index is invalid")
        if len(descriptors) != 1 or not isinstance(descriptors[0], dict):
            raise OCIImageEvidenceError("OCI index must contain one image manifest")
        descriptor = descriptors[0]
        if descriptor.get("mediaType") != OCI_MANIFEST:
            raise OCIImageEvidenceError("OCI index manifest media type is invalid")
        if (
            _digest(descriptor.get("digest"), label="OCI index manifest digest")
            != manifest_digest
        ):
            raise OCIImageEvidenceError("OCI index and BuildKit manifest digests differ")
        manifest_bytes = _member_bytes(
            archive,
            members,
            _blob_name(manifest_digest),
            label="image manifest",
        )
        if f"sha256:{hashlib.sha256(manifest_bytes).hexdigest()}" != manifest_digest:
            raise OCIImageEvidenceError("OCI image manifest blob digest mismatch")
        if descriptor.get("size") != len(manifest_bytes):
            raise OCIImageEvidenceError("OCI index manifest size differs from its blob")
        manifest = _json_object(manifest_bytes, label="OCI image manifest")
        if manifest.get("schemaVersion") != 2 or manifest.get("mediaType") != OCI_MANIFEST:
            raise OCIImageEvidenceError("OCI image manifest is invalid")
        config = manifest.get("config")
        layers = manifest.get("layers")
        if not isinstance(config, dict) or not isinstance(layers, list) or not layers:
            raise OCIImageEvidenceError("OCI image manifest content is incomplete")
        if config.get("mediaType") != OCI_CONFIG:
            raise OCIImageEvidenceError("OCI image config media type is invalid")
        if _digest(config.get("digest"), label="OCI config digest") != config_digest:
            raise OCIImageEvidenceError("OCI manifest and BuildKit config digests differ")
        config_bytes = _member_bytes(
            archive,
            members,
            _blob_name(config_digest),
            label="image config",
        )
        if f"sha256:{hashlib.sha256(config_bytes).hexdigest()}" != config_digest:
            raise OCIImageEvidenceError("OCI image config blob digest mismatch")
        if config.get("size") != len(config_bytes):
            raise OCIImageEvidenceError("OCI image config size differs from its blob")
        config_document = _json_object(config_bytes, label="OCI image config")
        if config_document.get("architecture") != "amd64" or config_document.get("os") != "linux":
            raise OCIImageEvidenceError("OCI image config platform must be linux/amd64")
        if (
            _epoch(config_document.get("created"), label="OCI image created")
            != source_date_epoch
        ):
            raise OCIImageEvidenceError(
                "OCI image created timestamp differs from SOURCE_DATE_EPOCH"
            )
        history = config_document.get("history")
        if not isinstance(history, list):
            raise OCIImageEvidenceError("OCI image config history is missing")
        for item in history:
            if not isinstance(item, dict):
                raise OCIImageEvidenceError("OCI image history contains an invalid entry")
            created = item.get("created")
            if (
                created is not None
                and _epoch(created, label="OCI history created") > source_date_epoch
            ):
                raise OCIImageEvidenceError("OCI image history exceeds SOURCE_DATE_EPOCH")
        rootfs = config_document.get("rootfs")
        diff_ids = rootfs.get("diff_ids") if isinstance(rootfs, dict) else None
        if not isinstance(diff_ids, list) or len(diff_ids) != len(layers):
            raise OCIImageEvidenceError("OCI image rootfs and layer counts differ")
        layer_digests: list[str] = []
        layer_sizes: list[int] = []
        for index_value, layer in enumerate(layers):
            if not isinstance(layer, dict):
                raise OCIImageEvidenceError("OCI image manifest contains an invalid layer")
            if layer.get("mediaType") not in OCI_LAYER_MEDIA_TYPES:
                raise OCIImageEvidenceError(
                    f"OCI layer {index_value} media type is unsupported"
                )
            layer_digest = _digest(
                layer.get("digest"),
                label=f"OCI layer {index_value} digest",
            )
            layer_digests.append(layer_digest)
            layer_size = _verify_blob(
                archive,
                members,
                layer_digest,
                label=f"OCI layer {index_value}",
            )
            if layer.get("size") != layer_size:
                raise OCIImageEvidenceError(
                    f"OCI layer {index_value} size differs from its blob"
                )
            layer_sizes.append(layer_size)

    return {
        "oci_image_evidence_schema_version": 1,
        "status": "validated-oci-image",
        "platform": "linux/amd64",
        "source_date_epoch": source_date_epoch,
        "archive": archive_identity,
        "build_metadata": metadata_identity,
        "image": {
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "created": config_document["created"],
            "layer_count": len(layer_digests),
            "layer_digests": layer_digests,
            "layer_blob_bytes": layer_sizes,
            "rootfs_diff_ids": [
                _digest(value, label=f"rootfs diff ID {index_value}")
                for index_value, value in enumerate(diff_ids)
            ],
        },
    }


def canonical_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            indent=2,
        )
        + "\n"
    ).encode("utf-8")


def write_exclusive(path: Path, payload: bytes) -> None:
    try:
        with path.open("xb") as output:
            output.write(payload)
    except FileExistsError as exc:
        raise OCIImageEvidenceError(f"refusing to overwrite existing output: {path}") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--source-date-epoch", required=True, type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = inspect_oci_image(
            args.archive,
            args.metadata,
            source_date_epoch=args.source_date_epoch,
        )
        payload = canonical_json(document)
        write_exclusive(args.output, payload)
    except Exception as exc:
        print(f"OCI image evidence: BLOCKED\n- {exc}", file=sys.stderr)
        return 1
    print(
        "OCI image evidence: PASS "
        f"({len(document['image']['layer_digests'])} layers; "
        f"{document['image']['manifest_digest']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
