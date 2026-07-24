import json
import gzip
import io
import tarfile
from pathlib import Path

import pytest

import ldfreq
from scripts import check_version_contract as versions
from scripts import build_release_evidence as evidence
from scripts import build_release_archive as archive


ROOT = Path(__file__).resolve().parents[1]


def _identity() -> dict:
    return json.loads(versions.IDENTITY_PATH.read_text(encoding="utf-8"))


def _scope() -> dict:
    return json.loads(versions.SCOPE_PATH.read_text(encoding="utf-8"))


def test_release_json_is_the_single_imported_version_authority():
    identity = _identity()

    assert identity["application_version"] == "0.9.0-dev.0"
    assert identity["output_schema_version"] == "1.0.0"
    assert identity["release_phase"] == "development"
    assert ldfreq.__version__ == identity["application_version"]
    assert ldfreq.OUTPUT_SCHEMA_VERSION == identity["output_schema_version"]
    assert ldfreq.TARGET_APPLICATION_RELEASE == identity["target_application_release"]
    with pytest.raises(TypeError):
        ldfreq.RELEASE_IDENTITY["application_version"] = "9.9.9"


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        ("0.9.0-dev.0", True),
        ("0.9.0-rc.1", True),
        ("1.0.0", True),
        ("1.0.0+build.7", True),
        ("01.0.0", False),
        ("1.0", False),
        ("1.0.0-01", False),
        ("v1.0.0", False),
        ("1.0.0_1", False),
    ],
)
def test_semver_parser_is_strict(value: str, valid: bool):
    assert versions.is_semver(value) is valid


def test_development_contract_matches_scope_changelog_and_attributes():
    violations, identity = versions.development_violations()

    assert violations == []
    assert identity == _identity()


@pytest.mark.parametrize(
    ("phase", "application", "message"),
    [
        ("development", "0.9.0-rc.1", "development version"),
        ("release-candidate", "0.9.0-dev.0", "release-candidate version"),
        ("stable", "1.0.0-rc.1", "stable version"),
    ],
)
def test_release_phase_and_semver_cannot_disagree(
    phase: str,
    application: str,
    message: str,
):
    identity = _identity()
    identity["release_phase"] = phase
    identity["application_version"] = application
    violations = versions.contract_violations(
        identity,
        _scope(),
        versions.CHANGELOG_PATH.read_text(encoding="utf-8"),
        versions.ATTRIBUTES_PATH.read_text(encoding="utf-8"),
    )

    assert any(message in violation for violation in violations)


def test_unknown_identity_field_is_release_blocking():
    identity = _identity()
    identity["unreviewed"] = True
    violations = versions.contract_violations(
        identity,
        _scope(),
        versions.CHANGELOG_PATH.read_text(encoding="utf-8"),
        versions.ATTRIBUTES_PATH.read_text(encoding="utf-8"),
    )

    assert "release identity keys differ from the reviewed schema" in violations


def test_release_evidence_is_canonical_and_distinguishes_oci_image_identity(
    tmp_path: Path,
):
    image_config_digest = "sha256:" + "a" * 64
    image_manifest_digest = "sha256:" + "b" * 64
    layer_digest = "sha256:" + "c" * 64
    source_archive = tmp_path / "source.tar.gz"
    source_archive.write_bytes(b"source archive fixture")
    oci_image_evidence = tmp_path / "release-oci-evidence.json"
    oci_image_evidence.write_text(
        json.dumps(
            {
                "oci_image_evidence_schema_version": 1,
                "status": "validated-oci-image",
                "platform": "linux/amd64",
                "source_date_epoch": 1784870000,
                "image": {
                    "config_digest": image_config_digest,
                    "manifest_digest": image_manifest_digest,
                    "layer_count": 1,
                    "layer_digests": [layer_digest],
                },
            }
        ),
        encoding="utf-8",
    )
    first = evidence.canonical_json(
        evidence.build_evidence(
            _identity(),
            image_config_digest=image_config_digest,
            image_manifest_digest=image_manifest_digest,
            oci_image_evidence=oci_image_evidence,
            source_archive=source_archive,
        )
    )
    second = evidence.canonical_json(
        evidence.build_evidence(
            _identity(),
            image_config_digest=image_config_digest,
            image_manifest_digest=image_manifest_digest,
            oci_image_evidence=oci_image_evidence,
            source_archive=source_archive,
        )
    )
    document = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert document["application_version"] == ldfreq.__version__
    assert document["source"]["commit"]
    assert document["source"]["tree"]
    assert document["source"]["archive"] == {
        "filename": "source.tar.gz",
        "bytes": 22,
        "sha256": "eb1a20133f0683368b8fe8524740c1ed1042e839a23e850a1c57d7a24a032129",
    }
    assert document["application_image"] == {
        "platform": "linux/amd64",
        "config_digest": image_config_digest,
        "manifest_digest": image_manifest_digest,
        "layer_digests": [layer_digest],
        "oci_image_evidence": evidence.external_file_identity(oci_image_evidence),
        "registry_manifest_digest": None,
        "status": "OCI image verified; registry publication pending",
    }
    assert set(document["build_inputs"]["files"]) == {
        path.as_posix() for path in evidence.EVIDENCE_PATHS
    }


