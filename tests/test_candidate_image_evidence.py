import hashlib
import json
from pathlib import Path

import pytest

from scripts import build_candidate_image_evidence as evidence


ROOT = Path(__file__).resolve().parents[1]


def _identity() -> dict:
    return json.loads((ROOT / "ldfreq" / "release.json").read_text(encoding="utf-8"))


def _file_identity(path: Path) -> dict:
    payload = path.read_bytes()
    return {
        "filename": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _reports(tmp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
    config_digest = "sha256:" + "a" * 64
    layer_digest = "sha256:" + "c" * 64
    registry = tmp_path / "registry-manifest.json"
    registry.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": config_digest},
                "layers": [{"digest": layer_digest}],
            }
        ),
        encoding="utf-8",
    )
    manifest_digest = "sha256:" + hashlib.sha256(registry.read_bytes()).hexdigest()
    sbom = tmp_path / "sbom.spdx.json"
    sbom.write_text(
        json.dumps({"spdxVersion": "SPDX-2.3", "packages": []}),
        encoding="utf-8",
    )
    scan = tmp_path / "grype.json"
    scan.write_text(
        json.dumps(
            {
                "matches": [
                    {"vulnerability": {"id": "CVE-TEST-1", "severity": "High"}},
                    {"vulnerability": {"id": "CVE-TEST-2", "severity": "Low"}},
                    {"vulnerability": {"id": "CVE-TEST-3", "severity": "High"}},
                ],
                "descriptor": {
                    "db": {"built": "2026-07-24T00:00:00Z", "schemaVersion": 6}
                },
            }
        ),
        encoding="utf-8",
    )
    metadata_document = {
        "containerimage.digest": manifest_digest,
        "containerimage.config.digest": config_digest,
    }
    primary_metadata = tmp_path / "production-build-1.json"
    rebuild_metadata = tmp_path / "production-build-2.json"
    published_metadata = tmp_path / "published-build.json"
    for path in (primary_metadata, rebuild_metadata, published_metadata):
        path.write_text(json.dumps(metadata_document), encoding="utf-8")
    primary_oci = tmp_path / "production-oci-evidence.json"
    rebuild_oci = tmp_path / "rebuild-oci-evidence.json"
    for path, metadata in (
        (primary_oci, primary_metadata),
        (rebuild_oci, rebuild_metadata),
    ):
        path.write_text(
            json.dumps(
                {
                    "oci_image_evidence_schema_version": 1,
                    "status": "validated-oci-image",
                    "platform": "linux/amd64",
                    "source_date_epoch": 1784870000,
                    "archive": {
                        "filename": path.stem + ".tar",
                        "bytes": 123,
                        "sha256": "d" * 64,
                    },
                    "build_metadata": _file_identity(metadata),
                    "image": {
                        "manifest_digest": manifest_digest,
                        "config_digest": config_digest,
                        "layer_count": 1,
                        "layer_digests": [layer_digest],
                    },
                }
            ),
            encoding="utf-8",
        )
    return {
        "config": config_digest,
        "manifest": manifest_digest,
        "layer": layer_digest,
    }, {
        "registry_manifest": registry,
        "sbom": sbom,
        "vulnerability_report": scan,
        "primary_build_metadata": primary_metadata,
        "rebuild_metadata": rebuild_metadata,
        "published_build_metadata": published_metadata,
        "primary_oci_evidence": primary_oci,
        "rebuild_oci_evidence": rebuild_oci,
    }


def _arguments(tmp_path: Path) -> tuple[dict[str, str], dict]:
    digests, paths = _reports(tmp_path)
    return digests, {
        "image_config_digest": digests["config"],
        "rebuild_image_config_digest": digests["config"],
        "image_manifest_digest": digests["manifest"],
        "rebuild_image_manifest_digest": digests["manifest"],
        "image_name": evidence.EXPECTED_IMAGE_NAME,
        "registry_manifest_digest": digests["manifest"],
        **paths,
        "syft_version": evidence.EXPECTED_SYFT_VERSION,
        "grype_version": evidence.EXPECTED_GRYPE_VERSION,
        "buildx_version": evidence.EXPECTED_BUILDX_VERSION,
        "buildkit_version": evidence.EXPECTED_BUILDKIT_VERSION,
        "buildkit_image": evidence.EXPECTED_BUILDKIT_IMAGE,
        "source_date_epoch": "1784870000",
        "repository": "Ryuya-dot-com/LexicalDiversity",
        "workflow_run_id": "123456",
        "workflow_run_attempt": "1",
        "attestation_id": "attestation-1",
        "attestation_url": (
            "https://github.com/Ryuya-dot-com/LexicalDiversity/attestations/1"
        ),
    }


