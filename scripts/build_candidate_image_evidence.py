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
    Path("data/resource_registry.json"),
    Path("tests/fixtures/v1_golden/manifest.json"),
)
EXPECTED_IMAGE_NAME = "ghcr.io/ryuya-dot-com/lexicaldiversity"
EXPECTED_SYFT_VERSION = "1.49.0"
EXPECTED_GRYPE_VERSION = "0.116.0"
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


def build_evidence(
    identity: dict[str, Any],
    *,
    image_id: str,
    rebuild_image_id: str,
    image_name: str,
    registry_manifest_digest: str,
    registry_manifest: Path,
    sbom: Path,
    vulnerability_report: Path,
    syft_version: str,
    grype_version: str,
    buildx_version: str,
    source_date_epoch: str,
    primary_build_metadata: Path,
    rebuild_metadata: Path,
    repository: str,
    workflow_run_id: str,
    workflow_run_attempt: str,
    attestation_id: str,
    attestation_url: str,
) -> dict[str, Any]:
    image_id = _validated_digest(image_id, label="local image ID")
    rebuild_image_id = _validated_digest(
        rebuild_image_id,
        label="rebuild image ID",
    )
    if rebuild_image_id != image_id:
        raise CandidateImageEvidenceError("two no-cache production image IDs differ")
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

    manifest_identity, manifest_document = json_identity(registry_manifest)
    sbom_identity, sbom_document = json_identity(sbom)
    scan_identity, scan_document = json_identity(vulnerability_report)
    primary_metadata_identity, _primary_metadata = json_identity(
        primary_build_metadata
    )
    rebuild_metadata_identity, _rebuild_metadata = json_identity(rebuild_metadata)
    if not (
        str(sbom_document.get("spdxVersion", "")).startswith("SPDX-")
        or str(sbom_document.get("bomFormat", "")).casefold() == "cyclonedx"
    ):
        raise CandidateImageEvidenceError("SBOM is not SPDX or CycloneDX JSON")
    manifest_config = manifest_document.get("config")
    if not isinstance(manifest_config, dict) or manifest_config.get("digest") != image_id:
        raise CandidateImageEvidenceError(
            "registry manifest config digest differs from the local image ID"
        )

    base_image = json.loads(
        (PROJECT_ROOT / "deploy" / "cloud-run" / "base-image.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "candidate_image_evidence_schema_version": 1,
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
            "local_image_ids_equal": True,
            "source_date_epoch": int(source_date_epoch),
            "rewrite_timestamp": True,
            "buildkit_compatibility_version": BUILDKIT_COMPATIBILITY_VERSION,
            "docker_buildx_version": _validated_version(
                buildx_version,
                label="Docker Buildx version",
            ),
            "primary_build_metadata": primary_metadata_identity,
            "rebuild_metadata": rebuild_metadata_identity,
        },
        "application_image": {
            "name": image_name,
            "candidate_tag": f"candidate-{_git('rev-parse', 'HEAD')}",
            "local_image_id": image_id,
            "registry_manifest_digest": registry_digest,
            "immutable_reference": f"{image_name}@{registry_digest}",
            "registry_manifest": manifest_identity,
            "registry_manifest_media_type": manifest_document.get("mediaType"),
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
            "finding_counts_by_severity": _severity_counts(scan_document),
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
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--rebuild-image-id", required=True)
    parser.add_argument("--image-name", required=True)
    parser.add_argument("--registry-manifest-digest", required=True)
    parser.add_argument("--registry-manifest", required=True, type=Path)
    parser.add_argument("--sbom", required=True, type=Path)
    parser.add_argument("--vulnerability-report", required=True, type=Path)
    parser.add_argument("--syft-version", required=True)
    parser.add_argument("--grype-version", required=True)
    parser.add_argument("--buildx-version", required=True)
    parser.add_argument("--source-date-epoch", required=True)
    parser.add_argument("--primary-build-metadata", required=True, type=Path)
    parser.add_argument("--rebuild-metadata", required=True, type=Path)
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
            image_id=args.image_id,
            rebuild_image_id=args.rebuild_image_id,
            image_name=args.image_name,
            registry_manifest_digest=args.registry_manifest_digest,
            registry_manifest=args.registry_manifest,
            sbom=args.sbom,
            vulnerability_report=args.vulnerability_report,
            syft_version=args.syft_version,
            grype_version=args.grype_version,
            buildx_version=args.buildx_version,
            source_date_epoch=args.source_date_epoch,
            primary_build_metadata=args.primary_build_metadata,
            rebuild_metadata=args.rebuild_metadata,
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
