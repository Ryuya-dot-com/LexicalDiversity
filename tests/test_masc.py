import csv
import gzip
import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from ldfreq.masc import (
    BIGRAM_ARTIFACT,
    TRIGRAM_ARTIFACT,
    UNIGRAM_ARTIFACT,
    build_masc_aggregates,
)
from ldfreq.tokenizer import ASCII_LEGACY_V1


DOCUMENTS = {
    "masc/blog/one.txt": "Alpha beta. Alpha can't.",
    "masc/spoken/two.txt": "beta gamma alpha.",
    "masc/empty.txt": "--- 123 ---",
    "masc/README.md": "This documentation must not enter counts.",
}


def _make_zip(path: Path, names: list[str] | None = None) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for index, name in enumerate(names or list(DOCUMENTS)):
            info = zipfile.ZipInfo(name)
            info.date_time = (2020 + index, 1, 2, 3, 4, 6)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, DOCUMENTS[name].encode("utf-8"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rows(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


class MascAggregateBuilderTests(unittest.TestCase):
    def test_builds_surface_counts_and_resets_ngram_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "local-input.zip"
            output = root / "out"
            _make_zip(source)
            manifest = build_masc_aggregates(
                source,
                output,
                expected_source_sha256=_sha256(source),
                acquired_on="2026-07-22",
            )

            unigram_rows = {row["surface"]: row for row in _rows(output / UNIGRAM_ARTIFACT)}
            self.assertEqual(
                unigram_rows,
                {
                    "alpha": {"surface": "alpha", "frequency": "3", "document_frequency": "2"},
                    "beta": {"surface": "beta", "frequency": "2", "document_frequency": "2"},
                    "can't": {"surface": "can't", "frequency": "1", "document_frequency": "1"},
                    "gamma": {"surface": "gamma", "frequency": "1", "document_frequency": "1"},
                },
            )

            bigrams = {
                (row["token_1"], row["token_2"]): int(row["frequency"])
                for row in _rows(output / BIGRAM_ARTIFACT)
            }
            self.assertEqual(bigrams[("alpha", "beta")], 1)
            self.assertEqual(bigrams[("gamma", "alpha")], 1)
            self.assertNotIn(("can't", "beta"), bigrams)

            trigrams = {
                (row["token_1"], row["token_2"], row["token_3"]): int(row["frequency"])
                for row in _rows(output / TRIGRAM_ARTIFACT)
            }
            self.assertEqual(trigrams[("alpha", "beta", "alpha")], 1)
            self.assertEqual(trigrams[("beta", "gamma", "alpha")], 1)
            self.assertNotIn(("alpha", "can't", "beta"), trigrams)

            self.assertEqual(manifest["build"]["documents"], 3)
            self.assertEqual(manifest["build"]["empty_documents"], 1)
            self.assertEqual(manifest["build"]["ignored_members"], 1)
            self.assertEqual(manifest["build"]["tokens"], 7)
            self.assertEqual(
                manifest["build"]["tokenizer_policy"],
                ASCII_LEGACY_V1,
            )
            self.assertIn(
                f"`{ASCII_LEGACY_V1}`",
                (output / "NOTICE.md").read_text(encoding="utf-8"),
            )

    def test_outputs_only_aggregates_notice_and_path_free_provenance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sensitive-local-name.zip"
            output = root / "out"
            _make_zip(source)
            build_masc_aggregates(
                source,
                output,
                expected_source_sha256=_sha256(source),
                acquired_on="2026-07-22",
            )

            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    UNIGRAM_ARTIFACT,
                    BIGRAM_ARTIFACT,
                    TRIGRAM_ARTIFACT,
                    "NOTICE.md",
                    "manifest.json",
                },
            )
            manifest_text = (output / "manifest.json").read_text(encoding="utf-8")
            manifest = json.loads(manifest_text)
            self.assertNotIn(str(source), manifest_text)
            self.assertNotIn(source.name, manifest_text)
            self.assertFalse(manifest["source"]["bundled"])
            self.assertFalse(manifest["source"]["retrieved_by_builder"])
            self.assertEqual(
                manifest["source"]["checksum_check"],
                "matched-caller-supplied-expected-sha256",
            )
            self.assertFalse(manifest["source"]["origin_verified_by_builder"])
            self.assertFalse(manifest["privacy"]["source_text_bundled"])
            for artifact in manifest["artifacts"]:
                artifact_path = output / artifact["file"]
                self.assertEqual(artifact["bytes"], artifact_path.stat().st_size)
                self.assertEqual(artifact["sha256"], _sha256(artifact_path))

    def test_aggregate_artifacts_ignore_zip_order_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_zip = root / "first.zip"
            second_zip = root / "second.zip"
            _make_zip(first_zip)
            _make_zip(second_zip, list(reversed(DOCUMENTS)))
            first_output = root / "first"
            second_output = root / "second"
            build_masc_aggregates(first_zip, first_output)
            build_masc_aggregates(second_zip, second_output)

            for name in (UNIGRAM_ARTIFACT, BIGRAM_ARTIFACT, TRIGRAM_ARTIFACT, "NOTICE.md"):
                self.assertEqual(
                    (first_output / name).read_bytes(),
                    (second_output / name).read_bytes(),
                    name,
                )

    def test_source_digest_mismatch_stops_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            with self.assertRaisesRegex(ValueError, "Source SHA-256 mismatch"):
                build_masc_aggregates(
                    source,
                    output,
                    expected_source_sha256="0" * 64,
                )
            self.assertFalse(output.exists())

    def test_rejects_duplicate_document_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "duplicates.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(source, "w") as archive:
                    archive.writestr("same.txt", "one")
                    archive.writestr("same.txt", "two")
            with self.assertRaisesRegex(ValueError, "duplicate member names"):
                build_masc_aggregates(source, root / "out")

    def test_rejects_non_utf8_text_without_leaking_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "invalid.zip"
            with zipfile.ZipFile(source, "w") as archive:
                archive.writestr("invalid.txt", b"secret-prefix-\xff-secret-suffix")
            output = root / "out"
            with self.assertRaisesRegex(ValueError, "not valid UTF-8") as caught:
                build_masc_aggregates(source, output)
            self.assertNotIn("secret", str(caught.exception))
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
