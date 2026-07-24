#!/usr/bin/env python3
"""Reject ambiguous partially staged paths before assembling reviewed commits."""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class StatusEntry:
    kind: str
    index: str
    worktree: str
    path: str


def parse_porcelain_v2(lines: Iterable[str]) -> list[StatusEntry]:
    """Parse the status fields needed for a commit-coherence decision."""

    entries: list[StatusEntry] = []
    for line in lines:
        if not line:
            continue
        kind = line[0]
        if kind == "#":
            continue
        if kind == "?":
            entries.append(StatusEntry("untracked", "?", "?", line[2:]))
            continue
        if kind == "!":
            continue
        if kind == "1":
            fields = line.split(" ", 8)
            if len(fields) != 9 or len(fields[1]) != 2:
                raise ValueError(f"invalid ordinary status record: {line!r}")
            entries.append(
                StatusEntry("ordinary", fields[1][0], fields[1][1], fields[8])
            )
            continue
        if kind == "2":
            fields = line.split(" ", 9)
            if len(fields) != 10 or len(fields[1]) != 2:
                raise ValueError(f"invalid rename status record: {line!r}")
            entries.append(
                StatusEntry("rename", fields[1][0], fields[1][1], fields[9])
            )
            continue
        if kind == "u":
            fields = line.split(" ", 10)
            path = fields[-1] if len(fields) == 11 else "<unknown>"
            entries.append(StatusEntry("unmerged", "U", "U", path))
            continue
        raise ValueError(f"unsupported porcelain-v2 status record: {line!r}")
    return entries


def coherence_violations(entries: Iterable[StatusEntry]) -> list[str]:
    violations: list[str] = []
    for entry in entries:
        if entry.kind == "unmerged":
            violations.append(f"unmerged path: {entry.path}")
        elif entry.index not in {".", "?"} and entry.worktree not in {".", "?"}:
            status = entry.index + entry.worktree
            if status == "AD":
                reason = "staged addition is absent from the worktree"
            else:
                reason = "index and worktree contain different changes"
            violations.append(f"partially staged {status}: {entry.path} ({reason})")
    return sorted(violations)


def git_status_lines() -> list[str]:
    result = subprocess.run(
        ["git", "status", "--porcelain=v2", "--untracked-files=all"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.splitlines()


def main() -> int:
    try:
        entries = parse_porcelain_v2(git_status_lines())
        violations = coherence_violations(entries)
    except Exception as exc:
        print(
            "Staging coherence: BLOCKED\n"
            f"- unexpected verification failure: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1

    if violations:
        print("Staging coherence: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    staged = sum(entry.index not in {".", "?"} for entry in entries)
    worktree = sum(entry.worktree not in {".", "?"} for entry in entries)
    untracked = sum(entry.kind == "untracked" for entry in entries)
    print(
        "Staging coherence: PASS "
        f"({staged} staged; {worktree} unstaged; {untracked} untracked paths)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
