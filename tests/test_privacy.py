import unittest

from ldfreq.privacy import (
    pseudonymize_documents,
    retain_aggregate_result,
    sensitive_paths,
)


class PrivacyTests(unittest.TestCase):
    def test_pseudonymize_documents_drops_source_names(self):
        documents = [
            {"name": "student-a.txt", "text": "alpha"},
            {"name": "student-b.txt", "text": "beta"},
        ]

        pseudonymous = pseudonymize_documents(documents)

        self.assertEqual(
            [document["name"] for document in pseudonymous],
            ["Document 001", "Document 002"],
        )
        self.assertNotIn("student-a.txt", repr(pseudonymous))

    def test_retained_result_has_no_token_or_private_lookup_sequences(self):
        result = {
            "name": "Document 001",
            "raw_tokens": ["privateword"],
            "raw_surfaces": ["PrivateWord"],
            "a_tokens": ["privateword"],
            "future_accidental_copy": "privateword",
            "n_tokens": 1,
            "n_types": 1,
            "indices": {"ttr": 1.0},
            "panel_b": {
                "mean_rank": {"mean_rank": 12.0},
                "_mapped": [("privateword", 12)],
            },
            "tubelex": {
                "token_coverage": 0.0,
                "frequency_zipf_token_mean": 0.763414439791093,
            },
        }

        retained = retain_aggregate_result(result)

        self.assertEqual(retained["n_tokens"], 1)
        self.assertEqual(retained["panel_b"]["mean_rank"]["mean_rank"], 12.0)
        self.assertEqual(retained["tubelex"]["token_coverage"], 0.0)
        self.assertEqual(sensitive_paths(retained), [])
        self.assertNotIn("future_accidental_copy", retained)
        self.assertNotIn("privateword", repr(retained))


if __name__ == "__main__":
    unittest.main()
