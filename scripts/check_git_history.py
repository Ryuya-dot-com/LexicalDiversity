#!/usr/bin/env python3
"""Block a release when reachable history violates the public inventory policy."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.check_public_release import REGISTRY_PATH, release_violations


def _git_bytes(*arguments: str) -> bytes:
    return subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    ).stdout


def _git_text(*arguments: str) -> str:
    return _git_bytes(*arguments).decode("utf-8", errors="strict").strip()


def reachable_commits() -> list[str]:
    """Return every commit reachable from local branches, tags, and remote refs."""

    return [line for line in _git_text("rev-list", "--all").splitlines() if line]


def reachable_history_paths(commits: Iterable[str]) -> list[str]:
    """Return the union of file paths present in every reachable commit tree."""

    paths: set[str] = set()
    for commit in commits:
        payload = _git_bytes("ls-tree", "-r", "-z", "--name-only", commit)
        paths.update(
            item.decode("utf-8", errors="surrogateescape")
            for item in payload.split(b"\0")
            if item
        )
    return sorted(paths)


def history_violations(
    registry: dict[str, object],
    historical_paths: Iterable[str],
) -> list[str]:
    """Translate current-inventory policy failures into history findings.

    Absence of a public artifact is a current-tree concern and is intentionally
    left to ``check_public_release.py``. This gate only asks whether a blocked
    payload remains retrievable from any reachable commit.
    """

    violations: list[str] = []
    for violation in release_violations(registry, historical_paths):
        if "is absent from Git inventory" in violation:
            continue
        violations.append(
            violation.replace(
                "is Git-tracked",
                "appears in reachable Git history",
            )
        )
    return sorted(set(violations))


def main() -> int:
    try:
        registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        commits = reachable_commits()
        paths = reachable_history_paths(commits)
        violations = history_violations(registry, paths)
    except Exception as exc:
        print(
            "Git history release gate: BLOCKED\n"
            f"- unexpected verification failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    if violations:
        print("Git history release gate: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    print(
        "Git history release gate: PASS "
        f"({len(commits)} reachable commits; {len(paths)} unique paths)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
