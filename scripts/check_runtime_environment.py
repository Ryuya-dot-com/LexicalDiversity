#!/usr/bin/env python3
"""Fail closed when the clean runtime differs from the reviewed contract.

This check deliberately treats ``requirements.txt`` as a developer convenience,
not as a reproducible environment. Runtime versions come from the exact pins in
``deploy/cloud-run/requirements-prod.txt`` and artifact identities come from the
single-hash Linux wheel lock. CI installs the corresponding hash-locked graph
with dependency resolution disabled, then runs this check and ``pip check``.

The release contract is exactly CPython 3.12.10. Security upgrades are explicit
reviewed lock migrations, not silent patch drift. The production container also
pins its exact linux/amd64 child manifest digest.
"""
from __future__ import annotations

import importlib
import importlib.metadata
import re
import sys
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from types import ModuleType


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_REQUIREMENTS = PROJECT_ROOT / "deploy" / "cloud-run" / "requirements-prod.txt"
PRODUCTION_WHEEL_LOCK = (
    PROJECT_ROOT / "deploy" / "cloud-run" / "requirements-prod-linux-x86_64.lock"
)
DEVELOPMENT_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
if str(PROJECT_ROOT) not in sys.path:
    # Direct execution sets sys.path[0] to scripts/.  Import the reviewed local
    # package explicitly so the TUBELEX code pin is checked in the same command
    # used by CI and the container build context.
    sys.path.insert(0, str(PROJECT_ROOT))

EXPECTED_IMPLEMENTATION = "cpython"
EXPECTED_PYTHON_VERSION = (3, 12, 10)
EXPECTED_PYTHON_SERIES = EXPECTED_PYTHON_VERSION[:2]
AUDITED_NLTK_VERSION = "3.10.0"

_DISTRIBUTION_NAME = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?\Z"
)
_DECLARATION = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"(?P<specifier>.*)\Z"
)
_HASHED_DECLARATION = re.compile(
    r"(?P<name>[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?)"
    r"==(?P<version>[^\s;#@\[\]]+)"
    r" --hash=sha256:(?P<digest>[0-9a-f]{64})\Z"
)
CORE_IMPORTS = (
    "nltk",
    "numpy",
    "pandas",
    "plotly",
    "openpyxl",
    "simplemma",
    "streamlit",
)


class RuntimeContractError(ValueError):
    """Raised when a lock file is missing, ambiguous, or not fully pinned."""


def canonical_package_name(name: str) -> str:
    """Return the PEP 503 canonical distribution name."""

    return re.sub(r"[-_.]+", "-", name).lower()


def parse_exact_pins(lines: Iterable[str], *, source: str) -> dict[str, str]:
    """Parse only unique, exact ``name==version`` pins from text lines."""

    pins: dict[str, str] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            raise RuntimeContractError(
                f"{source}:{line_number} is not one exact name==version pin"
            )
        name, version = (part.strip() for part in line.split("==", 1))
        if (
            not _DISTRIBUTION_NAME.fullmatch(name)
            or not version
            or any(character.isspace() for character in version)
            or any(marker in version for marker in (";", "#", "@", "[", "]"))
        ):
            raise RuntimeContractError(
                f"{source}:{line_number} contains an unsupported requirement"
            )
        canonical = canonical_package_name(name)
        if canonical in pins:
            raise RuntimeContractError(
                f"{source}:{line_number} duplicates distribution {canonical}"
            )
        pins[canonical] = version

    if not pins:
        raise RuntimeContractError(f"required lock file has no pins: {source}")
    return pins


