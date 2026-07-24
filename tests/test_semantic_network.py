import gzip
import json
import tempfile
import unittest
from pathlib import Path

from ldfreq.semantic_network import (
    build_oewn_lemma_artifact,
    DEFAULT_ARTIFACT_PATH,
    load_semantic_network_index,
    load_verified_semantic_network_index,
    normalize_lemma,
    sha256_file,
    summarize_lemmas,
)


FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<LexicalResource>
  <Lexicon id="fixture" language="en" version="test">
    <LexicalEntry id="entity-n">
      <Lemma writtenForm="entity" partOfSpeech="n"/>
      <Sense id="entity-1" synset="s-root-n"/>
    </LexicalEntry>
    <LexicalEntry id="bank-n">
      <Lemma writtenForm="Bank" partOfSpeech="n"/>
      <Sense id="bank-1" synset="s-level-1-n"/>
      <Sense id="bank-2" synset="s-level-2-n"/>
    </LexicalEntry>
    <LexicalEntry id="bank-v">
      <Lemma writtenForm="bank" partOfSpeech="v"/>
      <Sense id="bank-v-1" synset="s-root-v"/>
    </LexicalEntry>
    <LexicalEntry id="blue-a">
      <Lemma writtenForm="blue" partOfSpeech="a"/>
      <Sense id="blue-1" synset="s-blue-a"/>
    </LexicalEntry>
    <Synset id="s-root-n" partOfSpeech="n"/>
    <Synset id="s-level-1-n" partOfSpeech="n">
      <SynsetRelation relType="hypernym" target="s-root-n"/>
    </Synset>
    <Synset id="s-level-2-n" partOfSpeech="n">
      <SynsetRelation relType="instance_hypernym" target="s-level-1-n"/>
    </Synset>
    <Synset id="s-root-v" partOfSpeech="v"/>
    <Synset id="s-blue-a" partOfSpeech="a"/>
  </Lexicon>
</LexicalResource>
"""


class OpenEnglishWordNetTests(unittest.TestCase):
    def _build_fixture(self, root: Path, artifact_name: str = "metrics.csv.gz") -> Path:
        source = root / "fixture.xml.gz"
        with gzip.open(source, "wt", encoding="utf-8") as fh:
            fh.write(FIXTURE_XML)
        artifact = root / artifact_name
        build_oewn_lemma_artifact(source, artifact)
        return artifact

    def test_builds_polysemy_and_hypernym_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = load_semantic_network_index(self._build_fixture(Path(tmp)))

        bank_n = index.lookup("BANK", pos="noun")
        self.assertIsNotNone(bank_n)
        self.assertEqual(bank_n.polysemy, 2)
        self.assertEqual(bank_n.depth_sense_count, 2)
        self.assertEqual(bank_n.hypernym_depth_min, 1)
        self.assertEqual(bank_n.hypernym_depth_mean, 1.5)
        self.assertEqual(bank_n.hypernym_depth_max, 2)

        blue = index.lookup("blue", pos="adj")
        self.assertEqual(blue.polysemy, 1)
        self.assertEqual(blue.depth_sense_count, 0)
        self.assertIsNone(blue.hypernym_depth_mean)

    def test_lookup_without_pos_aggregates_senses_and_weighted_depth(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = load_semantic_network_index(self._build_fixture(Path(tmp)))

        bank = index.lookup("bank")
        self.assertEqual(bank.polysemy, 3)
        self.assertEqual(bank.depth_sense_count, 3)
        self.assertEqual(bank.hypernym_depth_min, 0)
        self.assertEqual(bank.hypernym_depth_mean, 1)
        self.assertEqual(bank.hypernym_depth_max, 2)
        self.assertEqual(index.lemmas, frozenset({"bank", "blue", "entity"}))

    def test_artifact_is_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = self._build_fixture(root, "first.csv.gz")
            second = self._build_fixture(root, "second.csv.gz")
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_source_digest_mismatch_stops_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.xml.gz"
            with gzip.open(source, "wt", encoding="utf-8") as fh:
                fh.write(FIXTURE_XML)

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
                build_oewn_lemma_artifact(
                    source,
                    root / "metrics.csv.gz",
                    expected_sha256="0" * 64,
                )

    def test_summary_reports_type_and_token_weighted_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = load_semantic_network_index(self._build_fixture(Path(tmp)))

        summary = summarize_lemmas(["bank", "bank", "unknown"], index, pos="n")
        self.assertEqual(summary["tokens"], 3)
        self.assertEqual(summary["types"], 2)
        self.assertEqual(summary["covered_tokens"], 2)
        self.assertEqual(summary["covered_types"], 1)
        self.assertAlmostEqual(summary["token_coverage"], 2 / 3)
        self.assertEqual(summary["depth_covered_tokens"], 2)
        self.assertEqual(summary["depth_covered_types"], 1)
        self.assertAlmostEqual(summary["depth_token_coverage"], 2 / 3)
        self.assertAlmostEqual(summary["depth_type_coverage"], 1 / 2)
        self.assertEqual(summary["polysemy_token_mean"], 2)
        self.assertEqual(summary["hypernym_depth_token_mean"], 1.5)

    def test_depth_coverage_excludes_covered_items_without_hypernym_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            index = load_semantic_network_index(self._build_fixture(Path(tmp)))

        summary = summarize_lemmas(["bank", "blue", "unknown"], index)
        self.assertEqual(summary["covered_tokens"], 2)
        self.assertEqual(summary["depth_covered_tokens"], 1)
        self.assertAlmostEqual(summary["token_coverage"], 2 / 3)
        self.assertAlmostEqual(summary["depth_token_coverage"], 1 / 3)
        self.assertEqual(summary["hypernym_depth_token_mean"], 1)

    def test_lemma_normalization_matches_multiword_artifact_keys(self):
        self.assertEqual(normalize_lemma("  Take_A_Breath  "), "take a breath")

    def test_bundled_2025_artifact_matches_its_manifest(self):
        manifest_path = DEFAULT_ARTIFACT_PATH.with_name("manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(
            sha256_file(DEFAULT_ARTIFACT_PATH),
            manifest["artifact"]["sha256"],
        )
        index = load_verified_semantic_network_index(DEFAULT_ARTIFACT_PATH)
        self.assertEqual(len(index), manifest["artifact"]["rows"])
        self.assertEqual(index.lookup("entity", pos="noun").hypernym_depth_mean, 0)

    def test_verified_loader_rejects_joint_artifact_manifest_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifact = self._build_fixture(root)
            manifest = json.loads(
                DEFAULT_ARTIFACT_PATH.with_name("manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            manifest["artifact"].update({
                "file": artifact.name,
                "bytes": artifact.stat().st_size,
                "rows": 4,
                "sha256": sha256_file(artifact),
            })
            artifact.with_name("manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "pinned production artifact"):
                load_verified_semantic_network_index(artifact)


if __name__ == "__main__":
    unittest.main()
