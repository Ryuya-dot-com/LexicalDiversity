#!/usr/bin/env python3
"""Fail closed when the Git release inventory violates resource governance."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path, PurePosixPath
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data" / "resource_registry.json"
FORBIDDEN_TRACKED_PREFIXES = (
    ".research/",
    ".streamlit/runtime_lists/",
    "LexicalSophistication/",
    "TAALED/",
    "NationBNCCOCA/",
    "NGSL/",
    "sources/",
    "data/raw/",
    "data/sources/",
)
FORBIDDEN_PAYLOAD_DIRS = {
    "data/antbnc/": {"data/antbnc/manifest.json"},
    "data/bnc_coca/": {"data/bnc_coca/manifest.json"},
}
PUBLIC_METADATA_ONLY_DIRS = {
    "benchmarks/ellipse/": {
        "benchmarks/ellipse/analysis-plan.json",
        "benchmarks/ellipse/manifest.json",
    },
    "benchmarks/synthetic/": {
        "benchmarks/synthetic/pilot-protocol.json",
    },
}
FORBIDDEN_TRACKED_FILES = {
    ".streamlit/secrets.toml",
    "streamlit-secrets.local.toml",
    "antbnc_lemmas_ver_004.txt",
    "NJ8.csv",
    "docs/dispersion-sensitivity-simulation.html",
    "docs/dispersion-sensitivity-simulation.quarto_ipynb",
}
FORBIDDEN_GENERATED_PREFIXES = (
    "docs/dispersion-sensitivity-simulation_files/",
)


def _normalized(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().rstrip("/")


def release_violations(
    registry: dict[str, object],
    tracked_paths: Iterable[str],
) -> list[str]:
    """Return fail-closed violations for the proposed Git release inventory."""

    tracked = {_normalized(path) for path in tracked_paths if path}
    violations: list[str] = []
    reviewed_public_artifacts = {
        _normalized(str(artifact["path"]))
        for resource in registry.get("resources", [])
        for artifact in resource.get("artifacts", [])
        if artifact.get("public_build") is True and artifact.get("path")
    }

    for path in sorted(tracked):
        if path in FORBIDDEN_TRACKED_FILES or any(
            path.startswith(prefix) for prefix in FORBIDDEN_TRACKED_PREFIXES
        ):
            violations.append(f"private deployment payload is Git-tracked: {path}")
        if any(path.startswith(prefix) for prefix in FORBIDDEN_GENERATED_PREFIXES):
            violations.append(f"generated documentation artifact is Git-tracked: {path}")
        for prefix, allowed in FORBIDDEN_PAYLOAD_DIRS.items():
            if path.startswith(prefix) and path not in allowed:
                violations.append(f"server-only payload is Git-tracked: {path}")
        for prefix, allowed in PUBLIC_METADATA_ONLY_DIRS.items():
            if path.startswith(prefix) and path not in allowed:
                violations.append(f"benchmark payload is Git-tracked: {path}")
        if path.startswith("data/open/") and path not in reviewed_public_artifacts:
            violations.append(f"unregistered public-data artifact is Git-tracked: {path}")

    for resource in registry.get("resources", []):
        resource_id = str(resource.get("id", "<missing-id>"))
        status = (resource.get("status") or {}).get("level")
        for artifact in resource.get("artifacts", []):
            raw_path = artifact.get("path")
            if not raw_path:
                continue
            path = _normalized(str(raw_path))
            matching = sorted(
                item for item in tracked
                if item == path or item.startswith(f"{path}/")
            )
            public_build = artifact.get("public_build") is True

            if not public_build and matching:
                for item in matching:
                    violations.append(
                        f"blocked resource is Git-tracked: {resource_id} ({status}) -> {item}"
                    )
            elif public_build and path not in tracked:
                violations.append(
                    f"public artifact is absent from Git inventory: {resource_id} -> {path}"
                )

    return sorted(set(violations))


def git_tracked_paths(project_root: Path = PROJECT_ROOT) -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=project_root,
        check=True,
        capture_output=True,
    )
    return [
        item.decode("utf-8", errors="surrogateescape")
        for item in result.stdout.split(b"\0")
        if item
    ]


def main() -> int:
    registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    tracked = git_tracked_paths()
    violations = release_violations(registry, tracked)
    if violations:
        print("Public release gate: BLOCKED")
        for violation in violations:
            print(f"- {violation}")
        return 1
    print("Public release gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
