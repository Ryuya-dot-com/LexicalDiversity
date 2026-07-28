#!/usr/bin/env python3
"""Validate the pinned Docker base image offline and, optionally, remotely."""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = PROJECT_ROOT / "deploy" / "cloud-run" / "base-image.json"
DOCKERFILE = PROJECT_ROOT / "deploy" / "cloud-run" / "Dockerfile"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class BaseImageIdentityError(ValueError):
    """Raised when local or registry identity is ambiguous or inconsistent."""


def read_identity(path: Path = IDENTITY_PATH) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BaseImageIdentityError(f"base image identity is unreadable: {path}") from exc
    if not isinstance(document, dict):
        raise BaseImageIdentityError("base image identity must be a JSON object")

    required_strings = (
        "registry",
        "repository",
        "tag",
        "index_digest",
        "manifest_digest",
        "config_digest",
        "python_version",
        "python_source_sha256",
        "created",
        "verified_on",
        "source",
    )
    for key in required_strings:
        if not isinstance(document.get(key), str) or not document[key]:
            raise BaseImageIdentityError(f"base image field {key!r} is missing")
    for key in ("index_digest", "manifest_digest", "config_digest"):
        if not DIGEST.fullmatch(document[key]):
            raise BaseImageIdentityError(f"base image field {key!r} is not SHA-256")
    if not re.fullmatch(r"[0-9a-f]{64}", document["python_source_sha256"]):
        raise BaseImageIdentityError("python_source_sha256 is not 64 lowercase hex")
    if document.get("schema_version") != 1:
        raise BaseImageIdentityError("unsupported base image identity schema")
    if document.get("platform") != {"os": "linux", "architecture": "amd64"}:
        raise BaseImageIdentityError("release base image must target linux/amd64")
    if document["python_version"] != "3.12.13":
        raise BaseImageIdentityError("release base image must contain Python 3.12.13")
    return document


def offline_violations(identity: dict[str, Any], dockerfile: Path = DOCKERFILE) -> list[str]:
    text = dockerfile.read_text(encoding="utf-8")
    image = (
        f"python:{identity['tag']}@{identity['manifest_digest']}"
    )
    violations: list[str] = []
    if f"ARG PYTHON_IMAGE={image}" not in text:
        violations.append("Dockerfile default does not match the platform manifest digest")
    if "FROM --platform=linux/amd64 ${PYTHON_IMAGE}" not in text:
        violations.append("Dockerfile does not force the reviewed linux/amd64 platform")
    return violations


def _json_request(url: str, *, headers: dict[str, str] | None = None) -> tuple[Any, Any]:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "ldfreq-base-image-verifier/1", **(headers or {})},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response), response.headers


def remote_violations(identity: dict[str, Any]) -> list[str]:
    """Compare the recorded tag, selected child manifest, and config to Docker Hub."""

    scope = urllib.parse.urlencode(
        {
            "service": "registry.docker.io",
            "scope": f"repository:{identity['repository']}:pull",
        }
    )
    token_doc, _ = _json_request(f"https://auth.docker.io/token?{scope}")
    token = token_doc.get("token") if isinstance(token_doc, dict) else None
    if not isinstance(token, str) or not token:
        raise BaseImageIdentityError("Docker registry did not return an anonymous token")
    authorization = {"Authorization": f"Bearer {token}"}
    base = f"https://{identity['registry']}/v2/{identity['repository']}"
    index, index_headers = _json_request(
        f"{base}/manifests/{identity['tag']}",
        headers={
            **authorization,
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json"
            ),
        },
    )
    violations: list[str] = []
    if index_headers.get("Docker-Content-Digest") != identity["index_digest"]:
        violations.append("remote tag index digest differs from the recorded digest")

    matches = [
        item
        for item in index.get("manifests", [])
        if item.get("platform") == identity["platform"]
    ]
    if len(matches) != 1:
        violations.append("remote index does not have exactly one linux/amd64 manifest")
        return violations
    if matches[0].get("digest") != identity["manifest_digest"]:
        violations.append("remote linux/amd64 manifest digest differs")
        return violations

    manifest, manifest_headers = _json_request(
        f"{base}/manifests/{identity['manifest_digest']}",
        headers={
            **authorization,
            "Accept": (
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            ),
        },
    )
    if manifest_headers.get("Docker-Content-Digest") != identity["manifest_digest"]:
        violations.append("registry did not return the requested child manifest")
    config_digest = manifest.get("config", {}).get("digest")
    if config_digest != identity["config_digest"]:
        violations.append("remote image config digest differs")
        return violations

    config, _ = _json_request(
        f"{base}/blobs/{config_digest}",
        headers=authorization,
    )
    if config.get("os") != "linux" or config.get("architecture") != "amd64":
        violations.append("remote image config platform differs")
    if config.get("created") != identity["created"]:
        violations.append("remote image creation identity differs")
    environment = {
        key: value
        for item in config.get("config", {}).get("Env", [])
        if "=" in item
        for key, value in [item.split("=", 1)]
    }
    if environment.get("PYTHON_VERSION") != identity["python_version"]:
        violations.append("remote image Python version differs")
    if environment.get("PYTHON_SHA256") != identity["python_source_sha256"]:
        violations.append("remote Python source digest differs")
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--remote",
        action="store_true",
        help="also query Docker Registry HTTP API V2 (requires network)",
    )
    args = parser.parse_args(argv)
    try:
        identity = read_identity()
        violations = offline_violations(identity)
        if args.remote:
            violations.extend(remote_violations(identity))
    except Exception as exc:
        print(
            "Base image identity: BLOCKED\n"
            f"- unexpected verification failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if violations:
        print("Base image identity: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    mode = "offline + registry" if args.remote else "offline"
    print(f"Base image identity: PASS ({mode}; {identity['manifest_digest']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
