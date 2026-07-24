import io
import json
import math
import unittest
import zipfile

from openpyxl import load_workbook

from ldfreq import exporting


class ExportingTests(unittest.TestCase):
    def test_payload_to_json_uses_frozen_precision_and_terminal_newline(self):
        payload = {
            "value": 1.1234567890129,
            "negative_zero": -0.0,
            "missing": math.nan,
            "unicode": "語彙",
        }

        serialized = exporting.payload_to_json(payload)

        self.assertTrue(serialized.endswith("\n"))
        self.assertNotIn("NaN", serialized)
        self.assertNotIn("\\u8a9e", serialized)
        self.assertEqual(
            json.loads(serialized),
            {
                "value": 1.123456789013,
                "negative_zero": 0.0,
                "missing": None,
                "unicode": "語彙",
            },
        )

    def test_public_export_rejects_infinity(self):
        with self.assertRaisesRegex(ValueError, "infinite"):
            exporting.payload_to_json({"value": math.inf})

    def test_payload_to_excel_writes_batch_summary_and_detail_sheets(self):
        payload = {
            "ldfreq_version": "test",
            "batch_diagnostics": {
                "bands": [{"document": "a.txt", "level": "K1", "coverage_%": 66.67}],
                "reliability": [{"document": "a.txt", "index": "TTR", "status": "available"}],
                "off_list": [{"document": "a.txt", "head": "xray", "count": 1}],
                "overlap_matrix": [{"document_a": "a.txt", "document_b": "b.txt", "jaccard": 0.5}],
                "overlap_pairs": [{"document_a": "a.txt", "document_b": "b.txt", "jaccard": 0.5}],
            },
            "documents": [
                {
                    "document": {"name": "a.txt"},
                    "settings": {
                        "unit": "flemma",
                        "lemmatizer": "word_form",
                        "lemmatizer_version": "-",
                        "list_name": "Test List",
                    },
                    "method_notes": ["Tokenizer policy: test"],
                    "n_tokens": 3,
                    "n_types": 2,
                    "panel_a": {"ttr": 0.667, "mattr": None, "mtld": None, "hdd": None, "vocd": None},
                    "panel_b": {
                        "lfp": [{"level": "K1", "tokens": 2, "types": 1, "coverage_%": 66.67, "cumulative_%": 66.67}],
                        "coverage_threshold": {90: None},
                        "advanced_guiraud": 0.58,
                        "pct_beyond_k": 50.0,
                        "mean_rank": {"pct_off_list": 33.33},
                        "p_lex": {"lambda": None, "n_segments": 0},
                        "s_index": {"S": None, "capped": None},
                        "band_wise": [{"level": "K1", "tokens": 2, "types": 1, "Min N": 50}],
                    },
                    "tubelex": {
                        "token_coverage": 2 / 3,
                        "type_coverage": 0.5,
                        "frequency_zipf_token_mean": 4.2,
                        "frequency_zipf_type_mean": 3.8,
                        "video_log10_prevalence_token_mean": -2.1,
                        "video_log10_prevalence_type_mean": -2.4,
                        "channel_log10_prevalence_token_mean": -2.3,
                        "channel_log10_prevalence_type_mean": -2.6,
                    },
                },
                {
                    "document": {"name": "b.txt"},
                    "settings": {"unit": "token", "list_name": "Test List"},
                    "n_tokens": 2,
                    "n_types": 2,
                    "panel_a": {"ttr": 1.0},
                    "panel_b": None,
                },
            ],
        }

        workbook = load_workbook(io.BytesIO(exporting.payload_to_excel(payload)), read_only=True)

        self.assertIn("summary", workbook.sheetnames)
        self.assertIn("descriptives", workbook.sheetnames)
        self.assertIn("panel_a", workbook.sheetnames)
        self.assertIn("metadata", workbook.sheetnames)
        self.assertIn("reliability", workbook.sheetnames)
        self.assertIn("off_list", workbook.sheetnames)
        self.assertIn("overlap_pairs", workbook.sheetnames)
        self.assertIn("semantic_network", workbook.sheetnames)
        self.assertIn("tubelex", workbook.sheetnames)
        summary_rows = list(workbook["summary"].iter_rows(values_only=True))
        self.assertEqual(summary_rows[0][0], "document")
        self.assertEqual(summary_rows[1][0], "a.txt")
        self.assertEqual(summary_rows[2][0], "b.txt")

        descriptives = exporting.descriptive_rows(payload)
        ttr = next(row for row in descriptives if row["measure"] == "ttr")
        self.assertEqual(ttr["n"], 2)
        self.assertEqual(ttr["missing"], 0)
        self.assertAlmostEqual(ttr["mean"], 0.8335, places=4)
        mattr = next(row for row in descriptives if row["measure"] == "mattr")
        self.assertEqual(mattr["n"], 0)
        self.assertEqual(mattr["missing"], 2)

    def test_payload_to_excel_is_byte_deterministic_and_has_fixed_zip_metadata(self):
        payload = {
            "ldfreq_version": "test",
            "document": {"name": "Document 001"},
            "settings": {},
            "method_notes": [],
            "privacy": {},
            "n_tokens": 1,
            "n_types": 1,
            "panel_a": {"ttr": 1.0},
            "panel_b": None,
            "semantic_network": None,
            "tubelex": None,
        }

        first = exporting.payload_to_excel(payload)
        second = exporting.payload_to_excel(payload)

        self.assertEqual(first, second)
        with zipfile.ZipFile(io.BytesIO(first)) as workbook_zip:
            self.assertTrue(workbook_zip.infolist())
            self.assertTrue(
                all(
                    member.date_time == exporting.XLSX_ZIP_TIMESTAMP
                    for member in workbook_zip.infolist()
                )
            )
            core = workbook_zip.read("docProps/core.xml").decode("utf-8")
        self.assertIn("1980-01-01T00:00:00Z", core)
        self.assertIn("<dc:creator", core)
        self.assertIn(">ldfreq</dc:creator>", core)


if __name__ == "__main__":
    unittest.main()
