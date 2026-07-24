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
    Path("ldfreq/release.json"),
    Path("CHANGELOG.md"),
    Path("requirements-ci-linux-x86_64.lock"),
    Path("deploy/cloud-run/requirements-prod-linux-x86_64.lock"),
    Path("deploy/cloud-run/base-image.json"),
    Path("deploy/cloud-run/Dockerfile"),
    Path("data/resource_registry.json"),
    Path("docs/v1-metric-scope.json"),
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
    payload = path.read_bytes()
    return {
        "filename": path.name,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def build_evidence(
    identity: dict[str, Any],
    *,
    image_id: str,
    source_archive: Path,
) -> dict[str, Any]:
    if not DIGEST.fullmatch(image_id):
        raise ValueError("local application image ID must be one SHA-256 digest")
    version = identity["application_version"]
    base_image = json.loads(
        (PROJECT_ROOT / "deploy" / "cloud-run" / "base-image.json").read_text(
            encoding="utf-8"
        )
    )
    return {
        "evidence_schema_version": 1,
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
            "local_image_id": image_id,
            "registry_manifest_digest": None,
            "status": "local-build-verified; registry publication pending",
        },
    }


def canonical_json(document: dict[str, Any]) -> bytes:
    return (
        json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
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
    parser.add_argument("--image-id", required=True)
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
                image_id=args.image_id,
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
