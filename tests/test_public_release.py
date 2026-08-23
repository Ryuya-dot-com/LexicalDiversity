import hashlib
import json
import re
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from scripts import check_git_history as history_gate
from scripts.check_public_release import (
    PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES,
    SERVER_ONLY_TEMPLATE_PATH,
    permission_assurance_violations,
    release_violations,
    result_bundle_review,
    server_only_template_violations,
)


ROOT = Path(__file__).resolve().parents[1]


def _bytes_identity(path, payload, *, artifact_class=None):
    identity = {
        "path": path,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
    if artifact_class is not None:
        identity["artifact_class"] = artifact_class
    return identity


def _publication_conditions(decision):
    return {
        "allowed": [],
        "allowed-with-citation": ["citation"],
        "allowed-with-attribution": ["attribution"],
        "allowed-with-attribution-and-disclosure-review": [
            "attribution",
            "disclosure-review",
        ],
    }.get(decision, [])


def _refresh_manifest(case):
    payload = (
        json.dumps(
            case["manifest"],
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    declaration = case["registry"]["derived_result_bundles"][0]
    manifest_path = declaration["manifest"]
    case["payloads"][manifest_path] = payload
    declaration["manifest_bytes"] = len(payload)
    declaration["manifest_sha256"] = hashlib.sha256(payload).hexdigest()
    case["tracked"] = sorted(case["payloads"])
    return case


def _derived_bundle_case(
    resource_specs=None,
    *,
    artifact_path="results/public/demo/summary.json",
    artifact_class="aggregate-metadata",
    artifact_payload=b'{"aggregate":true}\n',
):
    if resource_specs is None:
        resource_specs = [
            {
                "id": "open-resource",
                "decision": "allowed-with-attribution",
                "status": "green",
                "license_verified": True,
            }
        ]

    root = "results/public/demo"
    manifest_path = f"{root}/publication-manifest.json"
    plan_path = "analysis/public-demo-plan.json"
    generator_path = "scripts/build_public_demo.py"
    environment_path = "analysis/public-demo-environment.lock"
    payloads = {
        artifact_path: artifact_payload,
        plan_path: b'{"plan":"frozen"}\n',
        generator_path: b"# deterministic fixture generator\n",
        environment_path: b"runtime==1.0\n",
    }
    resource_ids = [item["id"] for item in resource_specs]
    manifest = {
        "schema_version": "1.0.0",
        "bundle": {"id": "public-demo", "root": root},
        "artifacts": [
            _bytes_identity(
                artifact_path,
                artifact_payload,
                artifact_class=artifact_class,
            )
        ],
        "upstream_resources": [
            {
                "id": item["id"],
                "registry_aggregate_result_publication": item["decision"],
                "artifact_classes": [artifact_class],
            }
            for item in resource_specs
        ],
        "provenance": {
            "plan": _bytes_identity(plan_path, payloads[plan_path]),
            "generators": [
                _bytes_identity(generator_path, payloads[generator_path])
            ],
            "environment": [
                _bytes_identity(environment_path, payloads[environment_path])
            ],
        },
        "aggregation": {
            "unit": "corpus-level aggregate",
            "contains_row_level_data": False,
            "contains_individual_predictions": False,
            "contains_fitted_models": False,
        },
        "attribution": [
            {"resource_id": item["id"], "text": f"Credit {item['id']}."}
            for item in resource_specs
        ],
        "publication_review": {
            "status": "approved",
            "authority": "test-review-board",
            "reviewed_on": "2026-07-27",
            "approval_reference": "test-fixture://derived-result/public-demo",
            "scopes": [
                {
                    "resource_id": item["id"],
                    "artifact_classes": [artifact_class],
                    "conditions_satisfied": _publication_conditions(
                        item["decision"]
                    ),
                }
                for item in resource_specs
            ],
        },
    }
    contract = {
        "manifest_schema_version": "1.0.0",
        "public_roots": ["results/public/"],
        "required_registry_fields": [
            "id",
            "root",
            "public_build",
            "manifest",
            "manifest_bytes",
            "manifest_sha256",
            "expected_upstream_resource_ids",
            "publication_status",
        ],
        "required_manifest_fields": [
            "schema_version",
            "bundle",
            "artifacts",
            "upstream_resources",
            "provenance",
            "aggregation",
            "attribution",
            "publication_review",
        ],
        "artifact_classes": [
            "aggregate-table",
            "aggregate-figure",
            "aggregate-metadata",
            "analysis-report",
        ],
        "publication_statuses": ["approved", "review-required", "blocked"],
    }
    registry = {
        "derived_result_contract": contract,
        "derived_result_bundles": [
            {
                "id": "public-demo",
                "root": root,
                "public_build": True,
                "manifest": manifest_path,
                "manifest_bytes": None,
                "manifest_sha256": None,
                "expected_upstream_resource_ids": resource_ids,
                "publication_status": "approved",
            }
        ],
        "resources": [
            {
                "id": item["id"],
                "status": {"level": item["status"]},
                "license": {"verified": item["license_verified"]},
                "web_use": {
                    "aggregate_result_publication": item["decision"],
                },
                "artifacts": [],
            }
            for item in resource_specs
        ],
    }
    return _refresh_manifest(
        {
            "registry": registry,
            "manifest": manifest,
            "payloads": payloads,
            "tracked": [],
        }
    )


class PublicReleaseGateTests(unittest.TestCase):
    def test_review_pending_nj8_record_is_honest_and_not_public_eligible(self):
        registry = json.loads(
            (ROOT / "data" / "resource_registry.json").read_text(encoding="utf-8")
        )
        nj8 = next(item for item in registry["resources"] if item["id"] == "nj8")

        self.assertEqual(permission_assurance_violations(registry), [])
        self.assertEqual(nj8["status"]["level"], "yellow")
        self.assertFalse(nj8["license"]["verified"])
        self.assertEqual(nj8["permission_assurance"]["status"], "review-pending")
        self.assertFalse(nj8["permission_assurance"]["release_eligible"])
        self.assertIsNone(
            nj8["permission_assurance"]["external_permission_record"]
        )
        self.assertTrue(
            all(
                nj8["provisioning"][flag] is False
                for flag in (
                    "git_payload",
                    "package_payload",
                    "container_payload",
                    "ci_payload",
                )
            )
        )
        self.assertTrue(
            all(artifact["public_build"] is False for artifact in nj8["artifacts"])
        )

    def test_owner_attestation_cannot_authorize_public_custom_permission_use(self):
        registry = json.loads(
            (ROOT / "data" / "resource_registry.json").read_text(encoding="utf-8")
        )
        candidate = deepcopy(registry)
        nj8 = next(item for item in candidate["resources"] if item["id"] == "nj8")
        nj8["provisioning"]["git_payload"] = True
        nj8["artifacts"][0]["public_build"] = True
        nj8["redistribution"]["repository_bundle"] = "allowed"

        violations = permission_assurance_violations(candidate)

        self.assertTrue(
            any("not independently reviewed" in item for item in violations),
            violations,
        )
        self.assertTrue(
            any("external record is incomplete" in item for item in violations),
            violations,
        )

    def test_complete_independent_custom_permission_contract_is_accepted(self):
        registry = json.loads(
            (ROOT / "data" / "resource_registry.json").read_text(encoding="utf-8")
        )
        digest = "a" * 64
        resource = {
            "id": "custom-reviewed",
            "provisioning": {
                "mode": "bundled",
                "git_payload": True,
                "package_payload": True,
                "container_payload": True,
                "ci_payload": True,
            },
            "license": {
                "spdx": None,
                "verified": True,
                "permission_model": "custom-permission",
            },
            "artifacts": [
                {
                    "path": "data/custom-reviewed/list.csv",
                    "sha256": digest,
                    "public_build": True,
                }
            ],
            "redistribution": {"repository_bundle": "allowed"},
            "web_use": {
                "public_saas_processing": "allowed",
                "aggregate_result_publication": "allowed-with-citation",
            },
            "status": {"level": "green"},
            "permission_assurance": {
                "schema_version": "1.0.0",
                "status": "independently-reviewed",
                "release_eligible": True,
                "owner_attestation": {
                    "attestor": "project-owner",
                    "attested_on": "2026-08-20",
                    "reference": "owner-record-2026-08-20",
                    "asserted_scopes": ["repository-redistribution"],
                },
                "external_permission_record": {
                    "record_id": "PERM-2026-0001",
                    "record_sha256": "b" * 64,
                    "record_editor": "permission-record-editor",
                    "grantor": "rights-holder",
                    "grantor_authority": "authorized-licensing-officer",
                    "granted_on": "2026-08-21",
                    "scopes": sorted(PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES),
                    "artifact_bindings": [
                        {
                            "path": "data/custom-reviewed/list.csv",
                            "sha256": digest,
                        }
                    ],
                    "revocation_or_expiry_terms": (
                        "No expiry; written revocation applies prospectively."
                    ),
                },
                "independent_review": {
                    "status": "approved",
                    "reviewer": "independent-reviewer",
                    "reviewer_role": "release-rights-reviewer",
                    "reviewed_on": "2026-08-22",
                    "decision_reference": "RIGHTS-REVIEW-2026-0001",
                    "verified_scopes": sorted(
                        PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES
                    ),
                },
            },
        }
        candidate = {
            "permission_assurance_contract": registry[
                "permission_assurance_contract"
            ],
            "resources": [resource],
        }

        self.assertEqual(permission_assurance_violations(candidate), [])

        owner_review = deepcopy(candidate)
        owner_review["resources"][0]["permission_assurance"][
            "independent_review"
        ]["reviewer"] = "project-owner"
        self.assertTrue(
            any(
                "reviewer is not independent" in item
                for item in permission_assurance_violations(owner_review)
            )
        )

        editor_review = deepcopy(candidate)
        editor_review["resources"][0]["permission_assurance"][
            "independent_review"
        ]["reviewer"] = "permission-record-editor"
        self.assertTrue(
            any(
                "reviewer also edited the permission record" in item
                for item in permission_assurance_violations(editor_review)
            )
        )

        missing_editor = deepcopy(candidate)
        del missing_editor["resources"][0]["permission_assurance"][
            "external_permission_record"
        ]["record_editor"]
        self.assertTrue(
            any(
                "external record fields are missing" in item
                and "record_editor" in item
                for item in permission_assurance_violations(missing_editor)
            )
        )

        wrong_binding = deepcopy(candidate)
        wrong_binding["resources"][0]["permission_assurance"][
            "external_permission_record"
        ]["artifact_bindings"][0]["sha256"] = "c" * 64
        self.assertTrue(
            any(
                "artifact bindings differ" in item
                for item in permission_assurance_violations(wrong_binding)
            )
        )

    def test_cloud_run_template_defaults_fail_closed_for_server_only_resources(self):
        template = (ROOT / SERVER_ONLY_TEMPLATE_PATH).read_bytes()

        self.assertEqual(server_only_template_violations(template), [])
        self.assertEqual(
            release_violations(
                {"resources": []},
                [SERVER_ONLY_TEMPLATE_PATH],
                file_payloads={SERVER_ONLY_TEMPLATE_PATH: template},
            ),
            [],
        )

    def test_public_release_rejects_each_server_only_activation_default(self):
        template = (ROOT / SERVER_ONLY_TEMPLATE_PATH).read_text(encoding="utf-8")
        mutations = {
            "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "bnc_coca",
            "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "1",
            "LDFREQ_SERVER_ONLY_CONTROL_ATTESTATION": "shared-abuse-controls-v1",
            "LDFREQ_SERVER_ONLY_CONTROL_EVIDENCE_ID": "GRC-2026-08-24-001",
        }
        for name, enabled_value in mutations.items():
            mutated = re.sub(
                rf"(- name: {name}\n(?:\s*#.*\n)*\s*value:) (?:\"\"|\"0\")",
                rf"\1 {enabled_value}",
                template,
                count=1,
            )
            self.assertNotEqual(mutated, template, name)
            violations = release_violations(
                {"resources": []},
                [SERVER_ONLY_TEMPLATE_PATH],
                file_payloads={
                    SERVER_ONLY_TEMPLATE_PATH: mutated.encode("utf-8")
                },
            )
            self.assertTrue(
                any("server-only deployment template is not fail-closed" in item
                    for item in violations),
                (name, violations),
            )

    def test_blocks_restricted_payload_and_requires_public_artifact(self):
        registry = {
            "resources": [
                {
                    "id": "open",
                    "status": {"level": "green"},
                    "artifacts": [
                        {"path": "data/open/table.csv.gz", "public_build": True},
                    ],
                },
                {
                    "id": "restricted",
                    "status": {"level": "yellow"},
                    "artifacts": [
                        {"path": "data/private", "public_build": False},
                    ],
                },
            ]
        }

        violations = release_violations(
            registry,
            ["data/private/list.txt"],
        )

        self.assertTrue(any("blocked resource" in item for item in violations))
        self.assertTrue(any("absent from Git inventory" in item for item in violations))

    def test_passes_clean_inventory(self):
        registry = {
            "resources": [
                {
                    "id": "open",
                    "status": {"level": "green"},
                    "artifacts": [
                        {"path": "data/open/table.csv.gz", "public_build": True},
                    ],
                },
                {
                    "id": "restricted",
                    "status": {"level": "yellow"},
                    "artifacts": [
                        {"path": "data/private/list.txt", "public_build": False},
                    ],
                },
            ]
        }

        self.assertEqual(
            release_violations(registry, ["data/open/table.csv.gz"]),
            [],
        )

    def test_blocks_renamed_or_unregistered_private_payloads(self):
        registry = {"resources": []}

        violations = release_violations(
            registry,
            [
                "data/bnc_coca/renamed-secret.dat",
                ".streamlit/runtime_lists/private/index.bin",
                "TAALED/legacy-resource.csv",
                "NationBNCCOCA/basewrd1.txt",
                "NGSL/local-download.csv",
                "antbnc_lemmas_ver_004.txt",
                "NJ8.csv",
            ],
        )

        self.assertEqual(len(violations), 7)
        self.assertTrue(any("server-only payload" in item for item in violations))
        self.assertTrue(any("private deployment payload" in item for item in violations))

    def test_blocks_any_nj8_payload_reintroduced_beside_public_manifest(self):
        violations = release_violations(
            {"resources": []},
            [
                "data/NJ8/manifest.json",
                "data/NJ8/NJ8.csv",
                "data/NJ8/renamed.bin",
                "data/NJ8/nested/list.dat",
            ],
        )

        self.assertEqual(len(violations), 3)
        self.assertTrue(
            all("server-only payload is Git-tracked" in item for item in violations)
        )

    def test_blocks_exact_nj8_artifact_identity_after_unrelated_rename(self):
        payload = b"test-only exact protected resource bytes\n"
        digest = hashlib.sha256(payload).hexdigest()
        path = "assets/renamed-reference.dat"

        with patch(
            "scripts.check_public_release.FORBIDDEN_TRACKED_PAYLOAD_SHA256",
            {digest: "nj8"},
        ):
            violations = release_violations(
                {"resources": []},
                [path],
                file_payloads={path: payload},
            )

        self.assertEqual(
            violations,
            [f"blocked exact resource artifact is Git-tracked: nj8 -> {path}"],
        )

    def test_blocks_generated_quarto_outputs_but_allows_source(self):
        registry = {"resources": []}
        blocked = [
            "docs/dispersion-sensitivity-simulation.html",
            "docs/dispersion-sensitivity-simulation.quarto_ipynb",
            "docs/dispersion-sensitivity-simulation_files/libs/runtime.js",
        ]

        violations = release_violations(
            registry,
            ["docs/dispersion-sensitivity-simulation.qmd", *blocked],
        )

        self.assertEqual(len(violations), len(blocked))
        for path in blocked:
            self.assertTrue(any(path in item for item in violations), path)

    def test_allows_governance_manifests_in_private_data_directories(self):
        registry = {"resources": []}
        self.assertEqual(
            release_violations(
                registry,
                ["data/antbnc/manifest.json", "data/bnc_coca/manifest.json"],
            ),
            [],
        )

    def test_allows_only_reviewed_ellipse_benchmark_metadata(self):
        registry = {"resources": []}

        self.assertEqual(
            release_violations(
                registry,
                [
                    "benchmarks/ellipse/manifest.json",
                    "benchmarks/ellipse/analysis-plan.json",
                ],
            ),
            [],
        )

        violations = release_violations(
            registry,
            ["benchmarks/ellipse/taales-coca-convergence-plan.json"],
        )
        self.assertTrue(any("quarantined derived output" in item for item in violations))

    def test_allows_only_the_synthetic_pilot_protocol_at_present(self):
        registry = {"resources": []}

        self.assertEqual(
            release_violations(
                registry,
                ["benchmarks/synthetic/pilot-protocol.json"],
            ),
            [],
        )

    def test_history_gate_reports_reachable_payloads_but_not_public_absence(self):
        registry = {
            "resources": [
                {
                    "id": "restricted",
                    "status": {"level": "yellow"},
                    "artifacts": [
                        {
                            "path": "data/antbnc/private.txt",
                            "public_build": False,
                        }
                    ],
                },
                {
                    "id": "public",
                    "status": {"level": "green"},
                    "artifacts": [
                        {
                            "path": "data/open/public.csv.gz",
                            "public_build": True,
                        }
                    ],
                },
            ]
        }

        violations = history_gate.history_violations(
            registry,
            ["data/antbnc/private.txt"],
        )

        self.assertTrue(violations)
        self.assertTrue(
            all("reachable Git history" in violation for violation in violations)
        )
        self.assertFalse(
            any("public artifact is absent" in violation for violation in violations)
        )

    def test_history_path_union_reads_each_reachable_tree(self):
        def fake_git_bytes(*arguments: str) -> bytes:
            commit = arguments[-1]
            if commit == "commit-a":
                return b"README.md\0shared.txt\0"
            if commit == "commit-b":
                return b"docs/policy.md\0shared.txt\0"
            raise AssertionError(arguments)

        with patch.object(
            history_gate,
            "_git_text",
            return_value="commit-a\ncommit-b",
        ), patch.object(history_gate, "_git_bytes", side_effect=fake_git_bytes):
            commits = history_gate.reachable_commits()
            paths = history_gate.reachable_history_paths(commits)

        self.assertEqual(commits, ["commit-a", "commit-b"])
        self.assertEqual(paths, ["README.md", "docs/policy.md", "shared.txt"])

    def test_blocks_synthetic_text_requests_responses_and_renamed_payloads(self):
        registry = {"resources": []}
        blocked = [
            "benchmarks/synthetic/essays.jsonl",
            "benchmarks/synthetic/generation-attempts.jsonl",
            "benchmarks/synthetic/raw-response.dat",
            "benchmarks/synthetic/pilot-protocol.json.bak",
            "benchmarks/synthetic/responses/pilot-protocol.json",
        ]

        violations = release_violations(registry, blocked)

        self.assertEqual(len(violations), len(blocked))
        for path in blocked:
            self.assertTrue(any(path in item for item in violations), path)
        self.assertTrue(all("benchmark payload" in item for item in violations))

    def test_blocks_renamed_or_nested_ellipse_payloads_and_research_files(self):
        registry = {"resources": []}
        blocked = [
            ".research/ellipse/raw-essays.parquet",
            "benchmarks/ellipse/ellipse.csv",
            "benchmarks/ellipse/raw.zip",
            "benchmarks/ellipse/manifest.json.bak",
            "benchmarks/ellipse/private/analysis-plan.json",
        ]

        violations = release_violations(registry, blocked)

        self.assertEqual(len(violations), len(blocked))
        for path in blocked:
            self.assertTrue(any(path in item for item in violations), path)
        self.assertTrue(any("benchmark payload" in item for item in violations))
        self.assertTrue(any("private deployment payload" in item for item in violations))

    def test_gitignore_keeps_only_reviewed_benchmark_metadata_trackable(self):
        rules = {
            line.strip()
            for line in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }

        self.assertIn("/.research/", rules)
        self.assertIn("/benchmarks/ellipse/*", rules)
        self.assertIn("!/benchmarks/ellipse/manifest.json", rules)
        self.assertIn("!/benchmarks/ellipse/analysis-plan.json", rules)
        self.assertNotIn(
            "!/benchmarks/ellipse/taales-coca-convergence-plan.json", rules
        )
        self.assertIn(
            "/r-package/experiments/taales-coca-convergence/", rules
        )
        self.assertIn(
            "/r-package/experiments/ellipse-external-association/", rules
        )
        self.assertNotIn(
            "!r-package/experiments/ellipse-external-association/coefficients.csv",
            rules,
        )
        self.assertIn("/benchmarks/synthetic/*", rules)
        self.assertIn("!/benchmarks/synthetic/pilot-protocol.json", rules)

    def test_approved_derived_bundle_accepts_every_explicit_allow_policy(self):
        case = _derived_bundle_case(
            [
                {
                    "id": "nation-bnc-coca-headwords-10000",
                    "decision": "allowed",
                    "status": "green",
                    "license_verified": True,
                },
                {
                    "id": "citation-resource",
                    "decision": "allowed-with-citation",
                    "status": "green",
                    "license_verified": True,
                },
                {
                    "id": "tubelex-en-treebank-7cb5fb36-frequency-index",
                    "decision": "allowed-with-attribution",
                    "status": "green",
                    "license_verified": True,
                },
                {
                    "id": "ellipse-corpus-dc3b8f0b-final",
                    "decision": "allowed-with-attribution-and-disclosure-review",
                    "status": "green",
                    "license_verified": True,
                },
            ]
        )

        findings, summaries = result_bundle_review(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertEqual(findings, [])
        self.assertEqual([item["id"] for item in summaries], ["public-demo"])
        self.assertEqual(
            summaries[0]["publication_review"]["approval_reference"],
            "test-fixture://derived-result/public-demo",
        )
        self.assertEqual(
            release_violations(
                case["registry"],
                case["tracked"],
                file_payloads=case["payloads"],
            ),
            [],
        )

    def test_registry_cannot_relax_the_fixed_derived_result_contract(self):
        tampering = (
            (
                "public_roots",
                ["results/public/", "results/unreviewed/"],
                "public root contract differs",
            ),
            (
                "required_registry_fields",
                ["id", "root", "public_build"],
                "registry field contract differs",
            ),
            (
                "required_manifest_fields",
                ["schema_version", "bundle", "artifacts"],
                "manifest field contract differs",
            ),
            (
                "artifact_classes",
                [
                    "aggregate-table",
                    "aggregate-figure",
                    "aggregate-metadata",
                    "analysis-report",
                    "unreviewed-archive",
                ],
                "artifact class contract differs",
            ),
            (
                "manifest_schema_version",
                "0.0-permissive",
                "manifest schema contract differs",
            ),
            (
                "publication_statuses",
                ["approved", "review-required", "blocked", "self-approved"],
                "publication status contract differs",
            ),
        )
        for field, replacement, expected in tampering:
            with self.subTest(field=field):
                case = _derived_bundle_case()
                case["registry"]["derived_result_contract"][field] = replacement

                violations = release_violations(
                    case["registry"],
                    case["tracked"],
                    file_payloads=case["payloads"],
                )

                self.assertTrue(any(expected in item for item in violations), violations)

    def test_publication_review_requires_an_external_approval_reference(self):
        case = _derived_bundle_case()
        del case["manifest"]["publication_review"]["approval_reference"]
        _refresh_manifest(case)

        violations = release_violations(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertTrue(
            any("approval reference is missing" in item for item in violations),
            violations,
        )

    def test_review_required_and_blocked_upstreams_cannot_self_approve(self):
        for decision in ("review-required", "blocked"):
            with self.subTest(decision=decision):
                case = _derived_bundle_case(
                    [
                        {
                            "id": "restricted-resource",
                            "decision": decision,
                            "status": "green",
                            "license_verified": True,
                        }
                    ]
                )

                violations = release_violations(
                    case["registry"],
                    case["tracked"],
                    file_payloads=case["payloads"],
                )

                self.assertTrue(
                    any(
                        "upstream aggregate publication is not allowed" in item
                        and decision in item
                        for item in violations
                    ),
                    violations,
                )

    def test_red_or_unverified_upstream_is_blocked_before_publication(self):
        cases = (
            ("red-local-only", True, "upstream resource is not green"),
            ("green", False, "upstream resource license is unverified"),
        )
        for status, verified, expected in cases:
            with self.subTest(status=status, verified=verified):
                case = _derived_bundle_case(
                    [
                        {
                            "id": "restricted-resource",
                            "decision": "allowed",
                            "status": status,
                            "license_verified": verified,
                        }
                    ]
                )

                violations = release_violations(
                    case["registry"],
                    case["tracked"],
                    file_payloads=case["payloads"],
                )

                self.assertTrue(any(expected in item for item in violations), violations)

    def test_manifest_cannot_omit_a_declared_upstream(self):
        case = _derived_bundle_case(
            [
                {
                    "id": "resource-a",
                    "decision": "allowed",
                    "status": "green",
                    "license_verified": True,
                },
                {
                    "id": "resource-b",
                    "decision": "allowed",
                    "status": "green",
                    "license_verified": True,
                },
            ]
        )
        case["manifest"]["upstream_resources"] = case["manifest"][
            "upstream_resources"
        ][:1]
        _refresh_manifest(case)

        violations = release_violations(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertTrue(
            any("upstream resource IDs differ" in item for item in violations),
            violations,
        )

    def test_manifest_cannot_name_an_unknown_upstream(self):
        case = _derived_bundle_case()
        case["registry"]["resources"] = []

        violations = release_violations(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertTrue(
            any("unknown upstream resource: open-resource" in item for item in violations),
            violations,
        )

    def test_bundle_rejects_an_unmanifested_extra_file(self):
        case = _derived_bundle_case()
        extra_path = "results/public/demo/renamed-private.csv"
        case["payloads"][extra_path] = b"term,secret\nexample,1\n"
        _refresh_manifest(case)

        violations = release_violations(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertTrue(
            any(
                "bundle inventory differs" in item and extra_path in item
                for item in violations
            ),
            violations,
        )

    def test_bundle_rejects_archive_bytes_disguised_as_a_figure(self):
        path = "results/public/demo/aggregate-figure.png"
        case = _derived_bundle_case(
            artifact_path=path,
            artifact_class="aggregate-figure",
            artifact_payload=b"PK\x03\x04not-really-a-png",
        )

        violations = release_violations(
            case["registry"],
            case["tracked"],
            file_payloads=case["payloads"],
        )

        self.assertTrue(
            any(
                "archive payload is prohibited" in item and path in item
                for item in violations
            ),
            violations,
        )

    def test_exact_coca_family_is_quarantined_without_name_false_positives(self):
        quarantined = [
            "benchmarks/ellipse/taales-coca-convergence-plan.json",
            "scripts/analyze_taales_coca_convergence.R",
            "scripts/prepare_ellipse_taales_convergence.py",
            "scripts/run_taales_legacy_coca.py",
            "tests/test_taales_coca_convergence.py",
            "r-package/experiments/taales-coca-convergence/renamed.bin",
        ]

        violations = release_violations({"resources": []}, quarantined)

        for path in quarantined:
            self.assertTrue(
                any(
                    item == f"quarantined derived output is Git-tracked: {path}"
                    for item in violations
                ),
                (path, violations),
            )
        history = history_gate.history_violations(
            {"resources": []},
            ["r-package/experiments/taales-coca-convergence/measurement.json"],
        )
        self.assertTrue(any("reachable Git history" in item for item in history))

        allowed_name_matches = [
            "scripts/build_nation_bnc_coca_index.py",
            "data/bnc_coca/manifest.json",
            "docs/coca-derived-output-publication-gate.md",
        ]
        self.assertEqual(
            release_violations({"resources": []}, allowed_name_matches),
            [],
        )

    def test_blocks_unregistered_file_even_when_renamed_under_data_open(self):
        registry = {
            "resources": [
                {
                    "id": "reviewed",
                    "status": {"level": "green"},
                    "artifacts": [
                        {
                            "path": "data/open/reviewed/table.csv.gz",
                            "public_build": True,
                        }
                    ],
                }
            ]
        }

        violations = release_violations(
            registry,
            [
                "data/open/reviewed/table.csv.gz",
                "data/open/reviewed/renamed-private.csv.gz",
                "data/open/unknown/manifest.json",
            ],
        )

        self.assertEqual(len(violations), 2)
        self.assertTrue(
            all("unregistered public-data artifact" in item for item in violations)
        )


if __name__ == "__main__":
    unittest.main()
