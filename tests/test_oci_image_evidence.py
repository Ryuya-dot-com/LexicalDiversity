import hashlib
import io
import json
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scripts import build_oci_image_evidence as evidence


EPOCH = 1784884991


def _json_bytes(document: dict) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:") as archive:
        for name, payload in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mtime = 0
            archive.addfile(member, io.BytesIO(payload))
    return output.getvalue()


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    layer = _tar_bytes({"opt/ldfreq/example.txt": b"reviewed\n"})
    layer_digest = _digest(layer)
    created = datetime.fromtimestamp(EPOCH, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    config = _json_bytes(
        {
            "architecture": "amd64",
            "os": "linux",
            "created": created,
            "history": [{"created": created, "created_by": "COPY example.txt"}],
            "rootfs": {"type": "layers", "diff_ids": [layer_digest]},
            "config": {"User": "10001:10001"},
        }
    )
    config_digest = _digest(config)
    manifest = _json_bytes(
        {
            "schemaVersion": 2,
            "mediaType": evidence.OCI_MANIFEST,
            "config": {
                "mediaType": evidence.OCI_CONFIG,
                "digest": config_digest,
                "size": len(config),
            },
            "layers": [
                {
                    "mediaType": "application/vnd.oci.image.layer.v1.tar",
                    "digest": layer_digest,
                    "size": len(layer),
                }
            ],
        }
    )
    manifest_digest = _digest(manifest)
    index = _json_bytes(
        {
            "schemaVersion": 2,
            "manifests": [
                {
                    "mediaType": evidence.OCI_MANIFEST,
                    "digest": manifest_digest,
                    "size": len(manifest),
                    "platform": {"architecture": "amd64", "os": "linux"},
                }
            ],
        }
    )
    layout = _json_bytes({"imageLayoutVersion": "1.0.0"})
    archive = tmp_path / "image.oci.tar"
    archive.write_bytes(
        _tar_bytes(
            {
                "oci-layout": layout,
                "index.json": index,
                f"blobs/sha256/{layer_digest.removeprefix('sha256:')}": layer,
                f"blobs/sha256/{config_digest.removeprefix('sha256:')}": config,
                f"blobs/sha256/{manifest_digest.removeprefix('sha256:')}": manifest,
            }
        )
    )
    metadata = tmp_path / "metadata.json"
    metadata.write_text(
        json.dumps(
            {
                "containerimage.digest": manifest_digest,
                "containerimage.config.digest": config_digest,
                "buildx.build.provenance": {
                    "invocation": {
                        "environment": {"platform": "linux/amd64"},
                        "parameters": {
                            "args": {"build-arg:SOURCE_DATE_EPOCH": str(EPOCH)}
                        },
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return archive, metadata, {
        "manifest": manifest_digest,
        "config": config_digest,
        "layer": layer_digest,
    }


def test_oci_archive_evidence_validates_all_content_digests(tmp_path: Path):
    archive, metadata, digests = _fixture(tmp_path)

    document = evidence.inspect_oci_image(
        archive,
        metadata,
        source_date_epoch=EPOCH,
    )

    assert document["status"] == "validated-oci-image"
    assert document["platform"] == "linux/amd64"
    assert document["source_date_epoch"] == EPOCH
    assert document["image"]["manifest_digest"] == digests["manifest"]
    assert document["image"]["config_digest"] == digests["config"]
    assert document["image"]["layer_digests"] == [digests["layer"]]
    assert document["image"]["rootfs_diff_ids"] == [digests["layer"]]
    assert document["image"]["layer_count"] == 1
    assert evidence.canonical_json(document).endswith(b"\n")


def test_oci_archive_evidence_rejects_metadata_digest_mismatch(tmp_path: Path):
    archive, metadata, _digests = _fixture(tmp_path)
    document = json.loads(metadata.read_text(encoding="utf-8"))
    document["containerimage.digest"] = "sha256:" + "f" * 64
    metadata.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(evidence.OCIImageEvidenceError, match="index and BuildKit"):
        evidence.inspect_oci_image(archive, metadata, source_date_epoch=EPOCH)


def test_oci_archive_evidence_rejects_wrong_source_date_epoch(tmp_path: Path):
    archive, metadata, _digests = _fixture(tmp_path)

    with pytest.raises(evidence.OCIImageEvidenceError, match="SOURCE_DATE_EPOCH differs"):
        evidence.inspect_oci_image(archive, metadata, source_date_epoch=EPOCH + 1)


def test_oci_archive_evidence_rejects_changed_layer_blob(tmp_path: Path):
    archive, metadata, digests = _fixture(tmp_path)
    with tarfile.open(archive, mode="r:") as source:
        files = {
            member.name: source.extractfile(member).read()
            for member in source.getmembers()
            if member.isfile()
        }
    files[
        f"blobs/sha256/{digests['layer'].removeprefix('sha256:')}"
    ] = b"changed layer"
    archive.write_bytes(_tar_bytes(files))

    with pytest.raises(evidence.OCIImageEvidenceError, match="layer 0 blob digest"):
        evidence.inspect_oci_image(archive, metadata, source_date_epoch=EPOCH)


def test_oci_archive_evidence_rejects_links(tmp_path: Path):
    archive, metadata, _digests = _fixture(tmp_path)
    with tarfile.open(archive, mode="a:") as output:
        link = tarfile.TarInfo("unsafe-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/private/input"
        output.addfile(link)

    with pytest.raises(evidence.OCIImageEvidenceError, match="unsupported OCI"):
        evidence.inspect_oci_image(archive, metadata, source_date_epoch=EPOCH)


def test_oci_archive_evidence_writer_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "evidence.json"
    evidence.write_exclusive(output, b"first")

    with pytest.raises(evidence.OCIImageEvidenceError, match="overwrite"):
        evidence.write_exclusive(output, b"second")
