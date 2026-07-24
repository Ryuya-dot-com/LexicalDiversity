import tempfile
import unittest
import os
from pathlib import Path
from unittest.mock import patch

import app


class FakeUpload:
    def __init__(self, name, data, mime="text/plain"):
        self.name = name
        self._data = data
        self.type = mime

    def getvalue(self):
        return self._data


class AppHelperTests(unittest.TestCase):
    def test_tubelex_rows_format_coverage_and_smoothed_corpus_means(self):
        rows = app._tubelex_rows({
            "tokens": 80,
            "types": 40,
            "token_coverage": 0.875,
            "type_coverage": 0.625,
            "frequency_zipf_token_mean": 4.25,
            "frequency_zipf_type_mean": 3.5,
            "video_log10_prevalence_token_mean": -2.25,
            "video_log10_prevalence_type_mean": -3.0,
            "channel_log10_prevalence_token_mean": -2.0,
            "channel_log10_prevalence_type_mean": -2.75,
        })
        by_index = {row["Index"]: row for row in rows}

        self.assertEqual(
            by_index["TUBELEX-EN Treebank lookup units"]["Token-weighted value"],
            80,
        )
        self.assertEqual(
            by_index["TUBELEX-EN Treebank coverage"]["Token-weighted value"],
            "87.50%",
        )
        self.assertEqual(
            by_index["TUBELEX-EN Treebank coverage"]["Type-weighted value"],
            "62.50%",
        )
        self.assertEqual(
            by_index["Mean smoothed Zipf frequency"]["Token-weighted value"],
            "4.2500",
        )
        self.assertNotIn("Mean normalized category entropy", by_index)

    def test_tubelex_rows_show_na_when_no_reference_items_match(self):
        rows = app._tubelex_rows({
            "tokens": 0,
            "types": 0,
            "token_coverage": 0.0,
            "type_coverage": 0.0,
        })
        by_index = {row["Index"]: row for row in rows}

        self.assertEqual(
            by_index["TUBELEX-EN Treebank coverage"]["Token-weighted value"],
            "0.00%",
        )
        self.assertEqual(
            by_index["Mean smoothed Zipf frequency"]["Token-weighted value"],
            "— (NA)",
        )

    def test_analysis_deadline_setting_is_bounded(self):
        with patch.dict(os.environ, {"LDFREQ_ANALYSIS_DEADLINE_SECONDS": "45"}):
            self.assertEqual(app._analysis_deadline_seconds(), 45.0)
        for invalid in ("0", "301", "nan", "not-a-number"):
            with patch.dict(
                os.environ,
                {"LDFREQ_ANALYSIS_DEADLINE_SECONDS": invalid},
            ):
                self.assertEqual(
                    app._analysis_deadline_seconds(),
                    app.ANALYSIS_DEADLINE_SECONDS_DEFAULT,
                )

    def test_upload_fingerprint_detects_same_size_content_changes(self):
        first = app._upload_fingerprint(FakeUpload("same.txt", b"alpha"))
        second = app._upload_fingerprint(FakeUpload("same.txt", b"bravo"))

        self.assertEqual(first["size"], second["size"])
        self.assertNotIn("name", first)
        self.assertNotIn("sha256", first)
        self.assertNotEqual(first["digest"], second["digest"])

    def test_path_fingerprint_tracks_directory_file_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fp = root / "list.txt"
            fp.write_text("alpha\n", encoding="utf-8")
            first = app._path_fingerprint(str(root))
            fp.write_text("alpha beta\n", encoding="utf-8")
            second = app._path_fingerprint(str(root))

        self.assertNotEqual(first, second)


if __name__ == "__main__":
    unittest.main()