def test_release_archive_gzip_is_byte_deterministic_with_fixed_header():
    payload = b"canonical tagged source\n" * 50

    first = archive.deterministic_gzip(payload)
    second = archive.deterministic_gzip(payload)

    assert first == second
    assert first[:10] == b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x02\xff"
    assert gzip.decompress(first) == payload


def _tar_payload(
    *,
    link: bool = False,
    member_name: str = "LexicalDiversity-1.0.0/README.md",
) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as output:
        member = tarfile.TarInfo(member_name)
        if link:
            member.type = tarfile.SYMTYPE
            member.linkname = "/private/source"
            output.addfile(member)
        else:
            payload = b"public"
            member.size = len(payload)
            output.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def test_release_archive_inventory_strips_one_prefix_and_rejects_links():
    assert archive.archived_paths(
        _tar_payload(), prefix="LexicalDiversity-1.0.0/"
    ) == ["README.md"]

    with pytest.raises(archive.ReleaseArchiveError, match="links are prohibited"):
        archive.archived_paths(
            _tar_payload(link=True), prefix="LexicalDiversity-1.0.0/"
        )

    with pytest.raises(archive.ReleaseArchiveError, match="unsafe archive path"):
        archive.archived_paths(
            _tar_payload(
                member_name="LexicalDiversity-1.0.0/../outside.txt"
            ),
            prefix="LexicalDiversity-1.0.0/",
        )


def test_release_archive_success_path_is_byte_reproducible(
    monkeypatch: pytest.MonkeyPatch,
):
    identity = _identity()
    prefix = f"LexicalDiversity-{identity['application_version']}/"
    paths = sorted(archive.REQUIRED_ARCHIVE_FILES)
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as output:
        for path in paths:
            payload = path.encode("utf-8")
            member = tarfile.TarInfo(prefix + path)
            member.size = len(payload)
            member.mtime = 0
            output.addfile(member, io.BytesIO(payload))
    tar_payload = buffer.getvalue()

    monkeypatch.setattr(archive, "_git_bytes", lambda *arguments: tar_payload)
    monkeypatch.setattr(
        archive,
        "_git_text",
        lambda *arguments: "\n".join(paths),
    )
    monkeypatch.setattr(
        archive,
        "release_violations",
        lambda registry, tracked_paths: [],
    )

    first = archive.build_archive(identity)
    second = archive.build_archive(identity)

    assert first == second
    assert gzip.decompress(first) == tar_payload
    assert archive.archived_paths(tar_payload, prefix=prefix) == paths


def test_release_artifact_writers_refuse_to_replace_existing_files(
    tmp_path: Path,
):
    archive_path = tmp_path / "source.tar.gz"
    evidence_path = tmp_path / "evidence.json"
    archive.write_exclusive(archive_path, b"first archive")
    evidence.write_exclusive(evidence_path, b"first evidence")

    with pytest.raises(archive.ReleaseArchiveError, match="refusing to overwrite"):
        archive.write_exclusive(archive_path, b"replacement")
    with pytest.raises(versions.VersionContractError, match="refusing to overwrite"):
        evidence.write_exclusive(evidence_path, b"replacement")

    assert archive_path.read_bytes() == b"first archive"
    assert evidence_path.read_bytes() == b"first evidence"
