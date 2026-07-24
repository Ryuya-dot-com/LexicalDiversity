import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import check_git_history as history_gate
from scripts.check_public_release import release_violations


ROOT = Path(__file__).resolve().parents[1]


class PublicReleaseGateTests(unittest.TestCase):
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
        self.assertIn("/benchmarks/synthetic/*", rules)
        self.assertIn("!/benchmarks/synthetic/pilot-protocol.json", rules)

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