def test_candidate_evidence_is_canonical_and_keeps_release_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    digests, arguments = _arguments(tmp_path)
    commit = "c" * 40
    tree = "d" * 40

    def fake_git(*git_arguments: str) -> str:
        if git_arguments == ("rev-parse", "HEAD"):
            return commit
        if git_arguments == ("rev-parse", "HEAD^{tree}"):
            return tree
        raise AssertionError(git_arguments)

    monkeypatch.setattr(evidence, "_git", fake_git)
    first = evidence.canonical_json(evidence.build_evidence(_identity(), **arguments))
    second = evidence.canonical_json(evidence.build_evidence(_identity(), **arguments))
    document = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert document["candidate_image_evidence_schema_version"] == 2
    assert document["status"] == "verified-candidate-not-release"
    assert document["source"]["commit"] == commit
    assert document["source"]["tree"] == tree
    assert document["application_image"]["candidate_tag"] == f"candidate-{commit}"
    assert document["application_image"]["config_digest"] == digests["config"]
    assert document["application_image"]["manifest_digest"] == digests["manifest"]
    assert document["application_image"]["layer_digests"] == [digests["layer"]]
    assert document["application_image"]["immutable_reference"].endswith(
        "@" + digests["manifest"]
    )
    assert document["sbom"]["generator_version"] == "1.49.0"
    assert document["vulnerability_scan"]["finding_counts_by_severity"] == {
        "high": 2,
        "low": 1,
    }
    assert document["vulnerability_scan"]["gate_passed"] is True
    reproducibility = document["reproducibility"]
    assert reproducibility["independent_no_cache_production_builds"] == 2
    assert reproducibility["image_manifest_digests_equal"] is True
    assert reproducibility["image_config_digests_equal"] is True
    assert reproducibility["image_layer_digests_equal"] is True
    assert reproducibility["source_date_epoch"] == 1784870000
    assert reproducibility["rewrite_timestamp"] is True
    assert reproducibility["buildkit_compatibility_version"] == 20
    assert reproducibility["docker_buildx_version"] == "0.35.0"
    assert reproducibility["buildkit_version"] == "0.31.2"
    assert reproducibility["buildkit_image"] == evidence.EXPECTED_BUILDKIT_IMAGE
    assert document["release_boundary"] == {
        "git_tag": None,
        "github_release": None,
        "registry_candidate_is_release": False,
        "promotion_requires_new_release_version_and_tag_workflow": True,
    }
    assert set(document["build_inputs"]["files"]) == {
        path.as_posix() for path in evidence.EVIDENCE_PATHS
    }


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("image_config_digest", "sha256:abc", "primary image config digest"),
        (
            "rebuild_image_config_digest",
            "sha256:" + "e" * 64,
            "config digests differ",
        ),
        (
            "rebuild_image_manifest_digest",
            "sha256:" + "e" * 64,
            "manifest digests differ",
        ),
        ("registry_manifest_digest", "b" * 64, "registry manifest digest"),
        ("image_name", "ghcr.io/Ryuya/Uppercase", "lowercase GHCR"),
        ("syft_version", "latest", "Syft version"),
        ("buildx_version", "0.34.0", "Buildx version differs"),
        ("buildkit_version", "0.30.0", "BuildKit version differs"),
        ("buildkit_image", "moby/buildkit:latest", "BuildKit image differs"),
        ("source_date_epoch", "today", "SOURCE_DATE_EPOCH"),
        ("workflow_run_id", "run-1", "workflow run identity"),
    ],
)
def test_candidate_evidence_rejects_ambiguous_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
):
    _digests, arguments = _arguments(tmp_path)
    monkeypatch.setattr(
        evidence,
        "_git",
        lambda *git_arguments: (
            "c" * 40 if git_arguments[-1] == "HEAD" else "d" * 40
        ),
    )
    arguments[field] = value

    with pytest.raises(evidence.CandidateImageEvidenceError, match=message):
        evidence.build_evidence(_identity(), **arguments)


def test_candidate_evidence_rejects_changed_layer_digest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _digests, arguments = _arguments(tmp_path)
    rebuild_path = arguments["rebuild_oci_evidence"]
    rebuild = json.loads(rebuild_path.read_text(encoding="utf-8"))
    rebuild["image"]["layer_digests"] = ["sha256:" + "e" * 64]
    rebuild_path.write_text(json.dumps(rebuild), encoding="utf-8")
    monkeypatch.setattr(evidence, "_git", lambda *args: "c" * 40)

    with pytest.raises(evidence.CandidateImageEvidenceError, match="layer digest"):
        evidence.build_evidence(_identity(), **arguments)


def test_candidate_evidence_rejects_non_sbom_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _digests, arguments = _arguments(tmp_path)
    arguments["sbom"].write_text('{"packages": []}', encoding="utf-8")
    monkeypatch.setattr(evidence, "_git", lambda *args: "c" * 40)

    with pytest.raises(evidence.CandidateImageEvidenceError, match="SBOM"):
        evidence.build_evidence(_identity(), **arguments)


def test_candidate_evidence_writer_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "evidence.json"
    evidence.write_exclusive(output, b"first")

    with pytest.raises(evidence.CandidateImageEvidenceError, match="overwrite"):
        evidence.write_exclusive(output, b"second")

    assert output.read_bytes() == b"first"
