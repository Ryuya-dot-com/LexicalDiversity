import csv
import gzip
import hashlib
import json
import lzma
import math
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import nltk
from scripts import fetch_tubelex as FETCH
from ldfreq.tubelex import (
    ARTIFACT_NAME,
    TUBELEX_EN_CHANNEL_UNSEEN_LOG10_PREVALENCE,
    TUBELEX_EN_FREQUENCY_UNSEEN_ZIPF,
    TUBELEX_EN_SOURCE_TOTAL_CHANNELS,
    TUBELEX_EN_SOURCE_TOTAL_TOKENS,
    TUBELEX_EN_SOURCE_TOTAL_VIDEOS,
    TUBELEX_EN_SOURCE_VOCABULARY_SIZE,
    TUBELEX_EN_VIDEO_UNSEEN_LOG10_PREVALENCE,
    TUBELEX_AUDITED_NLTK_VERSIONS,
    TubelexIndex,
    TubelexRecord,
    aggregate_tubelex_document,
    build_tubelex_aggregates,
    load_tubelex_index,
    load_verified_tubelex_index,
    sha256_file,
    summarize_tubelex_text,
    tokenize_tubelex_text,
)


HEADER = (
    "word\tcount\tvideos\tchannels\tcount:education\tcount:gaming\n"
)
ROWS = (
    'zeta\t2\t2\t1\t1\t1\n'
    "[TOTAL]\t6\t3\t2\t4\t2\n"
    '"unmatched\t1\t1\t1\t1\t0\n'
    "alpha\t3\t2\t2\t2\t1\n"
)
FIXTURE = HEADER + ROWS


def _write_source(path: Path, content: str = FIXTURE) -> None:
    if path.name.endswith(".xz"):
        with lzma.open(path, "wt", encoding="utf-8", newline="") as output:
            output.write(content)
    else:
        path.write_text(content, encoding="utf-8", newline="")


