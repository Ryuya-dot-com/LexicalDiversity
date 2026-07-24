import unittest

from ldfreq import batch as BATCH


class BatchDiagnosticsTests(unittest.TestCase):
    def _results(self):
        return [
            {
                "name": "a.txt",
                "raw_tokens": ["alpha", "beta", "xray"],
                "raw_surfaces": ["Alpha", "beta", "xray"],
                "a_tokens": ["alpha", "beta", "xray"],
                "indices": {"ttr": 1.0, "mattr": None, "mtld": None},
                "panel_b": {
                    "lfp": [
                        {"level": "K1", "tokens": 2, "types": 2, "coverage_%": 66.67, "cumulative_%": 66.67},
                        {"level": "off-list", "tokens": 1, "types": 1, "coverage_%": 33.33, "cumulative_%": 66.67},
                    ],
                    "_mapped": [("alpha", 1), ("beta", 2), ("xray", None)],
                },
            },
            {
                "name": "b.txt",
                "raw_tokens": ["alpha", "gamma"],
                "raw_surfaces": ["alpha", "gamma"],
                "a_tokens": ["alpha", "gamma"],
                "indices": {"ttr": 1.0, "mattr": None, "mtld": None},
                "panel_b": {
                    "lfp": [
                        {"level": "K1", "tokens": 1, "types": 1, "coverage_%": 50.0, "cumulative_%": 50.0},
                        {"level": "off-list", "tokens": 1, "types": 1, "coverage_%": 50.0, "cumulative_%": 50.0},
                    ],
                    "_mapped": [("alpha", 1), ("gamma", None)],
                },
            },
        ]

    def test_band_rows_include_document_names(self):
        rows = BATCH.band_rows(self._results())

        self.assertEqual(rows[0]["document"], "a.txt")
        self.assertEqual(rows[0]["level"], "K1")

    def test_reliability_rows_mark_short_indices(self):
        rows = BATCH.reliability_rows(self._results(), segment=50, window=50, hdd_sample=42)
        mattr = next(row for row in rows if row["document"] == "a.txt" and row["index_key"] == "mattr")
        ttr = next(row for row in rows if row["document"] == "a.txt" and row["index_key"] == "ttr")

        self.assertEqual(mattr["status"], "too short")
        self.assertEqual(ttr["status"], "available")

    def test_offlist_rows_group_forms_by_document_and_head(self):
        rows = BATCH.offlist_rows(self._results())

        self.assertEqual(rows[0]["document"], "a.txt")
        self.assertEqual(rows[0]["head"], "xray")
        self.assertEqual(rows[0]["count"], 1)
        self.assertIn("cause", rows[0])
        self.assertIn("evidence", rows[0])

    def test_offlist_rows_classify_surface_causes(self):
        results = [
            {
                "name": "a.txt",
                "raw_tokens": ["nasa", "tokyo", "walking", "don't"],
                "raw_surfaces": ["NASA", "Tokyo", "walking", "Don't"],
                "a_tokens": ["nasa", "tokyo", "walking", "don't"],
                "indices": {},
                "panel_b": {
                    "_mapped": [
                        ("nasa", None),
                        ("tokyo", None),
                        ("walking", None),
                        ("don't", None),
                    ],
                },
            }
        ]

        rows = BATCH.offlist_rows(results)
        by_head = {row["head"]: row for row in rows}

        self.assertEqual(by_head["nasa"]["cause"], "acronym or initialism")
        self.assertEqual(
            by_head["tokyo"]["cause"],
            "proper noun or sentence-initial capitalization",
        )
        self.assertEqual(by_head["walking"]["cause"], "derived or inflected form")
        self.assertEqual(by_head["don't"]["cause"], "contraction or possessive")

    def test_overlap_pair_rows_use_jaccard_on_types(self):
        rows = BATCH.overlap_pair_rows(self._results())

        self.assertEqual(rows[0]["shared_types"], 1)
        self.assertEqual(rows[0]["union_types"], 4)
        self.assertAlmostEqual(rows[0]["jaccard"], 0.25)


if __name__ == "__main__":
    unittest.main()
