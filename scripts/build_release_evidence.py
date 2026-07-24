#!/usr/bin/env python3
"""Create canonical source/build evidence for an already verified release tag."""
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


EVIDENCE_PATHS = (
    Path(".github/workflows/release.yml"),
    Path("ldfreq/release.json"),
    Path("CHANGELOG.md"),
    Path("requirements-ci-linux-x86_64.lock"),
    Path("deploy/cloud-run/requirements-prod-linux-x86_64.lock"),
    Path("deploy/cloud-run/base-image.json"),
    Path("deploy/cloud-run/Dockerfile"),
    Path("data/resource_registry.json"),
    Path("docs/v1-metric-scope.json"),
    Path("scripts/build_oci_image_evidence.py"),
    Path("tests/fixtures/v1_golden/manifest.json"),
)
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


def _git(*arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def file_identity(relative_path: Path) -> dict[str, Any]:
    path = PROJECT_ROOT / relative_path
    payload = path.read_bytes()
    return {
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def external_file_identity(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"evidence input is not a regular file: {path}")
    payload = path.read_bytes()
    return {
        "filename": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def oci_image_identity(
    path: Path,
    *,
    image_config_digest: str,
    image_manifest_digest: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    identity = external_file_identity(path)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("OCI image evidence is unreadable") from exc
    if not isinstance(document, dict):
        raise ValueError("OCI image evidence must contain one object")
    if document.get("oci_image_evidence_schema_version") != 1:
        raise ValueError("OCI image evidence schema is unsupported")
    if document.get("status") != "validated-oci-image":
        raise ValueError("OCI image evidence is not validated")
    if document.get("platform") != "linux/amd64":
        raise ValueError("OCI image evidence platform must be linux/amd64")
    image = document.get("image")
    if not isinstance(image, dict):
        raise ValueError("OCI image evidence lacks image identity")
    if image.get("config_digest") != image_config_digest:
        raise ValueError("OCI image config digest differs from release evidence")
    if image.get("manifest_digest") != image_manifest_digest:
        raise ValueError("OCI image manifest digest differs from release evidence")
    layers = image.get("layer_digests")
    if not isinstance(layers, list) or not layers:
        raise ValueError("OCI image evidence lacks layer digests")
    if image.get("layer_count") != len(layers):
        raise ValueError("OCI image evidence layer count is inconsistent")
    if any(not DIGEST.fullmatch(str(digest)) for digest in layers):
        raise ValueError("OCI image evidence contains an invalid layer digest")
    return identity, document


def build_evidence(
    identity: dict[str, Any],
    *,
    image_config_digest: str,
    image_manifest_digest: str,
    oci_image_evidence: Path,
    source_archive: Path,
) -> dict[str, Any]:
    if not DIGEST.fullmatch(image_config_digest):
        raise ValueError("application image config must be one SHA-256 digest")
    if not DIGEST.fullmatch(image_manifest_digest):
        raise ValueError("application image manifest must be one SHA-256 digest")
    oci_identity, oci_document = oci_image_identity(
        oci_image_evidence,
        image_config_digest=image_config_digest,
        image_manifest_digest=image_manifest_digest,
    )
    version = identity["application_version"]
    base_image = json.loads(
        (PROJECT_ROOT / "deploy" / "cloud-run" / "base-image.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "evidence_schema_version": 2,
        "application_version": version,
        "output_schema_version": identity["output_schema_version"],
        "release_phase": identity["release_phase"],
        "source": {
            "tag": f"v{version}",
            "commit": _git("rev-parse", "HEAD"),
            "tree": _git("rev-parse", "HEAD^{tree}"),
            "archive": external_file_identity(source_archive),
        },
        "build_inputs": {
            "python_base_index_digest": base_image["index_digest"],
            "python_base_linux_amd64_manifest_digest": base_image[
                "manifest_digest"
            ],
            "files": {
                path.as_posix(): file_identity(path) for path in EVIDENCE_PATHS
            },
        },
        "application_image": {
            "platform": "linux/amd64",
            "config_digest": image_config_digest,
            "manifest_digest": image_manifest_digest,
            "layer_digests": oci_document["image"]["layer_digests"],
            "oci_image_evidence": oci_identity,
            "registry_manifest_digest": None,
            "status": "OCI image verified; registry publication pending",
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
    """Create one immutable evidence file without replacing prior evidence."""

    try:
        with path.open("xb") as output:
            output.write(payload)
    except FileExistsError as exc:
        raise versions.VersionContractError(
            f"refusing to overwrite existing output: {path}"
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-config-digest", required=True)
    parser.add_argument("--image-manifest-digest", required=True)
    parser.add_argument("--oci-image-evidence", type=Path, required=True)
    parser.add_argument("--source-archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        violations, identity = versions.development_violations()
        violations.extend(versions.release_git_violations(identity))
        if violations:
            raise versions.VersionContractError("; ".join(sorted(set(violations))))
        payload = canonical_json(
            build_evidence(
                identity,
                image_config_digest=args.image_config_digest,
                image_manifest_digest=args.image_manifest_digest,
                oci_image_evidence=args.oci_image_evidence,
                source_archive=args.source_archive,
            )
        )
        write_exclusive(args.output, payload)
    except Exception as exc:
        print(f"Release evidence: BLOCKED\n- {exc}", file=sys.stderr)
        return 1
    print(
        "Release evidence: PASS "
        f"({len(payload)} bytes; sha256:{hashlib.sha256(payload).hexdigest()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
