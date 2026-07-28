#!/usr/bin/env python3
"""Create canonical evidence for a scanned, untagged application image candidate."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts import check_version_contract as versions


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?\Z")
IMAGE_NAME = re.compile(
    r"ghcr\.io/[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?/[a-z0-9][a-z0-9._/-]*\Z"
)
REPOSITORY = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z"
)
EVIDENCE_PATHS = (
    Path(".dockerignore"),
    Path(".github/workflows/image-candidate.yml"),
    Path("ldfreq/release.json"),
    Path("deploy/cloud-run/Dockerfile"),
    Path("deploy/cloud-run/base-image.json"),
    Path("deploy/cloud-run/requirements-prod-linux-x86_64.lock"),
    Path("deploy/cloud-run/requirements-watchdog-pure-linux-x86_64.lock"),
    Path("scripts/check_pure_watchdog_wheel.py"),
    Path("scripts/build_oci_image_evidence.py"),
    Path("data/resource_registry.json"),
    Path("tests/fixtures/v1_golden/manifest.json"),
)
EXPECTED_IMAGE_NAME = "ghcr.io/ryuya-dot-com/lexicaldiversity"
EXPECTED_SYFT_VERSION = "1.49.0"
EXPECTED_GRYPE_VERSION = "0.116.0"
EXPECTED_BUILDX_VERSION = "0.35.0"
EXPECTED_BUILDKIT_VERSION = "0.31.2"
EXPECTED_BUILDKIT_IMAGE = (
    "moby/buildkit:v0.31.2@"
    "sha256:63db51c9b30208a7c2b1c40392c7ebb9ce2f85ba238a18a85420f8f5ea2d4684"
)
BUILDKIT_COMPATIBILITY_VERSION = 20


class CandidateImageEvidenceError(ValueError):
    """Raised when candidate-image evidence is ambiguous or incomplete."""


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise CandidateImageEvidenceError(f"evidence input is not a regular file: {path}")
    payload = path.read_bytes()
    return {
        "filename": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def json_identity(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = file_identity(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateImageEvidenceError(
            f"evidence JSON is unreadable: {path.name}"
        ) from exc
    if not isinstance(document, dict):
        raise CandidateImageEvidenceError(
            f"evidence JSON must contain one object: {path.name}"
        )
    return identity, document


def _validated_digest(value: str, *, label: str) -> str:
    if not DIGEST.fullmatch(value):
        raise CandidateImageEvidenceError(f"{label} must be one SHA-256 digest")
    return value


def _validated_version(value: str, *, label: str) -> str:
    normalized = value.removeprefix("v")
    if not VERSION.fullmatch(normalized):
        raise CandidateImageEvidenceError(f"{label} is not a pinned version")
    return normalized


def _severity_counts(scan: dict[str, Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    matches = scan.get("matches", [])
    if not isinstance(matches, list):
        raise CandidateImageEvidenceError("Grype report matches must be an array")
    for match in matches:
        if not isinstance(match, dict):
            raise CandidateImageEvidenceError("Grype report contains an invalid match")
        vulnerability = match.get("vulnerability")
        if not isinstance(vulnerability, dict):
            raise CandidateImageEvidenceError("Grype match lacks vulnerability metadata")
        severity = str(vulnerability.get("severity", "Unknown")).strip().lower()
        counts[severity or "unknown"] = counts.get(severity or "unknown", 0) + 1
    return {key: counts[key] for key in sorted(counts)}


def _validated_scan_gate(scan: dict[str, Any]) -> dict[str, int]:
    counts = _severity_counts(scan)
    if counts.get("critical", 0):
        raise CandidateImageEvidenceError(
            "Grype report contains an active Critical finding"
        )
    ignored_matches = scan.get("ignoredMatches", [])
    if ignored_matches is None:
        ignored_matches = []
    if not isinstance(ignored_matches, list):
        raise CandidateImageEvidenceError(
            "Grype report ignoredMatches must be an array when present"
        )
    if ignored_matches:
        raise CandidateImageEvidenceError(
            "Grype report contains ignored findings; candidate policy permits none"
        )
    return counts


def _oci_evidence(
    path: Path,
    *,
    label: str,
    source_date_epoch: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity, document = json_identity(path)
    if document.get("oci_image_evidence_schema_version") != 1:
        raise CandidateImageEvidenceError(f"{label} OCI evidence schema is unsupported")
    if document.get("status") != "validated-oci-image":
        raise CandidateImageEvidenceError(f"{label} OCI evidence is not validated")
    if document.get("platform") != "linux/amd64":
        raise CandidateImageEvidenceError(f"{label} OCI platform must be linux/amd64")
    if document.get("source_date_epoch") != source_date_epoch:
        raise CandidateImageEvidenceError(
            f"{label} OCI SOURCE_DATE_EPOCH differs from the workflow"
        )
    image = document.get("image")
    if not isinstance(image, dict):
        raise CandidateImageEvidenceError(f"{label} OCI evidence lacks image identity")
    _validated_digest(
        str(image.get("manifest_digest")),
        label=f"{label} OCI manifest digest",
    )
    _validated_digest(
        str(image.get("config_digest")),
        label=f"{label} OCI config digest",
    )
    layers = image.get("layer_digests")
    if not isinstance(layers, list) or not layers:
        raise CandidateImageEvidenceError(f"{label} OCI layer digests are missing")
    for index, digest in enumerate(layers):
        _validated_digest(str(digest), label=f"{label} OCI layer {index} digest")
    if image.get("layer_count") != len(layers):
        raise CandidateImageEvidenceError(f"{label} OCI layer count is inconsistent")
    return identity, document


def _metadata_matches(
    path: Path,
    *,
    label: str,
    manifest_digest: str,
    config_digest: str,
) -> dict[str, Any]:
    identity, document = json_identity(path)
    if document.get("containerimage.digest") != manifest_digest:
        raise CandidateImageEvidenceError(f"{label} manifest digest differs")
    if document.get("containerimage.config.digest") != config_digest:
        raise CandidateImageEvidenceError(f"{label} config digest differs")
    return identity


def build_evidence(
    identity: dict[str, Any],
    *,
    image_config_digest: str,
    rebuild_image_config_digest: str,
    image_manifest_digest: str,
    rebuild_image_manifest_digest: str,
    image_name: str,
    registry_manifest_digest: str,
    registry_manifest: Path,
    sbom: Path,
    vulnerability_report: Path,
    syft_version: str,
    grype_version: str,
    buildx_version: str,
    buildkit_version: str,
    buildkit_image: str,
    source_date_epoch: str,
    primary_oci_evidence: Path,
    rebuild_oci_evidence: Path,
    primary_build_metadata: Path,
    rebuild_metadata: Path,
    published_build_metadata: Path,
    repository: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
    attestation_id: str,
    attestation_url: str,
) -> dict[str, Any]:
    image_config_digest = _validated_digest(
        image_config_digest,
        label="primary image config digest",
    )
    rebuild_image_config_digest = _validated_digest(
        rebuild_image_config_digest,
        label="rebuild image config digest",
    )
    image_manifest_digest = _validated_digest(
        image_manifest_digest,
        label="primary image manifest digest",
    )
    rebuild_image_manifest_digest = _validated_digest(
        rebuild_image_manifest_digest,
        label="rebuild image manifest digest",
    )
    if rebuild_image_config_digest != image_config_digest:
        raise CandidateImageEvidenceError("two no-cache image config digests differ")
    if rebuild_image_manifest_digest != image_manifest_digest:
        raise CandidateImageEvidenceError("two no-cache image manifest digests differ")
    registry_digest = _validated_digest(
        registry_manifest_digest,
        label="registry manifest digest",
    )
    if not IMAGE_NAME.fullmatch(image_name):
        raise CandidateImageEvidenceError("image name must be one lowercase GHCR path")
    if image_name != EXPECTED_IMAGE_NAME:
        raise CandidateImageEvidenceError("image name differs from the reviewed GHCR path")
    if not REPOSITORY.fullmatch(repository):
        raise CandidateImageEvidenceError("repository must be owner/name")
    if not workflow_run_id.isdigit() or not workflow_run_attempt.isdigit():
        raise CandidateImageEvidenceError("workflow run identity must be numeric")
    if not source_date_epoch.isdigit() or int(source_date_epoch) <= 0:
        raise CandidateImageEvidenceError("SOURCE_DATE_EPOCH must be a positive integer")
    if not attestation_id or not attestation_url.startswith("https://github.com/"):
        raise CandidateImageEvidenceError("GitHub attestation identity is incomplete")
    syft_version = _validated_version(syft_version, label="Syft version")
    grype_version = _validated_version(grype_version, label="Grype version")
    if syft_version != EXPECTED_SYFT_VERSION:
        raise CandidateImageEvidenceError("Syft version differs from the reviewed pin")
    if grype_version != EXPECTED_GRYPE_VERSION:
        raise CandidateImageEvidenceError("Grype version differs from the reviewed pin")
    buildx_version = _validated_version(
        buildx_version,
        label="Docker Buildx version",
    )
    buildkit_version = _validated_version(
        buildkit_version,
        label="BuildKit version",
    )
    if buildx_version != EXPECTED_BUILDX_VERSION:
        raise CandidateImageEvidenceError("Buildx version differs from the reviewed pin")
    if buildkit_version != EXPECTED_BUILDKIT_VERSION:
        raise CandidateImageEvidenceError("BuildKit version differs from the reviewed pin")
    if buildkit_image != EXPECTED_BUILDKIT_IMAGE:
        raise CandidateImageEvidenceError("BuildKit image differs from the reviewed pin")

    manifest_identity, manifest_document = json_identity(registry_manifest)
    sbom_identity, sbom_document = json_identity(sbom)
    scan_identity, scan_document = json_identity(vulnerability_report)
    epoch = int(source_date_epoch)
    finding_counts = _validated_scan_gate(scan_document)
    primary_oci_identity, primary_oci = _oci_evidence(
        primary_oci_evidence,
        label="primary",
        source_date_epoch=epoch,
    )
    rebuild_oci_identity, rebuild_oci = _oci_evidence(
        rebuild_oci_evidence,
        label="rebuild",
        source_date_epoch=epoch,
    )
    primary_image = primary_oci["image"]
    rebuild_image = rebuild_oci["image"]
    if primary_image["config_digest"] != image_config_digest:
        raise CandidateImageEvidenceError("primary OCI config digest differs")
    if rebuild_image["config_digest"] != rebuild_image_config_digest:
        raise CandidateImageEvidenceError("rebuild OCI config digest differs")
    if primary_image["manifest_digest"] != image_manifest_digest:
        raise CandidateImageEvidenceError("primary OCI manifest digest differs")
    if rebuild_image["manifest_digest"] != rebuild_image_manifest_digest:
        raise CandidateImageEvidenceError("rebuild OCI manifest digest differs")
    if primary_image["layer_digests"] != rebuild_image["layer_digests"]:
        raise CandidateImageEvidenceError("two no-cache OCI layer digest lists differ")
    primary_metadata_identity = _metadata_matches(
        primary_build_metadata,
        label="primary BuildKit metadata",
        manifest_digest=image_manifest_digest,
        config_digest=image_config_digest,
    )
    rebuild_metadata_identity = _metadata_matches(
        rebuild_metadata,
        label="rebuild BuildKit metadata",
        manifest_digest=rebuild_image_manifest_digest,
        config_digest=rebuild_image_config_digest,
    )
    published_metadata_identity = _metadata_matches(
        published_build_metadata,
        label="published BuildKit metadata",
        manifest_digest=image_manifest_digest,
        config_digest=image_config_digest,
    )
    if primary_oci.get("build_metadata") != primary_metadata_identity:
        raise CandidateImageEvidenceError(
            "primary OCI evidence identifies different BuildKit metadata"
        )
    if rebuild_oci.get("build_metadata") != rebuild_metadata_identity:
        raise CandidateImageEvidenceError(
            "rebuild OCI evidence identifies different BuildKit metadata"
        )
    if not (
        str(sbom_document.get("spdxVersion", "")).startswith("SPDX-")
        or str(sbom_document.get("bomFormat", "")).casefold() == "cyclonedx"
    ):
        raise CandidateImageEvidenceError("SBOM is not SPDX or CycloneDX JSON")
    manifest_config = manifest_document.get("config")
    if (
        not isinstance(manifest_config, dict)
        or manifest_config.get("digest") != image_config_digest
    ):
        raise CandidateImageEvidenceError(
            "registry manifest config digest differs from the OCI image config"
        )
    if f"sha256:{manifest_identity['sha256']}" != registry_digest:
        raise CandidateImageEvidenceError(
            "registry manifest bytes differ from the published digest"
        )
    if registry_digest != image_manifest_digest:
        raise CandidateImageEvidenceError(
            "registry manifest digest differs from the scanned OCI image"
        )

    base_image = json.loads(
        (PROJECT_ROOT / "deploy" / "cloud-run" / "base-image.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "candidate_image_evidence_schema_version": 3,
        "status": "verified-candidate-not-release",
        "application_version": identity["application_version"],
        "output_schema_version": identity["output_schema_version"],
        "release_phase": identity["release_phase"],
        "source": {
            "repository": repository,
            "commit": _git("rev-parse", "HEAD"),
            "tree": _git("rev-parse", "HEAD^{tree}"),
            "workflow": ".github/workflows/image-candidate.yml",
            "workflow_run_id": int(workflow_run_id),
            "workflow_run_attempt": int(workflow_run_attempt),
            "workflow_run_url": (
                f"https://github.com/{repository}/actions/runs/{workflow_run_id}"
            ),
        },
        "build_inputs": {
            "platform": "linux/amd64",
            "python_base_index_digest": base_image["index_digest"],
            "python_base_linux_amd64_manifest_digest": base_image[
                "manifest_digest"
            ],
            "files": {
                path.as_posix(): file_identity(PROJECT_ROOT / path)
                for path in EVIDENCE_PATHS
            },
        },
        "reproducibility": {
            "independent_no_cache_production_builds": 2,
            "image_manifest_digests_equal": True,
            "image_config_digests_equal": True,
            "image_layer_digests_equal": True,
            "source_date_epoch": epoch,
            "rewrite_timestamp": True,
            "buildkit_compatibility_version": BUILDKIT_COMPATIBILITY_VERSION,
            "docker_buildx_version": buildx_version,
            "buildkit_version": buildkit_version,
            "buildkit_image": buildkit_image,
            "primary_oci_image_evidence": primary_oci_identity,
            "rebuild_oci_image_evidence": rebuild_oci_identity,
            "primary_build_metadata": primary_metadata_identity,
            "rebuild_metadata": rebuild_metadata_identity,
        },
        "application_image": {
            "name": image_name,
            "candidate_tag": f"candidate-{_git('rev-parse', 'HEAD')}",
            "config_digest": image_config_digest,
            "manifest_digest": image_manifest_digest,
            "layer_digests": primary_image["layer_digests"],
            "registry_manifest_digest": registry_digest,
            "immutable_reference": f"{image_name}@{registry_digest}",
            "registry_manifest": manifest_identity,
            "registry_manifest_media_type": manifest_document.get("mediaType"),
            "published_build_metadata": published_metadata_identity,
        },
        "sbom": {
            **sbom_identity,
            "format": sbom_document.get("spdxVersion")
            or sbom_document.get("bomFormat"),
            "generator": "syft",
            "generator_version": syft_version,
        },
        "vulnerability_scan": {
            **scan_identity,
            "scanner": "grype",
            "scanner_version": grype_version,
            "severity_gate": "critical",
            "only_fixed": False,
            "gate_passed": True,
            "finding_counts_by_severity": finding_counts,
            "ignored_finding_count": 0,
            "exception_policy": "none",
            "database": (scan_document.get("descriptor") or {}).get("db"),
        },
        "provenance": {
            "provider": "GitHub artifact attestation",
            "attestation_id": attestation_id,
            "attestation_url": attestation_url,
            "registry_attestation_pushed": True,
        },
        "release_boundary": {
            "git_tag": None,
            "github_release": None,
            "registry_candidate_is_release": False,
            "promotion_requires_new_release_version_and_tag_workflow": True,
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
        raise CandidateImageEvidenceError(
            f"refusing to overwrite existing output: {path}"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-config-digest", required=True)
    parser.add_argument("--rebuild-image-config-digest", required=True)
    parser.add_argument("--image-manifest-digest", required=True)
    parser.add_argument("--rebuild-image-manifest-digest", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--registry-manifest-digest", required=True)
    parser.add_argument("--registry-manifest", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--vulnerability-report", required=True, type=Path)
    parser.add_argument("--syft-version", required=True)
    parser.add_argument("--grype-version", required=True)
    parser.add_argument("--buildx-version", required=True)
    parser.add_argument("--buildkit-version", required=True)
    parser.add_argument("--buildkit-image", required=True)
    parser.add_argument("--source-date-epoch", required=True)
    parser.add_argument("--primary-oci-evidence", required=True, type=Path)
    parser.add_argument("--rebuild-oci-evidence", required=True, type=Path)
    parser.add_argument("--primary-build-metadata", required=True, type=Path)
    parser.add_argument("--rebuild-metadata", required=True, type=Path)
    parser.add_argument("--published-build-metadata", required=True, type=Path)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--workflow-run-attempt", required=True)
    parser.add_argument("--attestation-id", required=True)
    parser.add_argument("--attestation-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)

    try:
        violations, identity = versions.development_violations()
        if violations:
            raise CandidateImageEvidenceError("; ".join(sorted(set(violations))))
        if _git("status", "--porcelain=v1", "--untracked-files=all"):
            raise CandidateImageEvidenceError("source checkout is not clean")
        document = build_evidence(
            identity,
            image_config_digest=args.image_config_digest,
            rebuild_image_config_digest=args.rebuild_image_config_digest,
            image_manifest_digest=args.image_manifest_digest,
            rebuild_image_manifest_digest=args.rebuild_image_manifest_digest,
            image_name=args.image_name,
            registry_manifest_digest=args.registry_manifest_digest,
            registry_manifest=args.registry_manifest,
            sbom=args.sbom,
            vulnerability_report=args.vulnerability_report,
            syft_version=args.syft_version,
            grype_version=args.grype_version,
            buildx_version=args.buildx_version,
            buildkit_version=args.buildkit_version,
            buildkit_image=args.buildkit_image,
            source_date_epoch=args.source_date_epoch,
            primary_oci_evidence=args.primary_oci_evidence,
            rebuild_oci_evidence=args.rebuild_oci_evidence,
            primary_build_metadata=args.primary_build_metadata,
            rebuild_metadata=args.rebuild_metadata,
            published_build_metadata=args.published_build_metadata,
            repository=args.repository,
            workflow_run_id=args.workflow_run_id,
            workflow_run_attempt=args.workflow_run_attempt,
            attestation_id=args.attestation_id,
            attestation_url=args.attestation_url,
        )
        payload = canonical_json(document)
        write_exclusive(args.output, payload)
    except Exception as exc:
        print(f"Candidate image evidence: BLOCKED\n- {exc}", file=sys.stderr)
        return 1

    print(
        "Candidate image evidence: PASS "
        f"({len(payload)} bytes; sha256:{hashlib.sha256(payload).hexdigest()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
