#!/usr/bin/env python3
"""Fail closed when the Git release inventory violates resource governance."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data" / "resource_registry.json"
SERVER_ONLY_TEMPLATE_PATH = "deploy/cloud-run/service.template.yaml"
SERVER_ONLY_TEMPLATE_DEFAULTS = {
    "LDFREQ_SERVING_MODE": "public",
    "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
    "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "",
    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "0",
    "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION": "",
    "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID": "",
}
PERMISSION_ASSURANCE_SCHEMA_VERSION = "1.0.0"
CUSTOM_PERMISSION_MARKER = "custom-permission"
KNOWN_CUSTOM_PERMISSION_RESOURCE_IDS = frozenset({"nj8"})
PERMISSION_ASSURANCE_STATUSES = frozenset(
    {"review-pending", "independently-reviewed"}
)
PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES = frozenset(
    {
        "github-repository-redistribution",
        "downstream-fork-redistribution",
        "release-archive-distribution",
        "container-image-distribution",
        "public-saas-processing",
        "transformation-and-derived-results",
        "commercial-use",
        "noncommercial-use",
        "revocation-or-expiry-terms",
    }
)
PERMISSION_ASSURANCE_REQUIRED_EXTERNAL_RECORD_FIELDS = frozenset(
    {
        "record_id",
        "record_sha256",
        "record_editor",
        "grantor",
        "grantor_authority",
        "granted_on",
        "scopes",
        "artifact_bindings",
        "revocation_or_expiry_terms",
    }
)
PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS = frozenset(
    {
        "status",
        "reviewer",
        "reviewer_role",
        "reviewed_on",
        "decision_reference",
        "verified_scopes",
    }
)
PERMISSION_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FORBIDDEN_TRACKED_PAYLOAD_SHA256 = {
    # Exact locally held artifact bound to the NJ8 owner attestation. This is a
    # payload exclusion identity, not a mirror-equivalence assertion.
    "1433dcd94135f86cfdbcbf5bafc209661678f1104f5062041b737834e99d3cf8": "nj8",
}
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
    "data/NJ8/": {"data/NJ8/manifest.json"},
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
QUARANTINED_DERIVED_PREFIXES = (
    "r-package/experiments/ellipse-external-association/",
    "r-package/experiments/taales-coca-convergence/",
)
QUARANTINED_DERIVED_FILES = {
    "benchmarks/ellipse/taales-coca-convergence-plan.json",
    "scripts/analyze_taales_coca_convergence.R",
    "scripts/prepare_ellipse_taales_convergence.py",
    "scripts/run_taales_legacy_coca.py",
    "tests/test_taales_coca_convergence.py",
}
QUARANTINED_DERIVED_FAMILIES = (
    "ellipse-external-association-pending-review",
    "legacy-taales-coca-convergence",
)
DERIVED_RESULT_MANIFEST_SCHEMA_VERSION = "1.0.0"
DERIVED_RESULT_PUBLIC_ROOTS = frozenset({"results/public"})
DERIVED_RESULT_REQUIRED_REGISTRY_FIELDS = frozenset(
    {
        "id",
        "root",
        "public_build",
        "manifest",
        "manifest_bytes",
        "manifest_sha256",
        "expected_upstream_resource_ids",
        "publication_status",
    }
)
DERIVED_RESULT_REQUIRED_MANIFEST_FIELDS = frozenset(
    {
        "schema_version",
        "bundle",
        "artifacts",
        "upstream_resources",
        "provenance",
        "aggregation",
        "attribution",
        "publication_review",
    }
)
DERIVED_RESULT_PUBLICATION_STATUSES = frozenset(
    {"approved", "review-required", "blocked"}
)
AGGREGATE_PUBLICATION_REQUIREMENTS = {
    "allowed": frozenset(),
    "allowed-with-citation": frozenset({"citation"}),
    "allowed-with-attribution": frozenset({"attribution"}),
    "allowed-with-attribution-and-disclosure-review": frozenset(
        {"attribution", "disclosure-review"}
    ),
}
ARTIFACT_CLASS_SUFFIXES = {
    "aggregate-table": (".csv", ".json"),
    "aggregate-figure": (".pdf", ".png", ".svg"),
    "aggregate-metadata": (".json",),
    "analysis-report": (".md",),
}
DERIVED_RESULT_ARTIFACT_CLASSES = frozenset(ARTIFACT_CLASS_SUFFIXES)
ARCHIVE_MAGIC_PREFIXES = (
    b"PK\x03\x04",
    b"PK\x05\x06",
    b"PK\x07\x08",
    b"\x1f\x8b",
    b"7z\xbc\xaf\x27\x1c",
    b"Rar!\x1a\x07",
)


def _normalized(path: str) -> str:
    return PurePosixPath(path.replace("\\", "/")).as_posix().rstrip("/")


def server_only_template_violations(payload: bytes | str) -> list[str]:
    """Reject a checked-in deployment template that enables private resources."""

    try:
        text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
    except UnicodeDecodeError:
        return ["server-only deployment template is not valid UTF-8"]
    if not isinstance(text, str):
        return ["server-only deployment template payload is invalid"]

    observed: dict[str, list[str]] = {
        name: [] for name in SERVER_ONLY_TEMPLATE_DEFAULTS
    }
    current_name: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- name:"):
            current_name = stripped.split(":", 1)[1].strip()
            continue
        if current_name in observed and stripped.startswith("value:"):
            value = stripped.split(":", 1)[1].strip()
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {'"', "'"}
            ):
                value = value[1:-1]
            observed[current_name].append(value)
            current_name = None

    violations: list[str] = []
    for name, expected in SERVER_ONLY_TEMPLATE_DEFAULTS.items():
        values = observed[name]
        if len(values) != 1:
            violations.append(
                f"server-only deployment template must define {name} exactly once"
            )
        elif values[0] != expected:
            violations.append(
                "server-only deployment template is not fail-closed: "
                f"{name} must default to {expected!r}"
            )
    return violations


def _safe_relative(path: str) -> bool:
    pure = PurePosixPath(path.replace("\\", "/"))
    return bool(pure.parts) and not pure.is_absolute() and ".." not in pure.parts


def _under(path: str, root: str) -> bool:
    normalized_path = _normalized(path)
    normalized_root = _normalized(root)
    return normalized_path == normalized_root or normalized_path.startswith(
        f"{normalized_root}/"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _payload_for(
    path: str,
    *,
    project_root: Path,
    file_payloads: Mapping[str, bytes] | None,
) -> bytes:
    normalized = _normalized(path)
    if file_payloads is not None:
        if normalized not in file_payloads:
            raise ValueError("file bytes are absent from the reviewed snapshot")
        return file_payloads[normalized]
    local = project_root / normalized
    if local.is_symlink() or not local.is_file():
        raise ValueError("file is absent or is not a regular file")
    return local.read_bytes()


def _valid_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _identity_violations(
    identity: object,
    *,
    label: str,
    tracked: set[str],
    project_root: Path,
    file_payloads: Mapping[str, bytes] | None,
) -> list[str]:
    if not isinstance(identity, dict):
        return [f"derived result {label} identity is not an object"]
    path_value = identity.get("path")
    if not isinstance(path_value, str) or not _safe_relative(path_value):
        return [f"derived result {label} path is unsafe or missing"]
    path = _normalized(path_value)
    if path not in tracked:
        return [f"derived result {label} is absent from Git inventory: {path}"]
    expected_bytes = identity.get("bytes")
    expected_hash = identity.get("sha256")
    if type(expected_bytes) is not int or expected_bytes < 0:
        return [f"derived result {label} has invalid byte identity: {path}"]
    if not isinstance(expected_hash, str) or len(expected_hash) != 64:
        return [f"derived result {label} has invalid SHA-256 identity: {path}"]
    try:
        payload = _payload_for(
            path,
            project_root=project_root,
            file_payloads=file_payloads,
        )
    except ValueError as exc:
        return [f"derived result {label} cannot be verified: {path} ({exc})"]
    violations: list[str] = []
    if len(payload) != expected_bytes:
        violations.append(f"derived result {label} byte identity differs: {path}")
    if _sha256(payload) != expected_hash.lower():
        violations.append(f"derived result {label} SHA-256 differs: {path}")
    return violations


def _has_archive_magic(payload: bytes) -> bool:
    if any(payload.startswith(prefix) for prefix in ARCHIVE_MAGIC_PREFIXES):
        return True
    return len(payload) >= 262 and payload[257:262] == b"ustar"


def result_bundle_review(
    registry: dict[str, object],
    tracked_paths: Iterable[str],
    *,
    project_root: Path = PROJECT_ROOT,
    file_payloads: Mapping[str, bytes] | None = None,
    validate_content: bool = True,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Validate declared public result bundles and return evidence summaries."""

    contract = registry.get("derived_result_contract")
    declarations = registry.get("derived_result_bundles")
    if contract is None and declarations is None:
        return [], []
    if not isinstance(contract, dict) or not isinstance(declarations, list):
        return ["derived-result registry contract is missing or malformed"], []

    tracked = {_normalized(path) for path in tracked_paths if path}
    violations: list[str] = []
    summaries: list[dict[str, Any]] = []
    public_roots_raw = contract.get("public_roots")
    required_registry_fields = contract.get("required_registry_fields")
    required_manifest_fields = contract.get("required_manifest_fields")
    allowed_classes_raw = contract.get("artifact_classes")
    public_roots = []
    if isinstance(public_roots_raw, list):
        for item in public_roots_raw:
            if not isinstance(item, str) or not _safe_relative(item):
                violations.append("derived-result public root is unsafe")
                continue
            public_roots.append(_normalized(item))
    if (
        len(public_roots) != len(DERIVED_RESULT_PUBLIC_ROOTS)
        or set(public_roots) != DERIVED_RESULT_PUBLIC_ROOTS
    ):
        violations.append("derived-result public root contract differs")
    public_roots = sorted(DERIVED_RESULT_PUBLIC_ROOTS)

    required_registry = (
        {str(item) for item in required_registry_fields}
        if isinstance(required_registry_fields, list)
        else set()
    )
    if required_registry != DERIVED_RESULT_REQUIRED_REGISTRY_FIELDS:
        violations.append("derived-result registry field contract differs")
    required_registry = set(DERIVED_RESULT_REQUIRED_REGISTRY_FIELDS)

    required_manifest = (
        {str(item) for item in required_manifest_fields}
        if isinstance(required_manifest_fields, list)
        else set()
    )
    if required_manifest != DERIVED_RESULT_REQUIRED_MANIFEST_FIELDS:
        violations.append("derived-result manifest field contract differs")
    required_manifest = set(DERIVED_RESULT_REQUIRED_MANIFEST_FIELDS)

    allowed_classes = (
        {str(item) for item in allowed_classes_raw}
        if isinstance(allowed_classes_raw, list)
        else set()
    )
    if allowed_classes != DERIVED_RESULT_ARTIFACT_CLASSES:
        violations.append("derived-result artifact class contract differs")
    allowed_classes = set(DERIVED_RESULT_ARTIFACT_CLASSES)
    if contract.get("manifest_schema_version") != DERIVED_RESULT_MANIFEST_SCHEMA_VERSION:
        violations.append("derived-result manifest schema contract differs")
    statuses_raw = contract.get("publication_statuses")
    statuses = (
        {str(item) for item in statuses_raw}
        if isinstance(statuses_raw, list)
        else set()
    )
    if statuses != DERIVED_RESULT_PUBLICATION_STATUSES:
        violations.append("derived-result publication status contract differs")

    resources: dict[str, dict[str, Any]] = {}
    for resource in registry.get("resources", []):
        if not isinstance(resource, dict):
            continue
        resource_id = resource.get("id")
        if not isinstance(resource_id, str) or not resource_id:
            continue
        if resource_id in resources:
            violations.append(f"duplicate resource ID in registry: {resource_id}")
        resources[resource_id] = resource

    ids: set[str] = set()
    roots: set[str] = set()
    public_declarations: list[tuple[dict[str, Any], str]] = []
    for raw in declarations:
        if not isinstance(raw, dict):
            violations.append("derived-result bundle declaration is not an object")
            continue
        missing = sorted(required_registry - set(raw))
        raw_bundle_id = raw.get("id")
        bundle_id = (
            raw_bundle_id
            if isinstance(raw_bundle_id, str) and raw_bundle_id
            else "<missing-id>"
        )
        if missing:
            violations.append(
                f"derived-result declaration is incomplete: {bundle_id} -> {', '.join(missing)}"
            )
            continue
        if bundle_id == "<missing-id>":
            violations.append("derived-result bundle ID is missing")
            continue
        root_value = raw.get("root")
        if not isinstance(root_value, str) or not _safe_relative(root_value):
            violations.append(f"derived-result root is unsafe: {bundle_id}")
            continue
        root = _normalized(root_value)
        if bundle_id in ids:
            violations.append(f"duplicate derived-result bundle ID: {bundle_id}")
        if root in roots:
            violations.append(f"duplicate derived-result bundle root: {root}")
        ids.add(bundle_id)
        roots.add(root)
        expected_ids = raw.get("expected_upstream_resource_ids")
        if (
            not isinstance(expected_ids, list)
            or not expected_ids
            or any(not isinstance(item, str) or not item for item in expected_ids)
            or len(expected_ids) != len(set(expected_ids))
        ):
            violations.append(
                f"derived-result expected upstream IDs are invalid: {bundle_id}"
            )
            expected_id_set: set[str] = set()
        else:
            expected_id_set = set(expected_ids)
        for resource_id in sorted(expected_id_set - set(resources)):
            violations.append(
                f"derived-result declaration names unknown upstream: "
                f"{bundle_id} -> {resource_id}"
            )
        if type(raw.get("public_build")) is not bool:
            violations.append(
                f"derived-result public_build is not boolean: {bundle_id}"
            )
        publication_status = raw.get("publication_status")
        if publication_status not in DERIVED_RESULT_PUBLICATION_STATUSES:
            violations.append(
                f"derived-result publication status is invalid: {bundle_id}"
            )
        selected = sorted(path for path in tracked if _under(path, root))
        if raw.get("public_build") is not True:
            if raw.get("manifest") is not None or raw.get("manifest_bytes") is not None or raw.get("manifest_sha256") is not None:
                violations.append(
                    f"blocked derived-result bundle records a public manifest: {bundle_id}"
                )
            if publication_status == "approved":
                violations.append(
                    f"nonpublic derived-result bundle claims approval: {bundle_id}"
                )
            for path in selected:
                violations.append(
                    f"quarantined derived result is Git-tracked: {bundle_id} -> {path}"
                )
            continue
        if publication_status != "approved":
            violations.append(
                f"public derived-result bundle lacks approved registry status: {bundle_id}"
            )
        if not any(_under(root, public_root) for public_root in public_roots):
            violations.append(
                f"public derived-result bundle is outside controlled roots: {bundle_id} -> {root}"
            )
        public_declarations.append((raw, root))

    for path in sorted(tracked):
        if not any(_under(path, root) for root in public_roots):
            continue
        matches = [
            str(raw.get("id"))
            for raw, root in public_declarations
            if _under(path, root)
        ]
        if len(matches) != 1:
            violations.append(
                f"unregistered or ambiguous public derived result is Git-tracked: {path}"
            )

    if not validate_content:
        return sorted(set(violations)), []

    for raw, root in public_declarations:
        bundle_id = str(raw["id"])
        try:
            manifest_value = raw.get("manifest")
            if not isinstance(manifest_value, str) or not _safe_relative(manifest_value):
                raise ValueError("manifest path is unsafe or missing")
            manifest_path = _normalized(manifest_value)
            if not _under(manifest_path, root):
                raise ValueError("manifest path is outside the bundle root")
            if manifest_path not in tracked:
                raise ValueError("manifest is absent from Git inventory")
            manifest_payload = _payload_for(
                manifest_path,
                project_root=project_root,
                file_payloads=file_payloads,
            )
            if raw.get("manifest_bytes") != len(manifest_payload):
                raise ValueError("manifest byte identity differs")
            expected_manifest_hash = raw.get("manifest_sha256")
            if (
                not isinstance(expected_manifest_hash, str)
                or _sha256(manifest_payload) != expected_manifest_hash.lower()
            ):
                raise ValueError("manifest SHA-256 differs")
            manifest = json.loads(manifest_payload.decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest is not an object")
            missing = sorted(required_manifest - set(manifest))
            if missing:
                raise ValueError("manifest fields are missing: " + ", ".join(missing))
            if manifest.get("schema_version") != DERIVED_RESULT_MANIFEST_SCHEMA_VERSION:
                raise ValueError("manifest schema version differs")
            bundle = manifest.get("bundle")
            if not isinstance(bundle, dict):
                raise ValueError("bundle identity is missing")
            if bundle.get("id") != bundle_id or _normalized(str(bundle.get("root", ""))) != root:
                raise ValueError("bundle identity differs from the registry")

            artifacts_raw = manifest.get("artifacts")
            if not isinstance(artifacts_raw, list) or not artifacts_raw:
                raise ValueError("artifact inventory is empty or malformed")
            artifact_paths: list[str] = []
            artifact_classes: set[str] = set()
            for index, artifact in enumerate(artifacts_raw):
                if not isinstance(artifact, dict):
                    raise ValueError(f"artifact {index} is not an object")
                path_value = artifact.get("path")
                artifact_class = artifact.get("artifact_class")
                if not isinstance(path_value, str) or not _safe_relative(path_value):
                    raise ValueError(f"artifact {index} path is unsafe")
                path = _normalized(path_value)
                if not _under(path, root) or path == manifest_path:
                    raise ValueError(f"artifact {index} is outside the bundle root")
                if path in artifact_paths:
                    raise ValueError(f"duplicate artifact path: {path}")
                if artifact_class not in allowed_classes:
                    raise ValueError(f"unknown artifact class: {artifact_class}")
                suffixes = ARTIFACT_CLASS_SUFFIXES.get(str(artifact_class), ())
                if not path.lower().endswith(suffixes):
                    raise ValueError(f"artifact extension is not allowed for its class: {path}")
                identity_findings = _identity_violations(
                    artifact,
                    label=f"artifact {index}",
                    tracked=tracked,
                    project_root=project_root,
                    file_payloads=file_payloads,
                )
                if identity_findings:
                    raise ValueError("; ".join(identity_findings))
                artifact_payload = _payload_for(
                    path,
                    project_root=project_root,
                    file_payloads=file_payloads,
                )
                if _has_archive_magic(artifact_payload):
                    raise ValueError(f"archive payload is prohibited in a result bundle: {path}")
                artifact_paths.append(path)
                artifact_classes.add(str(artifact_class))

            selected = {path for path in tracked if _under(path, root)}
            expected_selected = {manifest_path, *artifact_paths}
            if selected != expected_selected:
                extras = sorted(selected - expected_selected)
                missing_paths = sorted(expected_selected - selected)
                detail = []
                if extras:
                    detail.append("extra=" + ",".join(extras))
                if missing_paths:
                    detail.append("missing=" + ",".join(missing_paths))
                raise ValueError("bundle inventory differs: " + "; ".join(detail))

            expected_ids = sorted(str(item) for item in raw["expected_upstream_resource_ids"])
            upstream_raw = manifest.get("upstream_resources")
            if not isinstance(upstream_raw, list):
                raise ValueError("upstream resource inventory is malformed")
            upstream_by_id: dict[str, dict[str, Any]] = {}
            for item in upstream_raw:
                if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                    raise ValueError("upstream resource identity is malformed")
                resource_id = str(item["id"])
                if resource_id in upstream_by_id:
                    raise ValueError(f"duplicate upstream resource: {resource_id}")
                upstream_by_id[resource_id] = item
            if sorted(upstream_by_id) != expected_ids:
                raise ValueError("upstream resource IDs differ from the registry declaration")

            attribution_raw = manifest.get("attribution")
            if not isinstance(attribution_raw, list):
                raise ValueError("attribution inventory is malformed")
            attribution_ids = {
                str(item.get("resource_id"))
                for item in attribution_raw
                if isinstance(item, dict)
                and isinstance(item.get("resource_id"), str)
                and isinstance(item.get("text"), str)
                and item.get("text")
            }
            if attribution_ids != set(expected_ids) or len(attribution_raw) != len(expected_ids):
                raise ValueError("attribution does not cover every upstream resource exactly once")

            review = manifest.get("publication_review")
            if not isinstance(review, dict):
                raise ValueError("publication review is missing")
            if review.get("status") != "approved":
                raise ValueError("publication review is not approved")
            if not isinstance(review.get("authority"), str) or not review.get("authority"):
                raise ValueError("publication review authority is missing")
            if (
                not isinstance(review.get("approval_reference"), str)
                or not review.get("approval_reference")
            ):
                raise ValueError("publication review approval reference is missing")
            if not _valid_date(review.get("reviewed_on")):
                raise ValueError("publication review date is invalid")
            scopes_raw = review.get("scopes")
            if not isinstance(scopes_raw, list):
                raise ValueError("publication review scopes are malformed")
            scopes: dict[str, dict[str, Any]] = {}
            for scope in scopes_raw:
                if not isinstance(scope, dict) or not isinstance(scope.get("resource_id"), str):
                    raise ValueError("publication review scope is malformed")
                resource_id = str(scope["resource_id"])
                if resource_id in scopes:
                    raise ValueError(f"duplicate publication review scope: {resource_id}")
                scopes[resource_id] = scope
            if set(scopes) != set(expected_ids):
                raise ValueError("publication review scopes do not match upstream resources")

            for resource_id in expected_ids:
                resource = resources.get(resource_id)
                if resource is None:
                    raise ValueError(f"unknown upstream resource: {resource_id}")
                if (resource.get("status") or {}).get("level") != "green":
                    raise ValueError(f"upstream resource is not green: {resource_id}")
                if (resource.get("license") or {}).get("verified") is not True:
                    raise ValueError(f"upstream resource license is unverified: {resource_id}")
                decision = (resource.get("web_use") or {}).get(
                    "aggregate_result_publication"
                )
                requirements = AGGREGATE_PUBLICATION_REQUIREMENTS.get(decision)
                if requirements is None:
                    raise ValueError(
                        f"upstream aggregate publication is not allowed: {resource_id} ({decision})"
                    )
                upstream = upstream_by_id[resource_id]
                if upstream.get("registry_aggregate_result_publication") != decision:
                    raise ValueError(
                        f"upstream publication decision snapshot differs: {resource_id}"
                    )
                classes = upstream.get("artifact_classes")
                if (
                    not isinstance(classes, list)
                    or not classes
                    or any(item not in artifact_classes for item in classes)
                ):
                    raise ValueError(f"upstream artifact classes are invalid: {resource_id}")
                scope = scopes[resource_id]
                scope_classes = scope.get("artifact_classes")
                conditions = scope.get("conditions_satisfied")
                if not isinstance(scope_classes, list) or not set(classes) <= set(scope_classes):
                    raise ValueError(f"publication review class scope is incomplete: {resource_id}")
                if not isinstance(conditions, list) or not requirements <= set(conditions):
                    raise ValueError(f"publication review conditions are incomplete: {resource_id}")

            aggregation = manifest.get("aggregation")
            if not isinstance(aggregation, dict) or not isinstance(aggregation.get("unit"), str) or not aggregation.get("unit"):
                raise ValueError("aggregation unit is missing")
            for flag in (
                "contains_row_level_data",
                "contains_individual_predictions",
                "contains_fitted_models",
            ):
                if aggregation.get(flag) is not False:
                    raise ValueError(f"aggregation boundary is not fail-closed: {flag}")

            provenance = manifest.get("provenance")
            if not isinstance(provenance, dict):
                raise ValueError("provenance is missing")
            identity_findings = _identity_violations(
                provenance.get("plan"),
                label="plan",
                tracked=tracked,
                project_root=project_root,
                file_payloads=file_payloads,
            )
            generators = provenance.get("generators")
            environment = provenance.get("environment")
            if not isinstance(generators, list) or not generators:
                identity_findings.append("derived result generators are missing")
            else:
                for index, identity in enumerate(generators):
                    identity_findings.extend(
                        _identity_violations(
                            identity,
                            label=f"generator {index}",
                            tracked=tracked,
                            project_root=project_root,
                            file_payloads=file_payloads,
                        )
                    )
            if not isinstance(environment, list) or not environment:
                identity_findings.append("derived result environment identities are missing")
            else:
                for index, identity in enumerate(environment):
                    identity_findings.extend(
                        _identity_violations(
                            identity,
                            label=f"environment {index}",
                            tracked=tracked,
                            project_root=project_root,
                            file_payloads=file_payloads,
                        )
                    )
            if identity_findings:
                raise ValueError("; ".join(identity_findings))

            summaries.append(
                {
                    "id": bundle_id,
                    "root": root,
                    "manifest": manifest_path,
                    "manifest_bytes": len(manifest_payload),
                    "manifest_sha256": _sha256(manifest_payload),
                    "upstream_resource_ids": expected_ids,
                    "artifact_classes": sorted(artifact_classes),
                    "publication_review": {
                        "authority": review["authority"],
                        "reviewed_on": review["reviewed_on"],
                        "approval_reference": review["approval_reference"],
                    },
                }
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            violations.append(
                f"derived-result bundle failed review: {bundle_id} ({exc})"
            )

    return sorted(set(violations)), sorted(summaries, key=lambda item: item["id"])


def _permission_scope_set(value: object) -> set[str] | None:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) or not item for item in value)
        or len(value) != len(set(value))
    ):
        return None
    return set(value)


