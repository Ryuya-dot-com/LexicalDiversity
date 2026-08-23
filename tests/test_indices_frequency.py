import csv
from decimal import Decimal
import math
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

from ldfreq import frequency as FRQ
from ldfreq import indices as IDX


class IndicesTests(unittest.TestCase):
    def test_every_direct_metric_rejects_non_token_sequences(self):
        functions = (
            IDX.ttr,
            IDX.rttr,
            IDX.cttr,
            IDX.herdan,
            IDX.maas,
            IDX.msttr,
            IDX.mattr,
            IDX.mtld,
            IDX.hdd,
            IDX.vocd,
            IDX.yule_k,
            IDX.yule_i,
        )
        invalid_factories = (
            ("bare string", lambda: "alpha beta"),
            ("bytes", lambda: b"alpha beta"),
            ("generator", lambda: (token for token in ("alpha", "beta"))),
            ("none", lambda: None),
            ("numeric", lambda: 123),
            ("mixed", lambda: ["alpha", 2]),
            ("unhashable", lambda: ["alpha", ["beta"]]),
        )
        for function in functions:
            for label, factory in invalid_factories:
                with self.subTest(function=function.__name__, input=label):
                    with self.assertRaisesRegex(
                        TypeError,
                        "tokens must be a materialized sequence of strings|"
                        "every token must be a string",
                    ):
                        function(factory())

        self.assertEqual(IDX.ttr(["alpha", "beta"]), 1.0)
        self.assertEqual(IDX.ttr(("alpha", "alpha")), 0.5)

    def test_every_direct_metric_rejects_invalid_token_content(self):
        functions = (
            IDX.ttr,
            IDX.rttr,
            IDX.cttr,
            IDX.herdan,
            IDX.maas,
            IDX.msttr,
            IDX.mattr,
            IDX.mtld,
            IDX.hdd,
            IDX.vocd,
            IDX.yule_k,
            IDX.yule_i,
        )
        for function in functions:
            with self.subTest(function=function.__name__, content="empty"):
                with self.assertRaisesRegex(ValueError, "empty strings"):
                    function([""])
            with self.subTest(function=function.__name__, content="surrogate"):
                with self.assertRaisesRegex(ValueError, "valid UTF-8"):
                    function(["\ud800"])

    def test_record_and_projection_apis_enforce_token_and_boolean_contracts(self):
        invalid_factories = (
            (TypeError, lambda: "alpha beta"),
            (TypeError, lambda: b"alpha beta"),
            (TypeError, lambda: (token for token in ("alpha", "beta"))),
            (TypeError, lambda: None),
            (TypeError, lambda: 123),
            (TypeError, lambda: ["alpha", 2]),
            (TypeError, lambda: ["alpha", ["beta"]]),
            (ValueError, lambda: [""]),
            (ValueError, lambda: ["\ud800"]),
        )
        for function in (IDX.all_index_records, IDX.all_indices):
            for exception, factory in invalid_factories:
                with self.subTest(function=function.__name__, value=factory):
                    with self.assertRaises(exception):
                        function(factory())

        for invalid in (0, 1, None, "false"):
            with self.subTest(include_adaptive=invalid):
                with self.assertRaisesRegex(TypeError, "include_adaptive"):
                    IDX.all_index_records(
                        ["alpha"] * 60,
                        include_adaptive=invalid,
                    )
            with self.subTest(compute_below_floor=invalid):
                with self.assertRaisesRegex(TypeError, "compute_below_floor"):
                    IDX.all_indices(
                        ["alpha"] * 60,
                        compute_below_floor=invalid,
                    )

    def test_requested_parameters_rejects_mtld_identity_change(self):
        with self.assertRaisesRegex(ValueError, "fixes mtld_min_factor_len at 10"):
            IDX.requested_parameters(mtld_min_factor_len=5)

    def test_direct_methods_reject_invalid_parameters_before_computation(self):
        tokens = ["same"] * 60
        cases = [
            (IDX.msttr, {"segment": True}),
            (IDX.msttr, {"segment": 50.0}),
            (IDX.msttr, {"segment": 0}),
            (IDX.msttr, {"segment": -1}),
            (IDX.mattr, {"window": True}),
            (IDX.mattr, {"window": 50.0}),
            (IDX.mattr, {"window": 0}),
            (IDX.mattr, {"window": -1}),
            (IDX.hdd, {"sample": True}),
            (IDX.hdd, {"sample": 42.0}),
            (IDX.hdd, {"sample": 0}),
            (IDX.hdd, {"sample": -1}),
            (IDX.mtld, {"threshold": "0.72"}),
            (IDX.mtld, {"threshold": Decimal("0.72")}),
            (IDX.mtld, {"threshold": math.nan}),
            (IDX.mtld, {"threshold": math.inf}),
            (IDX.mtld, {"threshold": True}),
            (IDX.mtld, {"threshold": 0}),
            (IDX.mtld, {"threshold": 1}),
            (IDX.mtld, {"min_factor_len": True}),
            (IDX.mtld, {"min_factor_len": 10.0}),
            (IDX.mtld, {"min_factor_len": 0}),
            (IDX.mtld, {"min_factor_len": -1}),
            (IDX.vocd, {"lo": 51, "hi": 50}),
            (IDX.vocd, {"lo": 0}),
            (IDX.vocd, {"hi": 0}),
            (IDX.vocd, {"trials": 0}),
            (IDX.vocd, {"trials": -1}),
            (IDX.vocd, {"trials": True}),
            (IDX.vocd, {"trials": 100.0}),
            (IDX.vocd, {"runs": 0}),
            (IDX.vocd, {"runs": -1}),
            (IDX.vocd, {"runs": True}),
            (IDX.vocd, {"runs": 3.0}),
            (IDX.vocd, {"grid_max": 1}),
            (IDX.vocd, {"grid_max": 0}),
            (IDX.vocd, {"grid_max": math.nan}),
            (IDX.vocd, {"grid_max": math.inf}),
            (IDX.vocd, {"seed": -1}),
            (IDX.vocd, {"seed": True}),
            (IDX.vocd, {"seed": 42.0}),
        ]
        for function, kwargs in cases:
            with self.subTest(function=function.__name__, kwargs=kwargs):
                with self.assertRaises(ValueError):
                    function(tokens, **kwargs)

    def test_record_api_rejects_invalid_or_identity_changing_parameters(self):
        cases = [
            {"segment": True},
            {"segment": 50.0},
            {"segment": 0},
            {"segment": -1},
            {"window": True},
            {"window": 50.0},
            {"window": -1},
            {"hdd_sample": True},
            {"hdd_sample": 42.0},
            {"hdd_sample": 0},
            {"hdd_sample": -1},
            {"mtld_threshold": "0.72"},
            {"mtld_threshold": Decimal("0.72")},
            {"mtld_threshold": math.nan},
            {"mtld_threshold": True},
            {"mtld_threshold": 0},
            {"mtld_threshold": 1},
            {"mtld_min_factor_len": 5},
            {"mtld_min_factor_len": True},
            {"mtld_min_factor_len": 10.0},
            {"mtld_min_factor_len": 0},
            {"mtld_min_factor_len": -1},
            {"vocd_lo": 51, "vocd_hi": 50},
            {"vocd_lo": 0},
            {"vocd_hi": 0},
            {"vocd_trials": 0},
            {"vocd_trials": -1},
            {"vocd_trials": True},
            {"vocd_trials": 100.0},
            {"vocd_runs": 0},
            {"vocd_runs": -1},
            {"vocd_grid_max": 1},
            {"vocd_grid_max": 0},
            {"vocd_grid_max": math.nan},
            {"vocd_grid_max": math.inf},
            {"vocd_seed": -1},
            {"vocd_seed": True},
            {"min_tokens_override": 75.9},
            {"min_tokens_override": True},
            {"min_tokens_override": 0},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    IDX.all_index_records(["same"] * 60, **kwargs)

    def test_shared_formula_method_ids_match_cross_language_contract(self):
        self.assertEqual(IDX.METHOD_IDS["herdan"], "herdan_c_logv_over_logn_v1")
        self.assertEqual(IDX.METHOD_IDS["maas"], "maas_a2_ln_v1")
        self.assertEqual(IDX.METHOD_IDS["hdd"], "hdd_expected_ttr_scaled_v1")
        self.assertIn("python", IDX.METHOD_IDS["mtld"])
        self.assertIn("python", IDX.METHOD_IDS["vocd"])

    def test_effective_min_tokens_reflects_runtime_parameters(self):
        self.assertEqual(IDX.effective_min_tokens("mattr", window=80), 80)
        self.assertEqual(IDX.effective_min_tokens("msttr", segment=90), 90)
        self.assertEqual(IDX.effective_min_tokens("hdd", hdd_sample=60), 60)
        self.assertEqual(
            IDX.effective_min_tokens("mtld", min_tokens_override=75),
            75,
        )

    def test_standard_indices_do_not_shrink_short_text_parameters(self):
        tokens = ["alpha", "beta", "alpha", "gamma", "delta"]

        out = IDX.all_indices(tokens, segment=50, window=50, hdd_sample=42)

        self.assertIsNone(out["msttr"])
        self.assertIsNone(out["mattr"])
        self.assertIsNone(out["hdd"])
        self.assertIsNone(out["vocd"])

        records = IDX.all_index_records(tokens)
        for key in ("msttr", "mattr", "hdd", "vocd"):
            self.assertEqual(records[key]["status"], "missing")
            self.assertEqual(
                records[key]["missing_reason"],
                "too_short_for_requested_parameter",
            )
            self.assertEqual(records[key]["effective_parameters"], {})

    def test_adaptive_values_use_distinct_keys_and_method_ids(self):
        tokens = ["alpha", "beta", "alpha", "gamma", "delta"]

        out = IDX.all_indices(tokens, compute_below_floor=True)
        records = IDX.all_index_records(tokens, include_adaptive=True)

        self.assertIsNone(out["msttr"])
        self.assertAlmostEqual(out["msttr_adaptive"], 0.8)
        self.assertAlmostEqual(out["mattr_adaptive"], 0.8)
        self.assertAlmostEqual(out["hdd_adaptive"], 0.8)
        self.assertNotEqual(
            records["msttr"]["method_id"],
            records["msttr_adaptive"]["method_id"],
        )
        self.assertNotEqual(
            records["msttr_adaptive"]["requested_parameters"],
            records["msttr_adaptive"]["effective_parameters"],
        )

    def test_advisory_floor_does_not_suppress_computable_metrics(self):
        records = IDX.all_index_records(["same"] * 10)

        self.assertEqual(records["mtld"]["status"], "available")
        self.assertEqual(records["mtld"]["value"], 10.0)
        self.assertEqual(
            records["mtld"]["advisory_quality_status"],
            "below_advisory_floor",
        )
        self.assertEqual(records["yule_k"]["status"], "available")
        self.assertEqual(
            records["yule_k"]["advisory_quality_status"],
            "below_advisory_floor",
        )

    def test_mtld_has_explicit_python_variant_and_minimum_domain(self):
        self.assertTrue(math.isnan(IDX.mtld(["same"] * 9)))

        record = IDX.all_index_records(["same"] * 9)["mtld"]
        self.assertEqual(record["status"], "missing")
        self.assertEqual(
            record["missing_reason"],
            "insufficient_tokens_for_formula",
        )
        self.assertIn("leq_min10_python", record["method_id"])
        self.assertEqual(
            record["requested_parameters"]["factor_boundary_comparator"],
            "<=",
        )

    def test_shared_method_missing_reasons_match_cross_language_contract(self):
        empty = IDX.all_index_records([])
        for key in IDX._FUNCS:
            self.assertEqual(empty[key]["missing_reason"], "empty_input", key)
            self.assertEqual(empty[key]["effective_parameters"], {}, key)

        empty_adaptive = IDX.all_index_records([], include_adaptive=True)
        for key in IDX.ADAPTIVE_METHOD_IDS:
            self.assertEqual(empty_adaptive[key]["status"], "missing", key)
            self.assertEqual(empty_adaptive[key]["effective_parameters"], {}, key)

        singleton = IDX.all_index_records(["only"])
        self.assertEqual(
            singleton["herdan"]["missing_reason"],
            "insufficient_tokens_for_formula",
        )
        self.assertEqual(
            singleton["maas"]["missing_reason"],
            "insufficient_tokens_for_formula",
        )
        self.assertEqual(singleton["yule_i"]["missing_reason"], "zero_denominator")

        short = IDX.all_index_records(
            ["a", "b", "a"],
            segment=4,
            window=4,
            hdd_sample=4,
        )
        for key in ("msttr", "mattr", "hdd"):
            self.assertEqual(
                short[key]["missing_reason"],
                "too_short_for_requested_parameter",
                key,
            )

        all_distinct = IDX.all_index_records(["a", "b", "c", "d"])
        self.assertEqual(
            all_distinct["yule_i"]["missing_reason"],
            "zero_denominator",
        )

        no_factor = IDX.all_index_records([str(index) for index in range(10)])
        self.assertEqual(no_factor["mtld"]["missing_reason"], "no_factor")


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

    def test_band_wise_diversity_does_not_shrink_short_band_parameters(self):
        mapped = [("alpha", 1), ("beta", 1), ("alpha", 1)]
        tokens = ["alpha", "beta", "alpha"]

        rows = FRQ.band_wise_diversity(mapped, tokens, n_levels=1, min_tokens=50)
        k1 = rows[0]

        self.assertTrue(math.isnan(k1["MATTR"]))
        self.assertIsNone(k1["HD-D"])
        self.assertEqual(k1["Min N"], 50)

    def test_mtld_factor_closes_on_threshold_boundary(self):
        boundary = [str(i) for i in range(18)] + ["0"] * 7
        seq = boundary + [f"new-{i}" for i in range(25)]

        self.assertAlmostEqual(len(set(boundary)) / len(boundary), 0.72)
        self.assertEqual(IDX._mtld_pass(seq, threshold=0.72), 50)


if __name__ == "__main__":
    unittest.main()