def read_exact_pins(path: Path) -> dict[str, str]:
    """Read one flat requirements file containing only exact ``name==version`` pins."""

    if not path.is_file():
        raise RuntimeContractError(f"required lock file is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeContractError(f"required lock file is unreadable: {path}") from exc
    return parse_exact_pins(lines, source=str(path))


def read_hashed_lock(path: Path) -> dict[str, tuple[str, str]]:
    """Read one lock permitting exactly one SHA-256 wheel artifact per pin."""

    if not path.is_file():
        raise RuntimeContractError(f"required wheel lock is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeContractError(f"required wheel lock is unreadable: {path}") from exc

    artifacts: dict[str, tuple[str, str]] = {}
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _HASHED_DECLARATION.fullmatch(line)
        if match is None:
            raise RuntimeContractError(
                f"{path}:{line_number} is not one exact pin with one SHA-256 hash"
            )
        package = canonical_package_name(match.group("name"))
        if package in artifacts:
            raise RuntimeContractError(
                f"{path}:{line_number} duplicates distribution {package}"
            )
        artifacts[package] = (match.group("version"), match.group("digest"))
    if not artifacts:
        raise RuntimeContractError(f"required wheel lock has no artifacts: {path}")
    return artifacts


def declared_specifiers(path: Path, package: str) -> list[str]:
    """Return all direct declarations for ``package`` in a developer requirements file."""

    if not path.is_file():
        raise RuntimeContractError(f"requirements file is missing: {path}")
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeContractError(f"requirements file is unreadable: {path}") from exc

    target = canonical_package_name(package)
    found: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = _DECLARATION.fullmatch(line)
        if match and canonical_package_name(match.group("name")) == target:
            found.append(match.group("specifier").strip())
    return found


def python_contract_violations(
    *,
    implementation: str,
    version: tuple[int, int, int],
    releaselevel: str,
) -> list[str]:
    """Return Python interpreter contract violations."""

    violations: list[str] = []
    if implementation != EXPECTED_IMPLEMENTATION:
        violations.append(
            f"Python implementation is {implementation!r}; expected CPython"
        )
    if version != EXPECTED_PYTHON_VERSION:
        actual = ".".join(str(part) for part in version)
        expected = ".".join(str(part) for part in EXPECTED_PYTHON_VERSION)
        violations.append(f"Python is {actual}; expected exactly {expected}")
    if releaselevel != "final":
        violations.append(
            f"Python release level is {releaselevel!r}; expected a final release"
        )
    return violations


def installed_pin_violations(
    pins: Mapping[str, str],
    *,
    version_resolver: Callable[[str], str] = importlib.metadata.version,
) -> list[str]:
    """Return missing or mismatched installed-distribution violations."""

    violations: list[str] = []
    for package, expected in sorted(pins.items()):
        try:
            actual = version_resolver(package)
        except importlib.metadata.PackageNotFoundError:
            violations.append(f"locked distribution is not installed: {package}=={expected}")
            continue
        except Exception as exc:  # pragma: no cover - defensive resolver boundary
            violations.append(
                f"installed version could not be verified for {package}: "
                f"{type(exc).__name__}"
            )
            continue
        if actual != expected:
            violations.append(
                f"installed distribution mismatch: {package}=={actual}; expected {expected}"
            )
    return violations


def import_probe_violations(
    *,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> list[str]:
    """Import core runtime modules so incompatible wheels fail before tests."""

    violations: list[str] = []
    for module_name in CORE_IMPORTS:
        try:
            importer(module_name)
        except Exception as exc:
            violations.append(
                f"core runtime import failed for {module_name}: {type(exc).__name__}"
            )
    return violations


def nltk_contract_violations(
    pins: Mapping[str, str],
    *,
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> list[str]:
    """Cross-check the lock, imported NLTK, and TUBELEX audited runtime pin."""

    violations: list[str] = []
    locked = pins.get("nltk")
    if locked != AUDITED_NLTK_VERSION:
        violations.append(
            f"production NLTK pin is {locked!r}; expected {AUDITED_NLTK_VERSION}"
        )

    try:
        nltk = importer("nltk")
    except Exception as exc:
        violations.append(f"NLTK import failed: {type(exc).__name__}")
        return violations
    imported = str(getattr(nltk, "__version__", ""))
    if imported != AUDITED_NLTK_VERSION:
        violations.append(
            f"imported NLTK is {imported!r}; expected {AUDITED_NLTK_VERSION}"
        )

    try:
        tubelex = importer("ldfreq.tubelex")
    except Exception as exc:
        violations.append(f"TUBELEX runtime contract import failed: {type(exc).__name__}")
        return violations
    code_pin = str(getattr(tubelex, "TUBELEX_PRODUCTION_NLTK_VERSION", ""))
    audited = set(getattr(tubelex, "TUBELEX_AUDITED_NLTK_VERSIONS", ()))
    if code_pin != AUDITED_NLTK_VERSION:
        violations.append(
            f"TUBELEX production NLTK pin is {code_pin!r}; "
            f"expected {AUDITED_NLTK_VERSION}"
        )
    if audited != {AUDITED_NLTK_VERSION}:
        violations.append(
            "TUBELEX audited NLTK set differs from the single production pin"
        )
    return violations


def runtime_environment_violations() -> tuple[list[str], dict[str, str]]:
    """Evaluate the fixed interpreter, lock-file, package, and import contracts."""

    info = sys.version_info
    violations = python_contract_violations(
        implementation=sys.implementation.name,
        version=(info.major, info.minor, info.micro),
        releaselevel=info.releaselevel,
    )

    try:
        pins = read_exact_pins(PRODUCTION_REQUIREMENTS)
    except RuntimeContractError as exc:
        violations.append(str(exc))
        return violations, {}

    try:
        wheel_artifacts = read_hashed_lock(PRODUCTION_WHEEL_LOCK)
    except RuntimeContractError as exc:
        violations.append(str(exc))
    else:
        wheel_pins = {
            package: version for package, (version, _digest) in wheel_artifacts.items()
        }
        if wheel_pins != pins:
            violations.append(
                "production wheel lock pins differ from requirements-prod.txt"
            )

    if pins.get("nltk") != AUDITED_NLTK_VERSION:
        violations.append(
            f"production lock must contain nltk=={AUDITED_NLTK_VERSION}"
        )
    try:
        development_nltk = declared_specifiers(DEVELOPMENT_REQUIREMENTS, "nltk")
    except RuntimeContractError as exc:
        violations.append(str(exc))
    else:
        if development_nltk != [f"=={AUDITED_NLTK_VERSION}"]:
            violations.append(
                "requirements.txt must contain exactly one matching exact NLTK pin; "
                "it is not the reproducible runtime lock"
            )

    violations.extend(installed_pin_violations(pins))
    violations.extend(import_probe_violations())
    violations.extend(nltk_contract_violations(pins))
    return violations, pins


def main() -> int:
    try:
        violations, pins = runtime_environment_violations()
    except Exception as exc:  # pragma: no cover - final fail-closed boundary
        print(
            "Runtime environment contract: BLOCKED\n"
            f"- unexpected verification failure: {type(exc).__name__}",
            file=sys.stderr,
        )
        return 1

    if violations:
        print("Runtime environment contract: BLOCKED", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1

    version = ".".join(str(part) for part in sys.version_info[:3])
    print(
        "Runtime environment contract: PASS "
        f"(CPython {version}; {len(pins)} exact runtime pins; "
        f"NLTK {AUDITED_NLTK_VERSION})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