def _claims_public_custom_permission_use(resource: Mapping[str, Any]) -> bool:
    provisioning = resource.get("provisioning")
    if isinstance(provisioning, dict) and any(
        provisioning.get(flag) is True
        for flag in ("git_payload", "package_payload", "container_payload", "ci_payload")
    ):
        return True
    artifacts = resource.get("artifacts")
    if isinstance(artifacts, list) and any(
        isinstance(artifact, dict) and artifact.get("public_build") is True
        for artifact in artifacts
    ):
        return True
    redistribution = resource.get("redistribution")
    if isinstance(redistribution, dict) and any(
        isinstance(value, str) and value.startswith("allowed")
        for key, value in redistribution.items()
        if key != "conditions"
    ):
        return True
    web_use = resource.get("web_use")
    return isinstance(web_use, dict) and any(
        isinstance(web_use.get(key), str) and web_use[key].startswith("allowed")
        for key in ("public_saas_processing", "aggregate_result_publication")
    )


def permission_assurance_violations(registry: Mapping[str, Any]) -> list[str]:
    """Validate complete, independently reviewed custom-permission claims.

    Owner testimony remains useful evidence, but it cannot by itself authorize a
    public payload or service. The gate binds an off-repository permission record
    to the exact resource artifacts and requires a separately identified review.
    It validates the recorded contract; it cannot prove the off-repository facts.
    """

    resources_raw = registry.get("resources")
    resources = resources_raw if isinstance(resources_raw, list) else []
    contract = registry.get("permission_assurance_contract")
    violations: list[str] = []
    custom_resources: list[tuple[dict[str, Any], bool]] = []
    for raw in resources:
        if not isinstance(raw, dict):
            continue
        public_claim = _claims_public_custom_permission_use(raw)
        license_record = raw.get("license")
        resource_id = raw.get("id")
        if contract is not None and public_claim:
            if not isinstance(license_record, dict) or "spdx" not in license_record:
                violations.append(
                    f"public resource license identity is incomplete: {resource_id}"
                )
        explicitly_custom = (
            isinstance(license_record, dict)
            and license_record.get("permission_model") == CUSTOM_PERMISSION_MARKER
        )
        known_custom = resource_id in KNOWN_CUSTOM_PERMISSION_RESOURCE_IDS
        owner_attested = isinstance(raw.get("evidence"), list) and any(
            isinstance(item, dict)
            and item.get("type") == "project-owner-confirmation"
            for item in raw["evidence"]
        )
        implicit_custom_public_claim = (
            public_claim
            and isinstance(license_record, dict)
            and "spdx" in license_record
            and license_record.get("spdx") is None
        )
        if (
            explicitly_custom
            or known_custom
            or owner_attested
            or implicit_custom_public_claim
        ):
            custom_resources.append((raw, public_claim))

    if contract is None and not custom_resources:
        return []

    if not isinstance(contract, dict):
        violations.append("custom-permission assurance contract is missing or malformed")
    else:
        if contract.get("schema_version") != PERMISSION_ASSURANCE_SCHEMA_VERSION:
            violations.append("custom-permission assurance schema version differs")
        if contract.get("custom_permission_marker") != CUSTOM_PERMISSION_MARKER:
            violations.append("custom-permission marker contract differs")
        known_ids = contract.get("known_custom_permission_resource_ids")
        if (
            not isinstance(known_ids, list)
            or any(not isinstance(item, str) for item in known_ids)
            or len(known_ids) != len(set(known_ids))
            or set(known_ids) != KNOWN_CUSTOM_PERMISSION_RESOURCE_IDS
        ):
            violations.append(
                "custom-permission known resource ID contract differs"
            )
        fields = (
            ("statuses", PERMISSION_ASSURANCE_STATUSES),
            (
                "required_public_scopes",
                PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES,
            ),
            (
                "required_external_record_fields",
                PERMISSION_ASSURANCE_REQUIRED_EXTERNAL_RECORD_FIELDS,
            ),
            (
                "required_independent_review_fields",
                PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS,
            ),
        )
        for field, expected in fields:
            observed = contract.get(field)
            if (
                not isinstance(observed, list)
                or any(not isinstance(item, str) for item in observed)
                or len(observed) != len(set(observed))
                or set(observed) != expected
            ):
                violations.append(f"custom-permission {field} contract differs")

    for resource, public_claim in custom_resources:
        resource_id = str(resource.get("id", "<missing-id>"))
        assurance = resource.get("permission_assurance")
        if not isinstance(assurance, dict):
            violations.append(
                f"custom-permission resource lacks assurance record: {resource_id}"
            )
            continue
        if assurance.get("schema_version") != PERMISSION_ASSURANCE_SCHEMA_VERSION:
            violations.append(
                f"custom-permission assurance schema differs: {resource_id}"
            )
        status = assurance.get("status")
        if status not in PERMISSION_ASSURANCE_STATUSES:
            violations.append(
                f"custom-permission assurance status is invalid: {resource_id}"
            )
        if type(assurance.get("release_eligible")) is not bool:
            violations.append(
                f"custom-permission release eligibility is not boolean: {resource_id}"
            )

        owner = assurance.get("owner_attestation")
        if not isinstance(owner, dict):
            violations.append(
                f"custom-permission owner attestation is missing: {resource_id}"
            )
            owner = {}
        else:
            for field in ("attestor", "reference"):
                if not isinstance(owner.get(field), str) or not owner[field].strip():
                    violations.append(
                        f"custom-permission owner attestation {field} is missing: "
                        f"{resource_id}"
                    )
            if not _valid_date(owner.get("attested_on")):
                violations.append(
                    f"custom-permission owner attestation date is invalid: {resource_id}"
                )
            if _permission_scope_set(owner.get("asserted_scopes")) is None:
                violations.append(
                    f"custom-permission owner-attested scopes are malformed: {resource_id}"
                )

        review = assurance.get("independent_review")
        if status == "review-pending" and not public_claim:
            if assurance.get("release_eligible") is not False:
                violations.append(
                    f"review-pending custom permission claims release eligibility: {resource_id}"
                )
            if assurance.get("external_permission_record") is not None:
                violations.append(
                    f"review-pending custom permission claims a completed external record: "
                    f"{resource_id}"
                )
            if not isinstance(review, dict) or review.get("status") != "pending":
                violations.append(
                    f"review-pending custom permission lacks pending review state: "
                    f"{resource_id}"
                )
            elif (
                set(review)
                != PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS
                or any(
                    review.get(field) is not None
                    for field in (
                        "reviewer",
                        "reviewer_role",
                        "reviewed_on",
                        "decision_reference",
                    )
                )
                or review.get("verified_scopes") != []
            ):
                violations.append(
                    f"review-pending custom permission records a non-pending review: "
                    f"{resource_id}"
                )
            continue

        if public_claim:
            license_record = resource.get("license")
            if (
                not isinstance(license_record, dict)
                or license_record.get("permission_model") != CUSTOM_PERMISSION_MARKER
            ):
                violations.append(
                    f"public custom-permission resource lacks explicit marker: {resource_id}"
                )
            if status != "independently-reviewed":
                violations.append(
                    f"public custom-permission resource is not independently reviewed: "
                    f"{resource_id}"
                )
            if assurance.get("release_eligible") is not True:
                violations.append(
                    f"public custom-permission resource is not release eligible: {resource_id}"
                )
            if (resource.get("status") or {}).get("level") != "green":
                violations.append(
                    f"public custom-permission resource is not green: {resource_id}"
                )
            if not isinstance(license_record, dict) or license_record.get("verified") is not True:
                violations.append(
                    f"public custom-permission license is not verified: {resource_id}"
                )

        record = assurance.get("external_permission_record")
        if not isinstance(record, dict):
            violations.append(
                f"custom-permission external record is incomplete: {resource_id}"
            )
            continue
        missing_record = sorted(
            PERMISSION_ASSURANCE_REQUIRED_EXTERNAL_RECORD_FIELDS - set(record)
        )
        if missing_record:
            violations.append(
                f"custom-permission external record fields are missing: {resource_id} -> "
                + ", ".join(missing_record)
            )
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not PERMISSION_RECORD_ID_RE.fullmatch(record_id):
            violations.append(
                f"custom-permission external record ID is invalid: {resource_id}"
            )
        record_sha = record.get("record_sha256")
        if not isinstance(record_sha, str) or not SHA256_RE.fullmatch(record_sha):
            violations.append(
                f"custom-permission external record SHA-256 is invalid: {resource_id}"
            )
        for field in (
            "record_editor",
            "grantor",
            "grantor_authority",
            "revocation_or_expiry_terms",
        ):
            if not isinstance(record.get(field), str) or not record[field].strip():
                violations.append(
                    f"custom-permission external record {field} is missing: {resource_id}"
                )
        if not _valid_date(record.get("granted_on")):
            violations.append(
                f"custom-permission grant date is invalid: {resource_id}"
            )
        scopes = _permission_scope_set(record.get("scopes"))
        if scopes is None:
            violations.append(
                f"custom-permission external scopes are malformed: {resource_id}"
            )
            scopes = set()
        missing_scopes = sorted(PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES - scopes)
        if missing_scopes:
            violations.append(
                f"custom-permission external scopes are incomplete: {resource_id} -> "
                + ", ".join(missing_scopes)
            )

        expected_bindings: dict[str, str] = {}
        artifacts = resource.get("artifacts")
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    continue
                path = artifact.get("path")
                digest = artifact.get("sha256")
                if isinstance(path, str) and isinstance(digest, str):
                    expected_bindings[_normalized(path)] = digest
        bindings = record.get("artifact_bindings")
        observed_bindings: dict[str, str] = {}
        if not isinstance(bindings, list) or not bindings:
            violations.append(
                f"custom-permission artifact bindings are missing: {resource_id}"
            )
        else:
            for binding in bindings:
                if not isinstance(binding, dict):
                    violations.append(
                        f"custom-permission artifact binding is malformed: {resource_id}"
                    )
                    continue
                path = binding.get("path")
                digest = binding.get("sha256")
                if (
                    not isinstance(path, str)
                    or not _safe_relative(path)
                    or not isinstance(digest, str)
                    or not SHA256_RE.fullmatch(digest)
                ):
                    violations.append(
                        f"custom-permission artifact binding is invalid: {resource_id}"
                    )
                    continue
                normalized = _normalized(path)
                if normalized in observed_bindings:
                    violations.append(
                        f"custom-permission artifact binding is duplicated: "
                        f"{resource_id} -> {normalized}"
                    )
                observed_bindings[normalized] = digest
        if observed_bindings != expected_bindings:
            violations.append(
                f"custom-permission artifact bindings differ from registry: {resource_id}"
            )

        if not isinstance(review, dict):
            violations.append(
                f"custom-permission independent review is missing: {resource_id}"
            )
            continue
        missing_review = sorted(
            PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS - set(review)
        )
        if missing_review:
            violations.append(
                f"custom-permission independent review fields are missing: {resource_id} -> "
                + ", ".join(missing_review)
            )
        if review.get("status") != "approved":
            violations.append(
                f"custom-permission independent review is not approved: {resource_id}"
            )
        for field in ("reviewer", "reviewer_role", "decision_reference"):
            if not isinstance(review.get(field), str) or not review[field].strip():
                violations.append(
                    f"custom-permission independent review {field} is missing: "
                    f"{resource_id}"
                )
        if review.get("reviewer") == owner.get("attestor"):
            violations.append(
                f"custom-permission reviewer is not independent: {resource_id}"
            )
        if review.get("reviewer") == record.get("record_editor"):
            violations.append(
                f"custom-permission reviewer also edited the permission record: "
                f"{resource_id}"
            )
        if not _valid_date(review.get("reviewed_on")):
            violations.append(
                f"custom-permission independent review date is invalid: {resource_id}"
            )
        verified_scopes = _permission_scope_set(review.get("verified_scopes"))
        if verified_scopes is None:
            violations.append(
                f"custom-permission reviewed scopes are malformed: {resource_id}"
            )
            verified_scopes = set()
        missing_reviewed_scopes = sorted(
            PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES - verified_scopes
        )
        if missing_reviewed_scopes:
            violations.append(
                f"custom-permission reviewed scopes are incomplete: {resource_id} -> "
                + ", ".join(missing_reviewed_scopes)
            )

    return sorted(set(violations))


