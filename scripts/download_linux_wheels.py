#!/usr/bin/env python3
"""Download the exact CPython 3.12/Linux x86_64 wheel candidate set."""
from __future__ import annotations

import argparse
import importlib.metadata
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.build_linux_wheel_locks import WATCHDOG_PACKAGE, build_outputs
from scripts.check_runtime_environment import (
    PRODUCTION_REQUIREMENTS,
    RuntimeContractError,
    read_exact_pins,
)


EXPECTED_PYTHON = (3, 12, 13)
EXPECTED_PIP = "25.0.1"
# Ubuntu 24.04 CI uses glibc 2.39. Listing compatible policy tags newest-first
# makes wheel selection explicit instead of incorrectly treating 2.28 as the
# oldest usable ABI (for example, rpds-py is published for manylinux_2_17).
CI_PLATFORMS = tuple(
    f"manylinux_2_{minor}_x86_64" for minor in range(39, 16, -1)
) + (
    "manylinux2014_x86_64",
    "manylinux_2_12_x86_64",
    "manylinux2010_x86_64",
    "manylinux_2_5_x86_64",
    "manylinux1_x86_64",
    "linux_x86_64",
)
ABIS = ("cp312", "abi3", "none")
PRODUCTION_PLATFORM = "musllinux_1_2_x86_64"
WATCHDOG_PLATFORM = "manylinux2014_x86_64"


def _download_command(destination: Path, *, platforms: tuple[str, ...]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip",
        "download",
        "--isolated",
        "--disable-pip-version-check",
        "--no-cache-dir",
        "--no-deps",
        "--only-binary=:all:",
    ]
    for platform in platforms:
        command.extend(("--platform", platform))
    command.extend(("--implementation", "cp", "--python-version", "3.12"))
    for abi in ABIS:
        command.extend(("--abi", abi))
    command.extend(("--dest", str(destination)))
    return command


def download_commands(destination: Path, *, profile: str) -> list[list[str]]:
    if profile == "ci":
        command = _download_command(destination, platforms=CI_PLATFORMS)
        command.extend(
            ("--requirement", str(PROJECT_ROOT / "requirements-ci.txt"))
        )
        return [command]
    if profile == "production":
        pins = read_exact_pins(PRODUCTION_REQUIREMENTS)
        main = _download_command(
            destination,
            platforms=(PRODUCTION_PLATFORM,),
        )
        main.extend(
            f"{package}=={version}"
            for package, version in pins.items()
            if package != WATCHDOG_PACKAGE
        )
        watchdog = _download_command(
            destination,
            platforms=(WATCHDOG_PLATFORM,),
        )
        watchdog.append(f"{WATCHDOG_PACKAGE}=={pins[WATCHDOG_PACKAGE]}")
        return [main, watchdog]
    raise RuntimeContractError(f"unsupported wheel-download profile: {profile}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--profile", choices=("production", "ci"), required=True)
    parser.add_argument(
        "--allow-lock-change",
        action="store_true",
        help="retain a newly selected set for intentional lock migration",
    )
    args = parser.parse_args(argv)

    try:
        actual_python = sys.version_info[:3]
        if actual_python != EXPECTED_PYTHON or sys.implementation.name != "cpython":
            raise RuntimeContractError(
                f"wheel selection requires CPython 3.12.13; got "
                f"{sys.implementation.name} {'.'.join(map(str, actual_python))}"
            )
        actual_pip = importlib.metadata.version("pip")
        if actual_pip != EXPECTED_PIP:
            raise RuntimeContractError(
                f"wheel selection requires pip {EXPECTED_PIP}; got {actual_pip}"
            )
        if args.dest.exists() and any(args.dest.iterdir()):
            raise RuntimeContractError(f"destination must be empty: {args.dest}")
        args.dest.mkdir(parents=True, exist_ok=True)
        for command in download_commands(args.dest, profile=args.profile):
            subprocess.run(command, cwd=PROJECT_ROOT, check=True)
        # This rejects extra, missing, duplicate, or wrong-version artifacts.
        outputs = build_outputs(args.dest, profile=args.profile)
        changed = [
            path
            for path, expected in outputs.items()
            if not path.is_file() or path.read_text(encoding="utf-8") != expected
        ]
        if changed and not args.allow_lock_change:
            raise RuntimeContractError(
                "downloaded selection differs from committed lock: "
                + ", ".join(str(path) for path in changed)
            )
    except (OSError, RuntimeContractError, subprocess.CalledProcessError) as exc:
        print(f"Linux wheel download: BLOCKED\n- {exc}", file=sys.stderr)
        return 1

    print(f"Linux wheel download: PASS ({len(tuple(args.dest.glob('*.whl')))} wheels)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
