#!/usr/bin/env python3
"""Verify the reviewed watchdog wheel is portable pure Python despite its tag."""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import stat
import sys
import zipfile
from pathlib import Path, PurePosixPath


EXPECTED_FILENAME = "watchdog-6.0.0-py3-none-manylinux2014_x86_64.whl"
EXPECTED_SHA256 = "20ffe5b202af80ab4266dcd3e91aae72bf2da48c0d33bdb15c66658e685e94e2"
DIST_INFO = "watchdog-6.0.0.dist-info"
EXPECTED_TAG = "Tag: py3-none-manylinux2014_x86_64"
FORBIDDEN_SUFFIXES = {
    ".a",
    ".dll",
    ".dylib",
    ".exe",
    ".lib",
    ".o",
    ".pyd",
    ".so",
}


class PureWatchdogWheelError(ValueError):
    """Raised when the reviewed wheel is not the expected pure payload."""


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _record_digest(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_wheel(path: Path) -> dict[str, int | str]:
    if not path.is_file() or path.is_symlink():
        raise PureWatchdogWheelError("watchdog wheel must be one regular file")
    if path.name != EXPECTED_FILENAME:
        raise PureWatchdogWheelError("watchdog wheel filename differs")
    payload = path.read_bytes()
    if _sha256(payload) != EXPECTED_SHA256:
        raise PureWatchdogWheelError("watchdog wheel SHA-256 differs")

    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if not infos or len(names) != len(set(names)):
                raise PureWatchdogWheelError(
                    "watchdog wheel entries are empty or duplicated"
                )
            for info in infos:
                member = PurePosixPath(info.filename)
                if (
                    member.is_absolute()
                    or ".." in member.parts
                    or "\\" in info.filename
                    or not member.parts
                ):
                    raise PureWatchdogWheelError("watchdog wheel has an unsafe path")
                if member.parts[0] not in {"watchdog", DIST_INFO}:
                    raise PureWatchdogWheelError(
                        "watchdog wheel has an unexpected top-level path"
                    )
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    raise PureWatchdogWheelError("watchdog wheel contains a symlink")
                if member.suffix.casefold() in FORBIDDEN_SUFFIXES:
                    raise PureWatchdogWheelError(
                        "watchdog wheel contains a native or executable artifact"
                    )

            wheel_text = archive.read(f"{DIST_INFO}/WHEEL").decode("utf-8")
            wheel_lines = wheel_text.splitlines()
            if "Root-Is-Purelib: true" not in wheel_lines:
                raise PureWatchdogWheelError("watchdog wheel is not purelib")
            tags = [line for line in wheel_lines if line.startswith("Tag: ")]
            if tags != [EXPECTED_TAG]:
                raise PureWatchdogWheelError("watchdog wheel tag differs")

            metadata = archive.read(f"{DIST_INFO}/METADATA").decode("utf-8")
            if "\nName: watchdog\n" not in f"\n{metadata}":
                raise PureWatchdogWheelError("watchdog metadata name differs")
            if "\nVersion: 6.0.0\n" not in f"\n{metadata}":
                raise PureWatchdogWheelError("watchdog metadata version differs")

            record_name = f"{DIST_INFO}/RECORD"
            rows = list(
                csv.reader(
                    io.StringIO(archive.read(record_name).decode("utf-8"))
                )
            )
            if len(rows) != len(infos):
                raise PureWatchdogWheelError("watchdog RECORD entry count differs")
            recorded: set[str] = set()
            for row in rows:
                if len(row) != 3 or row[0] in recorded:
                    raise PureWatchdogWheelError("watchdog RECORD is malformed")
                name, digest, size = row
                recorded.add(name)
                if name == record_name:
                    if digest or size:
                        raise PureWatchdogWheelError(
                            "watchdog RECORD self-entry must be unhashed"
                        )
                    continue
                member_payload = archive.read(name)
                if digest != f"sha256={_record_digest(member_payload)}":
                    raise PureWatchdogWheelError("watchdog RECORD hash differs")
                if size != str(len(member_payload)):
                    raise PureWatchdogWheelError("watchdog RECORD size differs")
            if recorded != set(names):
                raise PureWatchdogWheelError("watchdog RECORD inventory differs")
    except (KeyError, UnicodeDecodeError, zipfile.BadZipFile) as exc:
        raise PureWatchdogWheelError("watchdog wheel is unreadable") from exc

    return {
        "filename": path.name,
        "bytes": len(payload),
        "sha256": EXPECTED_SHA256,
        "entries": len(names),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args(argv)
    try:
        identity = validate_wheel(args.wheel)
    except (OSError, PureWatchdogWheelError) as exc:
        print(f"Pure watchdog wheel: BLOCKED\n- {exc}", file=sys.stderr)
        return 1
    print(
        "Pure watchdog wheel: PASS "
        f"({identity['entries']} entries; sha256:{identity['sha256']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
