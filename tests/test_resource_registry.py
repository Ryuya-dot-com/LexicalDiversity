import hashlib
import json
import unittest
from pathlib import Path

from ldfreq import wordlists
from ldfreq import nation_bnc_coca
from ldfreq import semantic_network
from ldfreq import tubelex
from scripts.check_public_release import (
    AGGREGATE_PUBLICATION_REQUIREMENTS,
    PERMISSION_ASSURANCE_REQUIRED_EXTERNAL_RECORD_FIELDS,
    PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS,
    PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = PROJECT_ROOT / "data" / "resource_registry.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ResourceRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))

    def test_entries_have_required_fields_and_valid_status(self):
        required = set(self.registry["required_entry_fields"])
        valid_statuses = set(self.registry["status_definitions"])
        identifiers = []
        for entry in self.registry["resources"]:
            self.assertEqual(required - set(entry), set(), entry["id"])
            self.assertIn(entry["status"]["level"], valid_statuses)
            identifiers.append(entry["id"])
        self.assertEqual(len(identifiers), len(set(identifiers)))

    def test_schema_1_3_defines_permission_and_derived_result_contracts(self):
        self.assertEqual(self.registry["schema_version"], "1.3.0")
        permission = self.registry["permission_assurance_contract"]
        self.assertEqual(permission["schema_version"], "1.0.0")
        self.assertEqual(permission["custom_permission_marker"], "custom-permission")
        self.assertEqual(
            permission["known_custom_permission_resource_ids"],
            ["nj8"],
        )
        self.assertEqual(
            set(permission["statuses"]),
            {"review-pending", "independently-reviewed"},
        )
        self.assertEqual(
            set(permission["required_public_scopes"]),
            PERMISSION_ASSURANCE_REQUIRED_PUBLIC_SCOPES,
        )
        self.assertEqual(
            set(permission["required_external_record_fields"]),
            PERMISSION_ASSURANCE_REQUIRED_EXTERNAL_RECORD_FIELDS,
        )
        self.assertEqual(
            set(permission["required_independent_review_fields"]),
            PERMISSION_ASSURANCE_REQUIRED_INDEPENDENT_REVIEW_FIELDS,
        )
        contract = self.registry["derived_result_contract"]
        self.assertEqual(contract["manifest_schema_version"], "1.0.0")
        self.assertEqual(contract["public_roots"], ["results/public/"])
        self.assertEqual(
            set(contract["required_registry_fields"]),
            {
                "id",
                "root",
                "public_build",
                "manifest",
                "manifest_bytes",
                "manifest_sha256",
                "expected_upstream_resource_ids",
                "publication_status",
            },
        )
        self.assertEqual(
            set(contract["required_manifest_fields"]),
            {
                "schema_version",
                "bundle",
                "artifacts",
                "upstream_resources",
                "provenance",
                "aggregation",
                "attribution",
                "publication_review",
            },
        )
        self.assertEqual(
            set(contract["artifact_classes"]),
            {
                "aggregate-table",
                "aggregate-figure",
                "aggregate-metadata",
                "analysis-report",
            },
        )
        self.assertEqual(
            set(contract["publication_statuses"]),
            {"approved", "review-required", "blocked"},
        )

    def test_derived_result_declarations_are_unique_resolved_and_fail_closed(self):
        contract = self.registry["derived_result_contract"]
        required = set(contract["required_registry_fields"])
        valid_statuses = set(contract["publication_statuses"])
        resource_ids = {entry["id"] for entry in self.registry["resources"]}
        bundle_ids = []
        roots = []

        for bundle in self.registry["derived_result_bundles"]:
            self.assertEqual(required - set(bundle), set(), bundle.get("id"))
            self.assertIs(type(bundle["public_build"]), bool, bundle["id"])
            self.assertIn(bundle["publication_status"], valid_statuses, bundle["id"])
            self.assertFalse(Path(bundle["root"]).is_absolute(), bundle["id"])
            self.assertNotIn("..", Path(bundle["root"]).parts, bundle["id"])
            expected = bundle["expected_upstream_resource_ids"]
            self.assertTrue(expected, bundle["id"])
            self.assertEqual(len(expected), len(set(expected)), bundle["id"])
            self.assertEqual(set(expected) - resource_ids, set(), bundle["id"])
            if bundle["public_build"]:
                self.assertEqual(bundle["publication_status"], "approved", bundle["id"])
                self.assertIsInstance(bundle["manifest"], str, bundle["id"])
                self.assertIs(type(bundle["manifest_bytes"]), int, bundle["id"])
                self.assertRegex(bundle["manifest_sha256"], r"^[0-9a-f]{64}$")
            else:
                self.assertIn(
                    bundle["publication_status"],
                    {"review-required", "blocked"},
                    bundle["id"],
                )
                self.assertIsNone(bundle["manifest"], bundle["id"])
                self.assertIsNone(bundle["manifest_bytes"], bundle["id"])
                self.assertIsNone(bundle["manifest_sha256"], bundle["id"])
            bundle_ids.append(bundle["id"])
            roots.append(bundle["root"].rstrip("/"))

        self.assertEqual(len(bundle_ids), len(set(bundle_ids)))
        self.assertEqual(len(roots), len(set(roots)))

    def test_coca_and_pending_ellipse_declarations_are_exactly_quarantined(self):
        bundles = {
            entry["id"]: entry for entry in self.registry["derived_result_bundles"]
        }
        self.assertEqual(set(bundles), {
            "ellipse-external-association",
            "taales-coca-convergence",
        })

        ellipse = bundles["ellipse-external-association"]
        self.assertFalse(ellipse["public_build"])
        self.assertEqual(ellipse["publication_status"], "review-required")
        self.assertEqual(
            set(ellipse["expected_upstream_resource_ids"]),
            {
                "ellipse-corpus-dc3b8f0b-final",
                "ngsl-1.2",
                "open-english-wordnet-2025-metrics",
                "tubelex-en-treebank-7cb5fb36-frequency-index",
            },
        )

        coca = bundles["taales-coca-convergence"]
        self.assertFalse(coca["public_build"])
        self.assertEqual(coca["publication_status"], "blocked")
        self.assertEqual(
            set(coca["expected_upstream_resource_ids"]),
            {
                "ellipse-corpus-dc3b8f0b-final",
                "taales-2.8.1-legacy-application",
                "taales-legacy-coca-derived-tables",
                "tubelex-en-treebank-7cb5fb36-frequency-index",
            },
        )

    def test_aggregate_publication_decisions_use_a_closed_vocabulary(self):
        allowed = set(AGGREGATE_PUBLICATION_REQUIREMENTS)
        known = allowed | {"review-required"}
        seen = set()
        for resource in self.registry["resources"]:
            decision = resource["web_use"]["aggregate_result_publication"]
            seen.add(decision)
            self.assertIn(decision, known, resource["id"])
            if decision in allowed:
                self.assertEqual(resource["status"]["level"], "green", resource["id"])
                self.assertTrue(resource["license"]["verified"], resource["id"])

        self.assertTrue(seen <= known)
        self.assertIn("review-required", seen)

    def test_tier_and_provisioning_axes_are_explicit_and_fail_closed(self):
        valid_tiers = set(self.registry["tier_definitions"])
        valid_modes = set(self.registry["provisioning_mode_definitions"])
        payload_flags = (
            "git_payload",
            "package_payload",
            "container_payload",
            "ci_payload",
        )
        for entry in self.registry["resources"]:
            self.assertIn(entry["tier"], valid_tiers, entry["id"])
            provisioning = entry["provisioning"]
            self.assertIn(provisioning["mode"], valid_modes, entry["id"])
            for flag in payload_flags:
                self.assertIs(type(provisioning[flag]), bool, f"{entry['id']}:{flag}")
            if any(provisioning[flag] for flag in payload_flags):
                self.assertEqual(entry["tier"], "runtime-resource", entry["id"])
                self.assertEqual(provisioning["mode"], "bundled", entry["id"])
                self.assertEqual(entry["status"]["level"], "green", entry["id"])

    def test_ellipse_registry_manifest_and_analysis_plan_are_cross_pinned(self):
        resource = next(
            entry
            for entry in self.registry["resources"]
            if entry["id"] == "ellipse-corpus-dc3b8f0b-final"
        )
        manifest_path = PROJECT_ROOT / resource["provisioning"]["spec"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plan_path = PROJECT_ROOT / manifest["analysis"]["plan"]
        spec_path = PROJECT_ROOT / manifest["analysis"]["human_readable_spec"]

        self.assertEqual(resource["tier"], "evaluation-benchmark")
        self.assertEqual(resource["provisioning"]["mode"], "fetched")
        self.assertTrue(
            all(
                resource["provisioning"][flag] is False
                for flag in (
                    "git_payload",
                    "package_payload",
                    "container_payload",
                    "ci_payload",
                )
            )
        )
        self.assertTrue(
            all(artifact["public_build"] is False for artifact in resource["artifacts"])
        )
        self.assertEqual(manifest["resource_id"], resource["id"])
        self.assertIn(
            manifest["upstream"]["commit"],
            resource["version"]["upstream"],
        )
        self.assertEqual(_sha256(plan_path), manifest["analysis"]["plan_sha256"])
        self.assertEqual(
            _sha256(spec_path),
            manifest["analysis"]["human_readable_spec_sha256"],
        )

        registry_hashes = {
            artifact["sha256"] for artifact in resource["artifacts"]
        }
        train_member = next(
            item
            for item in manifest["members"]
            if item["kind"] == "final-train-csv"
        )
        self.assertIn(train_member["sha256"], registry_hashes)
        self.assertIn(
            manifest["nested_test_archive"]["member"]["sha256"],
            registry_hashes,
        )

    def test_recorded_file_hashes_match_workspace_artifacts(self):
        for entry in self.registry["resources"]:
            for artifact in entry["artifacts"]:
                relative = artifact.get("path")
                expected = artifact.get("sha256")
                if not relative or not expected:
                    continue
                path = PROJECT_ROOT / relative
                if not path.is_file():
                    self.assertFalse(artifact.get("public_build"), relative)
                    continue
                self.assertEqual(path.stat().st_size, artifact["bytes"], relative)
                self.assertEqual(_sha256(path), expected, relative)

    def test_only_green_resources_can_enter_public_build(self):
        for entry in self.registry["resources"]:
            public_artifacts = [
                artifact
                for artifact in entry["artifacts"]
                if artifact.get("public_build")
            ]
            if public_artifacts:
                self.assertEqual(entry["status"]["level"], "green", entry["id"])
                self.assertTrue(entry["license"]["verified"], entry["id"])

    def test_nj8_is_review_pending_and_payload_excluded_from_public_modes(self):
        nj8 = next(
            entry for entry in self.registry["resources"] if entry["id"] == "nj8"
        )

        self.assertEqual(nj8["status"]["level"], "yellow")
        self.assertFalse(nj8["license"]["verified"])
        self.assertEqual(
            nj8["license"]["permission_model"],
            "custom-permission",
        )
        self.assertEqual(
            nj8["permission_assurance"]["status"],
            "review-pending",
        )
        self.assertFalse(nj8["permission_assurance"]["release_eligible"])
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
        runtime = next(entry for entry in wordlists.REGISTRY if entry["id"] == "nj8")
        self.assertFalse(runtime["public_web"])
        self.assertFalse(runtime["redistributable"])
        manifest = json.loads(
            (PROJECT_ROOT / "data" / "NJ8" / "manifest.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            manifest["permission_assurance"]["status"],
            "review-pending",
        )
        self.assertFalse(
            manifest["permission_assurance"]["public_release_eligible"]
        )
        self.assertFalse((PROJECT_ROOT / "data" / "NJ8" / "NJ8.csv").exists())

    def test_runtime_public_wordlists_resolve_to_green_registry_entries(self):
        statuses = {
            entry["id"]: entry["status"]["level"]
            for entry in self.registry["resources"]
        }
        for entry in wordlists.REGISTRY:
            if not entry.get("public_web"):
                continue
            registry_id = entry.get("registry_id")
            self.assertIsNotNone(registry_id, entry["id"])
            self.assertEqual(statuses.get(registry_id), "green", entry["id"])

    def test_server_only_eligible_wordlists_resolve_to_green_web_resources(self):
        resources = {
            entry["id"]: entry
            for entry in self.registry["resources"]
        }
        for entry in wordlists.REGISTRY:
            if not entry.get("server_only_eligible"):
                continue
            registry_id = entry.get("registry_id")
            self.assertIsNotNone(registry_id, entry["id"])
            resource = resources[registry_id]
            self.assertEqual(resource["status"]["level"], "green", entry["id"])
            self.assertTrue(
                str(resource["web_use"]["public_saas_processing"]).startswith("allowed"),
                entry["id"],
            )
            self.assertIn(
                "blocked",
                resource["redistribution"]["client_download"],
                entry["id"],
            )

    def test_nation_runtime_pins_match_registry_evidence(self):
        resources = {entry["id"]: entry for entry in self.registry["resources"]}
        family = resources["nation-bnc-coca-families-25000"]
        expected = family["build_provenance"]["expected_runtime_artifact"]
        self.assertEqual(expected["bytes"], nation_bnc_coca.PRODUCTION_ARTIFACT_BYTES)
        self.assertEqual(expected["rows"], nation_bnc_coca.PRODUCTION_ARTIFACT_ROWS)
        self.assertEqual(expected["families"], nation_bnc_coca.PRODUCTION_FAMILIES)
        self.assertEqual(expected["sha256"], nation_bnc_coca.PRODUCTION_ARTIFACT_SHA256)

        headwords = resources["nation-bnc-coca-headwords-10000"]
        recorded = {
            Path(artifact["path"]).name: (artifact["bytes"], artifact["sha256"])
            for artifact in headwords["artifacts"]
        }
        self.assertEqual(recorded, wordlists.NATION_HEADWORD_FILES)

    def test_oewn_runtime_pins_match_registry_evidence(self):
        resource = next(
            entry
            for entry in self.registry["resources"]
            if entry["id"] == "open-english-wordnet-2025-metrics"
        )
        artifact = next(
            item for item in resource["artifacts"]
            if item["path"].endswith("open_english_wordnet_2025_lemma_metrics.csv.gz")
        )
        self.assertEqual(artifact["bytes"], semantic_network.PRODUCTION_ARTIFACT_BYTES)
        self.assertEqual(artifact["sha256"], semantic_network.PRODUCTION_ARTIFACT_SHA256)

    def test_tubelex_runtime_pins_match_registry_evidence(self):
        resource = next(
            entry
            for entry in self.registry["resources"]
            if entry["id"] == tubelex.PRODUCTION_RESOURCE_ID
        )
        artifact = next(
            item for item in resource["artifacts"]
            if item["path"].endswith(tubelex.ARTIFACT_NAME)
        )
        self.assertEqual(artifact["bytes"], tubelex.PRODUCTION_ARTIFACT_BYTES)
        self.assertEqual(artifact["sha256"], tubelex.PRODUCTION_ARTIFACT_SHA256)
        self.assertEqual(
            resource["build_provenance"]["upstream_inputs"],
            [
                "tubelex-en-treebank.tsv.xz; 4,152,940 bytes; SHA-256 "
                f"{tubelex.TUBELEX_EN_SOURCE_SHA256}; decompressed SHA-256 "
                f"{tubelex.TUBELEX_EN_SOURCE_DECOMPRESSED_SHA256}"
            ],
        )


if __name__ == "__main__":
    unittest.main()