def _artifact_rows(path: Path) -> list[list[str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        return list(csv.reader(source))


class TubelexBuilderTests(unittest.TestCase):
    def test_builds_sorted_deterministic_artifact_without_csv_quoting_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "published.tsv.xz"
            output = root / "out"
            _write_source(source)

            manifest = build_tubelex_aggregates(
                source,
                output,
                expected_source_sha256=sha256_file(source),
                expected_source_bytes=source.stat().st_size,
                acquired_on="2026-07-22",
            )

            rows = _artifact_rows(output / ARTIFACT_NAME)
            self.assertEqual(
                rows[0],
                [
                    "word",
                    "count",
                    "videos",
                    "channels",
                    "count:education",
                    "count:gaming",
                ],
            )
            # The leading unmatched quote is a literal word character. It must
            # not cause the TSV parser to join following physical rows.
            self.assertEqual([row[0] for row in rows[1:]], ["alpha", "zeta", "[TOTAL]"])
            self.assertEqual(manifest["artifact"]["rows"], 2)
            self.assertEqual(manifest["build"]["physical_rows"], 5)
            self.assertEqual(manifest["totals"]["count"], 6)
            self.assertEqual(
                manifest["build"]["lookup_filter"]["source_vocabulary_size"], 3
            )
            self.assertEqual(manifest["build"]["lookup_filter"]["retained_rows"], 2)
            self.assertEqual(manifest["build"]["lookup_filter"]["excluded_rows"], 1)
            self.assertEqual(
                manifest["build"]["lookup_filter"]["retained_token_mass"], 5
            )
            self.assertEqual(
                manifest["source"]["checksum_check"],
                "matched-caller-supplied-expected-sha256",
            )

    def test_plain_and_xz_inputs_produce_identical_aggregate_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plain = root / "input.tsv"
            compressed = root / "input.tsv.xz"
            _write_source(plain)
            _write_source(compressed)
            plain_out = root / "plain"
            xz_out = root / "xz"

            build_tubelex_aggregates(plain, plain_out)
            build_tubelex_aggregates(compressed, xz_out)

            self.assertEqual(
                (plain_out / ARTIFACT_NAME).read_bytes(),
                (xz_out / ARTIFACT_NAME).read_bytes(),
            )
            self.assertEqual(
                (plain_out / "NOTICE.md").read_bytes(),
                (xz_out / "NOTICE.md").read_bytes(),
            )
            # gzip MTIME bytes are zero, independently of build time.
            self.assertEqual((plain_out / ARTIFACT_NAME).read_bytes()[4:8], b"\0\0\0\0")

    def test_manifest_and_notice_are_path_free_and_contain_no_source_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "private-local-input-name.tsv"
            output = root / "out"
            _write_source(source)
            build_tubelex_aggregates(source, output)

            self.assertEqual(
                {path.name for path in output.iterdir()},
                {ARTIFACT_NAME, "manifest.json", "NOTICE.md"},
            )
            manifest_text = (output / "manifest.json").read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertNotIn(str(source), manifest_text)
            self.assertNotIn(source.name, manifest_text)
            self.assertFalse(manifest["privacy"]["raw_subtitles_bundled"])
            self.assertFalse(manifest["privacy"]["contiguous_subtitle_passages_bundled"])
            self.assertTrue(manifest["privacy"]["published_frequency_keys_bundled"])
            self.assertFalse(manifest["privacy"]["video_ids_bundled"])
            self.assertFalse(manifest["privacy"]["channel_ids_bundled"])
            self.assertFalse(manifest["privacy"]["document_names_bundled"])
            self.assertEqual(
                manifest["artifact"]["sha256"],
                hashlib.sha256((output / ARTIFACT_NAME).read_bytes()).hexdigest(),
            )
            notice = (output / "NOTICE.md").read_text(encoding="utf-8")
            self.assertIn("1. Redistributions of source code", notice)
            self.assertIn("2. Redistributions in binary form", notice)
            self.assertIn("3. Neither the name of the copyright holder", notice)
            self.assertIn('PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"', notice)

    def test_source_checksum_mismatch_fails_before_creating_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.tsv"
            output = root / "out"
            _write_source(source)
            with self.assertRaisesRegex(ValueError, "Source SHA-256 mismatch"):
                build_tubelex_aggregates(
                    source,
                    output,
                    expected_source_sha256="0" * 64,
                )
            self.assertFalse(output.exists())

    def test_rejects_schema_duplicates_negative_counts_and_bad_totals(self):
        invalid_cases = {
            "schema": FIXTURE.replace("word\tcount", "token\tcount", 1),
            "duplicate": HEADER + "alpha\t1\t1\t1\t1\t0\n" * 2 + "[TOTAL]\t2\t1\t1\t2\t0\n",
            "negative": FIXTURE.replace("zeta\t2", "zeta\t-2", 1),
            "row_category_total": FIXTURE.replace("zeta\t2\t2\t1\t1\t1", "zeta\t2\t2\t1\t2\t1", 1),
            "declared_total": FIXTURE.replace("[TOTAL]\t6", "[TOTAL]\t7", 1),
            "duplicate_total": FIXTURE + "[TOTAL]\t6\t3\t2\t4\t2\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, content in invalid_cases.items():
                with self.subTest(name=name):
                    source = root / f"{name}.tsv"
                    _write_source(source, content)
                    with self.assertRaises(ValueError):
                        build_tubelex_aggregates(source, root / f"out-{name}")

    def test_enforces_row_line_and_decompressed_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "input.tsv.xz"
            _write_source(source)
            with self.assertRaisesRegex(ValueError, "row limit"):
                build_tubelex_aggregates(source, root / "rows", max_rows=2)
            with self.assertRaisesRegex(ValueError, "line-size limit"):
                build_tubelex_aggregates(source, root / "lines", max_line_bytes=20)
            with self.assertRaisesRegex(ValueError, "decompressed-size limit"):
                build_tubelex_aggregates(
                    source,
                    root / "expanded",
                    max_decompressed_bytes=len(FIXTURE.encode("utf-8")) - 1,
                )


class TubelexFetchTests(unittest.TestCase):
    def test_failed_validation_cannot_replace_existing_production_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "production"
            output.mkdir()
            old = {
                name: f"reviewed-{name}".encode("utf-8")
                for name in FETCH._PROMOTION_ORDER
            }
            for name, payload in old.items():
                (output / name).write_bytes(payload)

            def fake_build(_source, staging, **_kwargs):
                for name in FETCH._PROMOTION_ORDER:
                    (Path(staging) / name).write_bytes(f"new-{name}".encode("utf-8"))
                return {
                    "artifact": {
                        "file": FETCH.TUBELEX.ARTIFACT_NAME,
                        "bytes": FETCH.TUBELEX.PRODUCTION_ARTIFACT_BYTES,
                        "sha256": "0" * 64,
                        "rows": FETCH.TUBELEX.PRODUCTION_ARTIFACT_ROWS,
                    },
                    "build": {
                        "lookup_filter": {
                            "source_vocabulary_size": (
                                FETCH.TUBELEX.TUBELEX_EN_SOURCE_VOCABULARY_SIZE
                            ),
                            "retained_token_mass": (
                                FETCH.TUBELEX.TUBELEX_EN_RETAINED_TOKEN_MASS
                            ),
                        }
                    },
                }

            with mock.patch.object(
                FETCH.TUBELEX,
                "build_tubelex_aggregates",
                side_effect=fake_build,
            ):
                with self.assertRaisesRegex(RuntimeError, "did not reproduce"):
                    FETCH.build(root / "source.tsv.xz", output)

            for name, payload in old.items():
                self.assertEqual((output / name).read_bytes(), payload)

    def test_promotion_rolls_back_all_known_files_on_mid_commit_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staging = root / "staging"
            output = root / "production"
            staging.mkdir()
            output.mkdir()
            for name in FETCH._PROMOTION_ORDER:
                (staging / name).write_text(f"new-{name}", encoding="utf-8")
                (output / name).write_text(f"old-{name}", encoding="utf-8")

            real_replace = FETCH.os.replace

            def fail_on_notice(source, destination):
                if Path(source) == staging / "NOTICE.md":
                    raise OSError("simulated promotion failure")
                return real_replace(source, destination)

            with mock.patch.object(FETCH.os, "replace", side_effect=fail_on_notice):
                with self.assertRaisesRegex(OSError, "simulated"):
                    FETCH._promote_verified_build(staging, output)

            for name in FETCH._PROMOTION_ORDER:
                self.assertEqual(
                    (output / name).read_text(encoding="utf-8"),
                    f"old-{name}",
                )


class TubelexRuntimeTests(unittest.TestCase):
    def _build(self, root: Path) -> tuple[Path, dict[str, object]]:
        source = root / "source.tsv.xz"
        output = root / "out"
        _write_source(source)
        manifest = build_tubelex_aggregates(
            source,
            output,
            expected_source_sha256=sha256_file(source),
        )
        return output / ARTIFACT_NAME, manifest

    def test_runtime_lookup_and_document_aggregation(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact, _manifest = self._build(Path(tmp))
            index = load_tubelex_index(artifact, source_vocabulary_size=3)

            self.assertEqual(len(index), 2)
            self.assertEqual(index.categories, ("education", "gaming"))
            self.assertEqual(index.totals.count, 6)
            self.assertEqual(index.source_vocabulary_size, 3)
            self.assertEqual(index.retained_token_mass, 5)
            self.assertEqual(index.lookup("ＡＬＰＨＡ").word, "alpha")
            self.assertIsNone(index.lookup("unknown"))

            result = aggregate_tubelex_document(
                ["ALPHA", "alpha", "zeta", "unknown"], index
            )
            self.assertEqual(
                set(result),
                {
                    "tokens",
                    "types",
                    "covered_tokens",
                    "covered_types",
                    "token_coverage",
                    "type_coverage",
                    "frequency_zipf_token_mean",
                    "frequency_zipf_type_mean",
                    "video_log10_prevalence_token_mean",
                    "video_log10_prevalence_type_mean",
                    "channel_log10_prevalence_token_mean",
                    "channel_log10_prevalence_type_mean",
                },
            )
            self.assertEqual(result["tokens"], 4)
            self.assertEqual(result["types"], 3)
            self.assertEqual(result["covered_tokens"], 3)
            self.assertEqual(result["covered_types"], 2)
            self.assertEqual(result["token_coverage"], 0.75)
            alpha_zipf = math.log10(1_000_000_000 * 4 / 9)
            zeta_zipf = math.log10(1_000_000_000 * 3 / 9)
            unseen_zipf = math.log10(1_000_000_000 / 9)
            self.assertAlmostEqual(
                result["frequency_zipf_token_mean"],
                (alpha_zipf * 2 + zeta_zipf + unseen_zipf) / 4,
            )
            self.assertAlmostEqual(
                result["frequency_zipf_type_mean"],
                (alpha_zipf + zeta_zipf + unseen_zipf) / 3,
            )
            self.assertAlmostEqual(
                result["video_log10_prevalence_token_mean"],
                (math.log10(3 / 5) * 3 + math.log10(1 / 5)) / 4,
            )
            self.assertNotIn("category_entropy_token_mean", result)

    def test_treebank_adapter_matches_pinned_contraction_units(self):
        self.assertEqual(
            tokenize_tubelex_text("Don't can't won't I'm."),
            ["do", "n't", "ca", "n't", "wo", "n't", "i", "'m"],
        )
        self.assertEqual(
            tokenize_tubelex_text("John's well-being at 42, café."),
            ["john", "'s", "well-being", "at", "café"],
        )
        self.assertEqual(
            tokenize_tubelex_text("I'm. Next line.\nI am. Ready? Yes!"),
            ["i", "'m", "next", "line", "i", "am", "ready", "yes"],
        )
        # The model-free pre-segmenter may split abbreviations; unlike silent
        # token loss, the lexical output remains deterministic and explicit.
        self.assertEqual(
            tokenize_tubelex_text("Dr. Smith left."),
            ["dr", "smith", "left"],
        )
        self.assertEqual(tokenize_tubelex_text("a" * 65), [])

    def test_typographic_apostrophes_are_counted_as_contraction_units(self):
        self.assertEqual(
            tokenize_tubelex_text("I’m ready; I can’t leave John’s book."),
            ["i", "'m", "ready", "i", "ca", "n't", "leave", "john", "'s", "book"],
        )

    def test_unaudited_nltk_version_fails_closed(self):
        self.assertIn(nltk.__version__, TUBELEX_AUDITED_NLTK_VERSIONS)
        with mock.patch.object(nltk, "__version__", "999.0"):
            with self.assertRaisesRegex(RuntimeError, "audited nltk version"):
                tokenize_tubelex_text("ordinary text")

    def test_full_text_summary_tokenizes_once(self):
        index = TubelexIndex(
            [
                TubelexRecord("well-being", 2, 2, 1, (2,)),
                TubelexRecord("unknown", 1, 1, 1, (1,)),
            ],
            categories=("one",),
            totals=TubelexRecord("[TOTAL]", 3, 2, 1, (3,)),
            source_vocabulary_size=2,
        )
        summary = summarize_tubelex_text("Well-being unknown.", index)
        self.assertEqual(summary["tokens"], 2)
        self.assertEqual(summary["covered_tokens"], 2)
        self.assertEqual(
            aggregate_tubelex_document("Well-being unknown.", index), summary
        )

    def test_empty_document_returns_zero_coverage_and_no_means(self):
        index = TubelexIndex(
            [TubelexRecord("a", 1, 1, 1, (1,))],
            categories=("one",),
            totals=TubelexRecord("[TOTAL]", 1, 1, 1, (1,)),
        )
        result = aggregate_tubelex_document([], index)
        self.assertEqual(result["tokens"], 0)
        self.assertEqual(result["token_coverage"], 0.0)
        self.assertIsNone(result["frequency_zipf_token_mean"])
        self.assertIsNone(result["channel_log10_prevalence_type_mean"])

    def test_production_unseen_floor_constants_match_public_formulas(self):
        self.assertAlmostEqual(
            TUBELEX_EN_FREQUENCY_UNSEEN_ZIPF,
            math.log10(
                1_000_000_000
                / (
                    TUBELEX_EN_SOURCE_TOTAL_TOKENS
                    + TUBELEX_EN_SOURCE_VOCABULARY_SIZE
                )
            ),
        )
        self.assertAlmostEqual(
            TUBELEX_EN_VIDEO_UNSEEN_LOG10_PREVALENCE,
            math.log10(1 / (TUBELEX_EN_SOURCE_TOTAL_VIDEOS + 2)),
        )
        self.assertAlmostEqual(
            TUBELEX_EN_CHANNEL_UNSEEN_LOG10_PREVALENCE,
            math.log10(1 / (TUBELEX_EN_SOURCE_TOTAL_CHANNELS + 2)),
        )

    def test_verified_loader_checks_manifest_and_external_source_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            artifact, manifest = self._build(Path(tmp))
            index = load_verified_tubelex_index(
                artifact,
                expected_artifact_sha256=manifest["artifact"]["sha256"],
                expected_artifact_bytes=manifest["artifact"]["bytes"],
                expected_source_sha256=manifest["source"]["sha256"],
                expected_resource_id=manifest["id"],
            )
            self.assertEqual(len(index), 2)
            self.assertEqual(index.metadata["id"], manifest["id"])

    def test_verified_loader_rejects_artifact_or_manifest_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, _manifest = self._build(root)
            artifact.write_bytes(artifact.read_bytes() + b"tamper")
            with self.assertRaisesRegex(ValueError, "size mismatch"):
                load_verified_tubelex_index(artifact)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact, _manifest = self._build(root)
            manifest_path = artifact.with_name("manifest.json")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["totals"]["count"] += 1
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "total-count mismatch"):
                load_verified_tubelex_index(artifact)


if __name__ == "__main__":
    unittest.main()
