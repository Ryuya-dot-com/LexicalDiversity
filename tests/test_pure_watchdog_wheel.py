import base64
import csv
import hashlib
import io
import zipfile
from pathlib import Path

import pytest

from scripts import check_pure_watchdog_wheel as pure_wheel


def _record_digest(payload: bytes) -> str:
    digest = hashlib.sha256(payload).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _write_wheel(path: Path, *, native_artifact: bool = False) -> None:
    dist_info = pure_wheel.DIST_INFO
    files = {
        "watchdog/__init__.py": b'__version__ = "6.0.0"\n',
        f"{dist_info}/WHEEL": (
            b"Wheel-Version: 1.0\n"
            b"Root-Is-Purelib: true\n"
            b"Tag: py3-none-manylinux2014_x86_64\n"
        ),
        f"{dist_info}/METADATA": (
            b"Metadata-Version: 2.1\nName: watchdog\nVersion: 6.0.0\n"
        ),
    }
    if native_artifact:
        files["watchdog/native.so"] = b"not-a-real-shared-object"
    record_name = f"{dist_info}/RECORD"
    rows = [
        (name, f"sha256={_record_digest(payload)}", str(len(payload)))
        for name, payload in files.items()
    ]
    rows.append((record_name, "", ""))
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    files[record_name] = record.getvalue().encode("utf-8")

    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, payload in files.items():
            archive.writestr(name, payload)


def _review_test_wheel(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(pure_wheel, "EXPECTED_FILENAME", path.name)
    monkeypatch.setattr(
        pure_wheel,
        "EXPECTED_SHA256",
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_reviewed_watchdog_wheel_accepts_a_complete_purelib_record(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    wheel = tmp_path / pure_wheel.EXPECTED_FILENAME
    _write_wheel(wheel)
    _review_test_wheel(monkeypatch, wheel)

    identity = pure_wheel.validate_wheel(wheel)

    assert identity["entries"] == 4
    assert identity["bytes"] == wheel.stat().st_size


def test_reviewed_watchdog_wheel_rejects_native_payloads(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    wheel = tmp_path / pure_wheel.EXPECTED_FILENAME
    _write_wheel(wheel, native_artifact=True)
    _review_test_wheel(monkeypatch, wheel)

    with pytest.raises(pure_wheel.PureWatchdogWheelError, match="native"):
        pure_wheel.validate_wheel(wheel)


def test_reviewed_watchdog_wheel_rejects_unreviewed_bytes(
    tmp_path: Path,
):
    wheel = tmp_path / pure_wheel.EXPECTED_FILENAME
    _write_wheel(wheel)

    with pytest.raises(pure_wheel.PureWatchdogWheelError, match="SHA-256"):
        pure_wheel.validate_wheel(wheel)
