#!/usr/bin/env python3
"""Verify application, output-schema, changelog, and Git-tag version contracts."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
IDENTITY_PATH = PROJECT_ROOT / "ldfreq" / "release.json"
SCOPE_PATH = PROJECT_ROOT / "docs" / "v1-metric-scope.json"
CHANGELOG_PATH = PROJECT_ROOT / "CHANGELOG.md"
ATTRIBUTES_PATH = PROJECT_ROOT / ".gitattributes"
CITATION_PATH = PROJECT_ROOT / "CITATION.cff"
IDENTITY_KEYS = {
    "contract_schema_version",
    "application_version",
    "output_schema_version",
    "target_application_release",
    "release_phase",
    "versioning_scheme",
}
SEMVER = re.compile(
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>"
    r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*"
    r"))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?\Z"
)


class VersionContractError(ValueError):
    """Raised when version metadata cannot be safely interpreted."""


def _read_json(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VersionContractError(f"version contract is unreadable: {path}") from exc
    if not isinstance(document, dict):
        raise VersionContractError(f"version contract must be an object: {path}")
    return document


def is_semver(value: object) -> bool:
    return isinstance(value, str) and SEMVER.fullmatch(value) is not None


def contract_violations(
    identity: Mapping[str, Any],
    scope: Mapping[str, Any],
    changelog: str,
    attributes: str,
) -> list[str]:
    """Return version-contract violations without consulting mutable Git state."""

    violations: list[str] = []
    if set(identity) != IDENTITY_KEYS:
        violations.append("release identity keys differ from the reviewed schema")
    if identity.get("contract_schema_version") != 1:
        violations.append("release identity contract schema must be 1")

    for key in (
        "application_version",
        "output_schema_version",
        "target_application_release",
    ):
        if not is_semver(identity.get(key)):
            violations.append(f"{key} is not strict Semantic Versioning")

    phase = identity.get("release_phase")
    application = identity.get("application_version")
    match = SEMVER.fullmatch(application) if isinstance(application, str) else None
    prerelease = match.group("prerelease") if match else None
    if phase not in {"development", "release-candidate", "stable"}:
        violations.append("release_phase is unsupported")
    elif phase == "development" and (
        prerelease is None or "dev" not in prerelease.split(".")
    ):
        violations.append("development version must contain a dev prerelease identifier")
    elif phase == "release-candidate" and (
        prerelease is None or "rc" not in prerelease.split(".")
    ):
        violations.append("release-candidate version must contain an rc identifier")
    elif phase == "stable" and prerelease is not None:
        violations.append("stable version must not contain a prerelease identifier")

    if identity.get("versioning_scheme") != "SemVer 2.0.0":
        violations.append("versioning scheme must remain SemVer 2.0.0")
    if scope.get("contract_version") != identity.get("output_schema_version"):
        violations.append("output schema version differs from the frozen scope contract")
    if scope.get("target_application_release") != identity.get(
        "target_application_release"
    ):
        violations.append("target application release differs from the scope contract")

    if "## [Unreleased]" not in changelog:
        violations.append("CHANGELOG.md has no Unreleased section")
    if str(application) not in changelog:
        violations.append("CHANGELOG.md does not state the current application identity")

    required_attributes = {
        "* text=auto eol=lf",
        "*.csv binary",
        "*.gz binary",
        "*.xlsx binary",
        "*.zip binary",
    }
    active_attributes = {
        line.strip()
        for line in attributes.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if not required_attributes <= active_attributes:
        violations.append(".gitattributes does not preserve text/binary identities")
    return violations


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def release_git_violations(
    identity: Mapping[str, Any],
    *,
    environment: Mapping[str, str] = os.environ,
) -> list[str]:
    """Return violations that are required only for an immutable tagged release."""

    version = str(identity.get("application_version", ""))
    tag = f"v{version}"
    violations: list[str] = []
    if identity.get("release_phase") == "development" or "-dev." in version:
        violations.append("development identity cannot be released or tagged")
    if _git("status", "--porcelain=v1", "--untracked-files=all"):
        violations.append("release worktree is not clean")

    ref_type = environment.get("GITHUB_REF_TYPE")
    ref_name = environment.get("GITHUB_REF_NAME")
    if ref_type is not None and (ref_type != "tag" or ref_name != tag):
        violations.append(f"GitHub release ref must be the exact tag {tag}")

    exact_tags = _git("tag", "--points-at", "HEAD").splitlines()
    if exact_tags != [tag]:
        violations.append(f"HEAD must have exactly one release tag: {tag}")
    try:
        tag_type = _git("cat-file", "-t", f"refs/tags/{tag}")
    except subprocess.CalledProcessError:
        tag_type = ""
    if tag_type != "tag":
        violations.append(f"release tag must be annotated: {tag}")

    dated_release = re.compile(
        rf"(?m)^## \[{re.escape(version)}\] - \d{{4}}-\d{{2}}-\d{{2}}$"
    )
    if dated_release.search(CHANGELOG_PATH.read_text(encoding="utf-8")) is None:
        violations.append("CHANGELOG.md has no dated entry for the release version")
    if identity.get("release_phase") == "stable" and not CITATION_PATH.is_file():
        violations.append("stable release requires CITATION.cff")
    return violations


def development_violations() -> tuple[list[str], dict[str, Any]]:
    identity = _read_json(IDENTITY_PATH)
    scope = _read_json(SCOPE_PATH)
    violations = contract_violations(
        identity,
        scope,
        CHANGELOG_PATH.read_text(encoding="utf-8"),
        ATTRIBUTES_PATH.read_text(encoding="utf-8"),
    )

    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))
    import ldfreq

    if ldfreq.__version__ != identity.get("application_version"):
        violations.append("imported application version differs from release.json")
    if ldfreq.OUTPUT_SCHEMA_VERSION != identity.get("output_schema_version"):
        violations.append("imported output schema version differs from release.json")
    return violations, identity


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--development", action="store_true")
    mode.add_argument("--release", action="store_true")
    args = parser.parse_args(argv)

    try:
        violations, identity = development_violations()
        if args.release:
            violations.extend(release_git_violations(identity))
    except Exception as exc:
        print(
            "Version contract: BLOCKED\n"
            f"- unexpected verification failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1
    if violations:
        print("Version contract: BLOCKED", file=sys.stderr)
        for violation in sorted(set(violations)):
            print(f"- {violation}", file=sys.stderr)
        return 1
    print(
        "Version contract: PASS "
        f"({identity['application_version']}; output schema "
        f"{identity['output_schema_version']}; {identity['release_phase']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
