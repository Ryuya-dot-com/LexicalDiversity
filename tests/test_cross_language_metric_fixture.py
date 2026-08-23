import json
import unittest
from pathlib import Path

from ldfreq import indices as IDX


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "cross-language-metric-fixture.json"
)


class CrossLanguageMetricFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))

    def test_shared_method_identities_and_semantic_cases(self):
        fixture = self.fixture
        self.assertEqual(
            fixture["fixture_id"],
            "ldfreq-cross-language-semantic-metrics",
        )
        self.assertEqual(fixture["license"], "CC0-1.0")
        self.assertIn("Parse and compare", fixture["comparison_rule"])
        self.assertEqual(
            fixture["status_mapping"]["available"]["python"],
            "available",
        )

        expected_methods = {
            method["metric_id"]: method["method_id"]
            for method in fixture["shared_methods"]
        }
        for metric_id, method_id in expected_methods.items():
            with self.subTest(metric_id=metric_id):
                self.assertEqual(IDX.METHOD_IDS[metric_id], method_id)

        for case in fixture["cases"]:
            parameters = case["parameters"]
            kwargs = {}
            if "segment_length" in parameters:
                kwargs["segment"] = parameters["segment_length"]
            if "window_length" in parameters:
                kwargs["window"] = parameters["window_length"]
            if "sample_size" in parameters:
                kwargs["hdd_sample"] = parameters["sample_size"]
            records = IDX.all_index_records(case["tokens"], **kwargs)

            for assertion in case["assertions"]:
                metric_id = assertion["metric_id"]
                record = records[metric_id]
                context = f"case={case['id']} metric={metric_id}"
                expected_status = fixture["status_mapping"][
                    assertion["semantic_status"]
                ]["python"]
                with self.subTest(context=context):
                    self.assertEqual(record["method_id"], expected_methods[metric_id])
                    self.assertEqual(record["status"], expected_status)
                    if assertion["semantic_status"] == "available":
                        expected = assertion["value"]
                        tolerance = (
                            fixture["numeric_tolerance"]["absolute"]
                            + fixture["numeric_tolerance"]["relative"]
                            * abs(expected)
                        )
                        self.assertAlmostEqual(
                            record["value"], expected, delta=tolerance
                        )
                        self.assertIsNone(record["missing_reason"])
                    else:
                        self.assertIsNone(record["value"])
                        self.assertEqual(
                            record["missing_reason"],
                            assertion["missing_reason"],
                        )

    def test_mtld_boundary_is_intentionally_runtime_specific(self):
        fixture = self.fixture
        boundary = fixture["runtime_specific_cases"][0]
        expected = boundary["python"]
        record = IDX.all_index_records(
            boundary["tokens"],
            mtld_threshold=boundary["parameters"]["threshold"],
            mtld_min_factor_len=boundary["parameters"]["minimum_factor_length"],
        )["mtld"]

        self.assertEqual(record["method_id"], expected["method_id"])
        self.assertEqual(record["status"], expected["status"])
        tolerance = (
            fixture["numeric_tolerance"]["absolute"]
            + fixture["numeric_tolerance"]["relative"] * abs(expected["value"])
        )
        self.assertAlmostEqual(record["value"], expected["value"], delta=tolerance)
        self.assertNotEqual(boundary["r"]["method_id"], expected["method_id"])
        self.assertNotEqual(boundary["r"]["value"], expected["value"])


if __name__ == "__main__":
    unittest.main()
