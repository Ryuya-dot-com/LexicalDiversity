import json
from pathlib import Path

import pytest

from scripts import build_candidate_image_evidence as evidence


ROOT = Path(__file__).resolve().parents[1]


def _identity() -> dict:
    return json.loads((ROOT / "ldfreq" / "release.json").read_text(encoding="utf-8"))


def _reports(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    registry = tmp_path / "registry-manifest.json"
    registry.write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "config": {"digest": "sha256:" + "a" * 64},
            }
        ),
        encoding="utf-8",
    )
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
    primary = tmp_path / "production-build-1.json"
    rebuild = tmp_path / "production-build-2.json"
    primary.write_text('{"containerimage.digest":"sha256:one"}', encoding="utf-8")
    rebuild.write_text('{"containerimage.digest":"sha256:one"}', encoding="utf-8")
    return registry, sbom, scan, primary, rebuild


def test_candidate_evidence_is_canonical_and_keeps_release_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    registry, sbom, scan, primary, rebuild = _reports(tmp_path)
    commit = "c" * 40
    tree = "d" * 40

    def fake_git(*arguments: str) -> str:
        if arguments == ("rev-parse", "HEAD"):
            return commit
        if arguments == ("rev-parse", "HEAD^{tree}"):
            return tree
        raise AssertionError(arguments)

    monkeypatch.setattr(evidence, "_git", fake_git)
    arguments = {
        "image_id": "sha256:" + "a" * 64,
        "rebuild_image_id": "sha256:" + "a" * 64,
        "image_name": "ghcr.io/ryuya-dot-com/lexicaldiversity",
        "registry_manifest_digest": "sha256:" + "b" * 64,
        "registry_manifest": registry,
        "sbom": sbom,
        "vulnerability_report": scan,
        "syft_version": "1.49.0",
        "grype_version": "0.116.0",
        "buildx_version": "0.32.0",
        "source_date_epoch": "1784870000",
        "primary_build_metadata": primary,
        "rebuild_metadata": rebuild,
        "repository": "Ryuya-dot-com/LexicalDiversity",
        "workflow_run_id": "123456",
        "workflow_run_attempt": "1",
        "attestation_id": "attestation-1",
        "attestation_url": "https://github.com/Ryuya-dot-com/LexicalDiversity/attestations/1",
    }

    first = evidence.canonical_json(evidence.build_evidence(_identity(), **arguments))
    second = evidence.canonical_json(evidence.build_evidence(_identity(), **arguments))
    document = json.loads(first)

    assert first == second
    assert first.endswith(b"\n")
    assert document["status"] == "verified-candidate-not-release"
    assert document["source"]["commit"] == commit
    assert document["source"]["tree"] == tree
    assert document["application_image"]["candidate_tag"] == f"candidate-{commit}"
    assert document["application_image"]["immutable_reference"].endswith(
        "@sha256:" + "b" * 64
    )
    assert document["sbom"]["generator_version"] == "1.49.0"
    assert document["vulnerability_scan"]["finding_counts_by_severity"] == {
        "high": 2,
        "low": 1,
    }
    assert document["vulnerability_scan"]["gate_passed"] is True
    assert document["reproducibility"]["independent_no_cache_production_builds"] == 2
    assert document["reproducibility"]["local_image_ids_equal"] is True
    assert document["reproducibility"]["source_date_epoch"] == 1784870000
    assert document["reproducibility"]["rewrite_timestamp"] is True
    assert document["reproducibility"]["buildkit_compatibility_version"] == 20
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
        ("image_id", "sha256:abc", "local image ID"),
        ("rebuild_image_id", "sha256:" + "c" * 64, "image IDs differ"),
        ("registry_manifest_digest", "b" * 64, "registry manifest digest"),
        ("image_name", "ghcr.io/Ryuya/Uppercase", "lowercase GHCR"),
        ("syft_version", "latest", "Syft version"),
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
    registry, sbom, scan, primary, rebuild = _reports(tmp_path)
    monkeypatch.setattr(
        evidence,
        "_git",
        lambda *arguments: "c" * 40 if arguments[-1] == "HEAD" else "d" * 40,
    )
    arguments = {
        "image_id": "sha256:" + "a" * 64,
        "rebuild_image_id": "sha256:" + "a" * 64,
        "image_name": "ghcr.io/ryuya-dot-com/lexicaldiversity",
        "registry_manifest_digest": "sha256:" + "b" * 64,
        "registry_manifest": registry,
        "sbom": sbom,
        "vulnerability_report": scan,
        "syft_version": "1.49.0",
        "grype_version": "0.116.0",
        "buildx_version": "0.32.0",
        "source_date_epoch": "1784870000",
        "primary_build_metadata": primary,
        "rebuild_metadata": rebuild,
        "repository": "Ryuya-dot-com/LexicalDiversity",
        "workflow_run_id": "123456",
        "workflow_run_attempt": "1",
        "attestation_id": "attestation-1",
        "attestation_url": "https://github.com/example/repo/attestations/1",
    }
    arguments[field] = value

    with pytest.raises(evidence.CandidateImageEvidenceError, match=message):
        evidence.build_evidence(_identity(), **arguments)


def test_candidate_evidence_rejects_non_sbom_json(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    registry, sbom, scan, primary, rebuild = _reports(tmp_path)
    sbom.write_text('{"packages": []}', encoding="utf-8")
    monkeypatch.setattr(
        evidence,
        "_git",
        lambda *arguments: "c" * 40 if arguments[-1] == "HEAD" else "d" * 40,
    )

    with pytest.raises(evidence.CandidateImageEvidenceError, match="SBOM"):
        evidence.build_evidence(
            _identity(),
            image_id="sha256:" + "a" * 64,
            rebuild_image_id="sha256:" + "a" * 64,
            image_name="ghcr.io/ryuya-dot-com/lexicaldiversity",
            registry_manifest_digest="sha256:" + "b" * 64,
            registry_manifest=registry,
            sbom=sbom,
            vulnerability_report=scan,
            syft_version="1.49.0",
            grype_version="0.116.0",
            buildx_version="0.32.0",
            source_date_epoch="1784870000",
            primary_build_metadata=primary,
            rebuild_metadata=rebuild,
            repository="Ryuya-dot-com/LexicalDiversity",
            workflow_run_id="123456",
            workflow_run_attempt="1",
            attestation_id="attestation-1",
            attestation_url="https://github.com/example/repo/attestations/1",
        )


def test_candidate_evidence_writer_refuses_overwrite(tmp_path: Path):
    output = tmp_path / "evidence.json"
    evidence.write_exclusive(output, b"first")

    with pytest.raises(evidence.CandidateImageEvidenceError, match="overwrite"):
        evidence.write_exclusive(output, b"second")

    assert output.read_bytes() == b"first"
