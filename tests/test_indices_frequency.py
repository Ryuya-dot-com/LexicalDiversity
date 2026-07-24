import csv
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from ldfreq import frequency as FRQ
from ldfreq import indices as IDX


class IndicesTests(unittest.TestCase):
    def test_effective_min_tokens_reflects_runtime_parameters(self):
        self.assertEqual(IDX.effective_min_tokens("mattr", window=80), 80)
        self.assertEqual(IDX.effective_min_tokens("msttr", segment=90), 90)
        self.assertEqual(IDX.effective_min_tokens("hdd", hdd_sample=60), 60)
        self.assertEqual(
            IDX.effective_min_tokens("mtld", min_tokens_override=75),
            75,
        )

    def test_all_indices_reports_short_text_values_with_reduced_windows(self):
        tokens = ["alpha", "beta", "alpha", "gamma", "delta"]

        out = IDX.all_indices(tokens, segment=50, window=50, hdd_sample=42)

        self.assertAlmostEqual(out["msttr"], 0.8)
        self.assertAlmostEqual(out["mattr"], 0.8)
        self.assertAlmostEqual(out["hdd"], 0.8)


class FrequencyTests(unittest.TestCase):
    def test_p_lex_is_bounded_by_segment_length(self):
        all_hard = [("x", None)] * 100
        all_easy = [("the", 1)] * 100

        self.assertLessEqual(FRQ.p_lex(all_hard)["lambda"], 10)
        self.assertEqual(FRQ.p_lex(all_easy)["lambda"], 0)

    def test_p_lex_matches_meara_bell_profile_example(self):
        # Meara & Bell's worked profile: 4 segments with 0 hard words,
        # 4 with 1 hard word, and 2 with 2 hard words gives lambda ~= 0.92.
        mapped = []
        for hard_count in [0, 0, 0, 0, 1, 1, 1, 1, 2, 2]:
            mapped.extend([("hard", None)] * hard_count)
            mapped.extend([("easy", 1)] * (10 - hard_count))

        self.assertAlmostEqual(FRQ.p_lex(mapped)["lambda"], 0.92, places=2)

    def test_pct_beyond_k_is_family_based(self):
        mapped = [
            ("easy", 1),
            ("easy", 1),
            ("hard", 2501),
            ("off", None),
            ("off", None),
        ]

        self.assertAlmostEqual(FRQ.pct_beyond_k(mapped, k=2), 100 * 2 / 3)

    def test_lfp_off_list_row_keeps_in_list_cumulative_coverage(self):
        rows = FRQ.lexical_frequency_profile(
            [("k1", 1), ("k2", 1001), ("off", None)],
            n_levels=2,
        )

        self.assertEqual(rows[-1]["level"], "off-list")
        self.assertEqual(rows[-1]["coverage_%"], 33.33)
        self.assertEqual(rows[-1]["cumulative_%"], 66.67)

    def test_s_index_reference_note_handles_percent_sign(self):
        mapped = [("the", 1), ("off", None)] * 25
        out = FRQ.s_index(mapped, ranks=(500, 1000))

        self.assertIn("100%", out["reference_list_note"])
        self.assertIn("1000", out["reference_list_note"])

    def test_load_ngsl_directory_uses_stats_ranks_and_lemma_forms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (root / "NGSL_1.2_stats.csv").open("w", encoding="utf-8", newline="") as fh:
                writer = csv.writer(fh)
                writer.writerow(["Lemma", "SFI Rank", "SFI", "Adjusted Frequency per Million (U)"])
                writer.writerow(["the", "1", "87.85", "60910"])
                writer.writerow(["abandon", "2", "50.00", "100"])

            with (root / "NGSL_1.2_lemmatized_for_research.csv").open(
                "w", encoding="utf-8", newline=""
            ) as fh:
                writer = csv.writer(fh)
                writer.writerow(["## comment"])
                writer.writerow(["the"])
                writer.writerow(["abandon", "abandons", "abandoned"])

            rank, meta = FRQ.load_ngsl(str(root))

        self.assertEqual(rank["the"], 1)
        self.assertEqual(rank["abandoned"], 2)
        self.assertEqual(meta["entries"], 2)
        self.assertEqual(meta["variants"], 2)

    def test_load_headword_bands_accepts_nested_zip_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "NationBNCCOCA"
            nested.mkdir()
            (nested / "headwords 1st 1000.txt").write_text("the\nof\n", encoding="utf-8")

            rank, meta = FRQ.load_headword_bands(str(root))

        self.assertEqual(rank["the"], 1)
        self.assertEqual(rank["of"], 2)
        self.assertEqual(meta["n_levels"], 1)

    def test_load_bnc_coca_families_xlsx_maps_related_forms_to_family_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "BNC_COCA_lists.xlsx"
            workbook = Workbook()
            sheet = workbook.active
            sheet.append(["List ", "Headword ", "Related forms", "Total frequency"])
            sheet.append([
                "1k",
                "accept",
                "accept (10), accepted (5), acceptance (3), unacceptable (2)",
                20,
            ])
            sheet.append(["2k", "xray", "xray (4), xrays (2)", 6])
            workbook.save(path)

            rank, meta = FRQ.load_bnc_coca_families_xlsx(str(path))

        self.assertEqual(meta["lookup_unit"], "word_family")
        self.assertEqual(meta["entries"], 2)
        self.assertEqual(rank["accepted"]["head"], "accept")
        mapped = FRQ.map_tokens(
            ["accepted", "acceptance", "xrays", "unknown"],
            rank,
            lemmatizer=type("L", (), {"normalize": lambda self, token: token})(),
        )
        self.assertEqual(mapped[:3], [("accept", 1), ("accept", 1), ("xray", 1001)])

    def test_load_range_baseword_lists_maps_indented_members_to_baseword(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "basewrd2.txt").write_text(
                "ZETA\n"
                "  zetas\n",
                encoding="utf-8",
            )
            (root / "basewrd1.txt").write_text(
                "# ignored comment\n"
                "ABLE\n"
                "  ability\n"
                "  unable\n"
                "ABOUT\n",
                encoding="utf-8",
            )

            rank, meta = FRQ.load_range_baseword_lists(str(root))

        self.assertEqual(meta["lookup_unit"], "range_word_family")
        self.assertEqual(meta["entries"], 3)
        self.assertEqual(meta["source_files"], ["basewrd1.txt", "basewrd2.txt"])
        self.assertEqual(rank["ability"]["head"], "able")
        self.assertEqual(rank["unable"]["rank"], 1)
        self.assertEqual(rank["about"]["head"], "about")
        self.assertEqual(rank["zetas"]["rank"], 1001)

    def test_band_wise_diversity_reports_short_band_values(self):
        mapped = [("alpha", 1), ("beta", 1), ("alpha", 1)]
        tokens = ["alpha", "beta", "alpha"]

        rows = FRQ.band_wise_diversity(mapped, tokens, n_levels=1, min_tokens=50)
        k1 = rows[0]

        self.assertAlmostEqual(k1["MATTR"], 2 / 3)
        self.assertAlmostEqual(k1["HD-D"], 2 / 3)
        self.assertEqual(k1["Min N"], 50)

    def test_mtld_factor_closes_on_threshold_boundary(self):
        boundary = [str(i) for i in range(18)] + ["0"] * 7
        seq = boundary + [f"new-{i}" for i in range(25)]

        self.assertAlmostEqual(len(set(boundary)) / len(boundary), 0.72)
        self.assertEqual(IDX._mtld_pass(seq, threshold=0.72), 50)


if __name__ == "__main__":
    unittest.main()