def release_violations(
    registry: dict[str, object],
    tracked_paths: Iterable[str],
    *,
    project_root: Path = PROJECT_ROOT,
    file_payloads: Mapping[str, bytes] | None = None,
    validate_derived_result_content: bool = True,
) -> list[str]:
    """Return fail-closed violations for the proposed Git release inventory."""

    tracked = {_normalized(path) for path in tracked_paths if path}
    violations: list[str] = []
    violations.extend(permission_assurance_violations(registry))
    reviewed_public_artifacts = {
        _normalized(str(artifact["path"]))
        for resource in registry.get("resources", [])
        for artifact in resource.get("artifacts", [])
        if artifact.get("public_build") is True and artifact.get("path")
    }

    for path in sorted(tracked):
        if path in QUARANTINED_DERIVED_FILES or any(
            path.startswith(prefix) for prefix in QUARANTINED_DERIVED_PREFIXES
        ):
            violations.append(f"quarantined derived output is Git-tracked: {path}")
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

        # Path rules reject the expected and renamed NJ8 directory layouts. An
        # exact artifact binding also prevents the known snapshot from being
        # reintroduced under an unrelated filename. Do not interpret this
        # digest as evidence that any upstream mirror is equivalent.
        if file_payloads is None or path in file_payloads:
            try:
                payload = _payload_for(
                    path,
                    project_root=project_root,
                    file_payloads=file_payloads,
                )
            except ValueError:
                payload = None
            if payload is not None:
                blocked_resource = FORBIDDEN_TRACKED_PAYLOAD_SHA256.get(
                    _sha256(payload)
                )
                if blocked_resource is not None:
                    violations.append(
                        "blocked exact resource artifact is Git-tracked: "
                        f"{blocked_resource} -> {path}"
                    )

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

    if SERVER_ONLY_TEMPLATE_PATH in tracked:
        template_payload = (
            file_payloads.get(SERVER_ONLY_TEMPLATE_PATH)
            if file_payloads is not None
            else None
        )
        if template_payload is None:
            try:
                template_payload = (
                    project_root / SERVER_ONLY_TEMPLATE_PATH
                ).read_bytes()
            except OSError:
                violations.append(
                    "server-only deployment template could not be read"
                )
        if template_payload is not None:
            violations.extend(server_only_template_violations(template_payload))

    result_violations, _ = result_bundle_review(
        registry,
        tracked,
        project_root=project_root,
        file_payloads=file_payloads,
        validate_content=validate_derived_result_content,
    )
    violations.extend(result_violations)

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
