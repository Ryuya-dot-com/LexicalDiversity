import hashlib
import json
import unittest
from pathlib import Path

from ldfreq import wordlists
from ldfreq import nation_bnc_coca
from ldfreq import semantic_network
from ldfreq import tubelex


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
