import csv
import gzip
import hashlib
import json
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from ldfreq.nation_bnc_coca import (
    ARTIFACT_COLUMNS,
    ARTIFACT_NAME,
    LICENSE_SPDX,
    PRODUCTION_ARTIFACT_BYTES,
    PRODUCTION_ARTIFACT_ROWS,
    PRODUCTION_ARTIFACT_SHA256,
    SELECTED_MEMBER_NAMES,
    SOURCE_ASSET,
    SOURCE_BYTES,
    SOURCE_SHA256,
    SOURCE_URL,
    build_nation_bnc_coca_index,
    load_nation_bnc_coca_index,
    load_verified_nation_bnc_coca_index,
)


def _contents() -> dict[str, bytes]:
    members: dict[str, bytes] = {}
    for band in range(1, 26):
        if band == 1:
            text = "ACCEPT 0\n\tACCEPTED 0\n\tACCEPTANCE 0\nABOUT 0\n"
        elif band == 2:
            # ACCEPTED deliberately collides with band 1; the earlier band wins.
            text = "BETA 0\n\tBETAS 0\n\tACCEPTED 0\n"
        else:
            text = f"HEAD{band} 0\n\tMEMBER{band} 0\n"
        members[f"range-data/basewrd{band}.txt"] = text.encode("utf-8")
    members["range-data/basewrd26.txt"] = b"FORBIDDEN 0\n"
    members["range-data/basewrd34.txt"] = b"ALSOFORBIDDEN 0\n"
    members["range-data/range.txt"] = b"documentation must not become a form"
    members["range-data/Range32.exe"] = b"MZ-not-a-real-executable"
    return members


def _make_zip(path: Path, *, reverse: bool = False, timestamp_year: int = 2020) -> None:
    items = list(_contents().items())
    if reverse:
        items.reverse()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for offset, (name, payload) in enumerate(items):
            info = zipfile.ZipInfo(name)
            info.date_time = (timestamp_year, 1, 2, 3, 4, 6 + 2 * (offset % 20))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, payload)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _build_fixture(source: Path, output: Path):
    return build_nation_bnc_coca_index(
        source,
        output,
        expected_source_sha256=_sha256(source),
        expected_source_bytes=source.stat().st_size,
        acquired_on="2026-07-22",
    )


def _artifact_rows(path: Path) -> list[dict[str, str]]:
    with gzip.open(path, "rt", encoding="utf-8", newline="") as source:
        return list(csv.DictReader(source))


class NationBncCocaBuilderTests(unittest.TestCase):
    def test_official_source_identity_is_pinned(self):
        self.assertEqual(SOURCE_BYTES, 600_930)
        self.assertEqual(
            SOURCE_SHA256,
            "ac81c7a60e5c76cd2bbf0c59b0501808f0d4fa026b2936919dd54329a9bb6a69",
        )
        self.assertEqual(
            SOURCE_URL,
            "https://www.wgtn.ac.nz/lals/resources/paul-nations-resources/"
            "vocabulary-analysis-programs/range/BNC_COCA_25000.zip",
        )
        self.assertEqual(PRODUCTION_ARTIFACT_BYTES, 471_046)
        self.assertEqual(PRODUCTION_ARTIFACT_ROWS, 75_679)
        self.assertEqual(
            PRODUCTION_ARTIFACT_SHA256,
            "20c1c5a2bcf832831c9ac09a395f584a1b1cc5106b094f0cf6f5ca84b5baf081",
        )

    def test_builds_only_first_25_basewrd_bands(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "local-source.zip"
            output = root / "server-private"
            _make_zip(source)
            manifest = _build_fixture(source, output)

            rows = {row["form"]: row for row in _artifact_rows(output / ARTIFACT_NAME)}
            self.assertEqual(tuple(rows["accept"]), ARTIFACT_COLUMNS)
            self.assertEqual(
                rows["accepted"],
                {
                    "form": "accepted",
                    "head": "accept",
                    "rank": "1",
                    "band": "1",
                    "family_ordinal": "1",
                },
            )
            self.assertEqual(rows["about"]["rank"], "2")
            self.assertEqual(rows["betas"]["rank"], "1001")
            self.assertEqual(rows["member25"]["rank"], "24001")
            self.assertNotIn("forbidden", rows)
            self.assertNotIn("alsoforbidden", rows)
            self.assertNotIn("documentation", rows)

            self.assertEqual(
                manifest["build"]["selected_archive_members"],
                list(SELECTED_MEMBER_NAMES),
            )
            self.assertEqual(manifest["build"]["ignored_members"], 4)
            self.assertEqual(
                manifest["build"]["collisions_first_occurrence_kept"], 1
            )
            self.assertFalse(manifest["source"]["bundled"])
            self.assertFalse(manifest["source"]["retrieved_by_builder"])
            self.assertFalse(manifest["source"]["matches_official_pinned_asset"])
            self.assertTrue(manifest["server_only"])
            self.assertFalse(manifest["client_download"])
            self.assertEqual(manifest["license_spdx"], LICENSE_SPDX)

    def test_outputs_artifact_notice_and_path_free_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "sensitive-local-name.zip"
            output = root / "out"
            _make_zip(source)
            manifest = _build_fixture(source, output)

            self.assertEqual(
                {path.name for path in output.iterdir()},
                {ARTIFACT_NAME, "NOTICE.md", "manifest.json"},
            )
            manifest_text = (output / "manifest.json").read_text(encoding="utf-8")
            self.assertNotIn(str(source), manifest_text)
            self.assertNotIn(source.name, manifest_text)
            self.assertEqual(json.loads(manifest_text), manifest)
            artifact = manifest["artifact"]
            artifact_path = output / artifact["file"]
            self.assertEqual(artifact["bytes"], artifact_path.stat().st_size)
            self.assertEqual(artifact["sha256"], _sha256(artifact_path))
            notice = (output / "NOTICE.md").read_text(encoding="utf-8")
            self.assertIn("CC BY-SA 4.0", notice)
            self.assertIn("basewrd26.txt", notice)
            self.assertIn("does not offer it as a client download", notice)

    def test_artifact_is_independent_of_zip_order_paths_and_timestamps(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_zip = root / "first.zip"
            second_zip = root / "second.zip"
            _make_zip(first_zip, timestamp_year=2020)
            _make_zip(second_zip, reverse=True, timestamp_year=2024)
            first_output = root / "first"
            second_output = root / "second"
            _build_fixture(first_zip, first_output)
            _build_fixture(second_zip, second_output)

            self.assertEqual(
                (first_output / ARTIFACT_NAME).read_bytes(),
                (second_output / ARTIFACT_NAME).read_bytes(),
            )
            self.assertEqual(
                (first_output / "NOTICE.md").read_bytes(),
                (second_output / "NOTICE.md").read_bytes(),
            )

    def test_source_identity_mismatch_stops_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            with self.assertRaisesRegex(ValueError, "Source SHA-256 mismatch"):
                build_nation_bnc_coca_index(
                    source,
                    output,
                    expected_source_sha256="0" * 64,
                    expected_source_bytes=source.stat().st_size,
                )
            self.assertFalse(output.exists())

    def test_requires_every_selected_band(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "missing.zip"
            members = _contents()
            members.pop("range-data/basewrd17.txt")
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "missing required basewrd bands: 17"):
                _build_fixture(source, root / "out")

    def test_rejects_duplicate_selected_basename(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "duplicates.zip"
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                with zipfile.ZipFile(
                    source, "w", compression=zipfile.ZIP_DEFLATED
                ) as archive:
                    for name, payload in _contents().items():
                        archive.writestr(name, payload)
                    archive.writestr("another/basewrd1.txt", b"OTHER 0\n")
            with self.assertRaisesRegex(ValueError, "duplicate basewrd list for band 1"):
                _build_fixture(source, root / "out")

    def test_rejects_a_member_before_its_family_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "member-first.zip"
            members = _contents()
            members["range-data/basewrd7.txt"] = b"\tORPHAN 0\nHEAD 0\n"
            with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name, payload in members.items():
                    archive.writestr(name, payload)
            with self.assertRaisesRegex(ValueError, "starts a member before a family head"):
                _build_fixture(source, root / "out")

    def test_runtime_loader_returns_panel_b_rank_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)

            with self.assertRaisesRegex(ValueError, "pinned official source"):
                load_nation_bnc_coca_index(output / ARTIFACT_NAME)

            rank, meta = load_nation_bnc_coca_index(
                output / ARTIFACT_NAME,
                _allow_unpinned_source_for_tests=True,
            )

            self.assertEqual(
                rank["acceptance"], {"head": "accept", "rank": 1, "level": 1}
            )
            self.assertEqual(rank["betas"]["head"], "beta")
            self.assertEqual(rank["betas"]["rank"], 1001)
            self.assertEqual(meta["lookup_unit"], "word_family")
            self.assertEqual(meta["n_levels"], 25)
            self.assertGreater(meta["variants"], 0)

    def test_runtime_loader_rejects_artifact_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)
            artifact = output / ARTIFACT_NAME
            artifact.write_bytes(artifact.read_bytes() + b"tampered")

            with self.assertRaisesRegex(ValueError, "size does not match"):
                load_nation_bnc_coca_index(
                    artifact,
                    _allow_unpinned_source_for_tests=True,
                )

    def test_runtime_loader_rejects_manifest_digest_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)
            manifest_path = output / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["artifact"]["sha256"] = "0" * 64
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "artifact SHA-256 mismatch"):
                load_nation_bnc_coca_index(
                    output / ARTIFACT_NAME,
                    _allow_unpinned_source_for_tests=True,
                )

    def test_runtime_loader_rejects_joint_artifact_and_manifest_replacement(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)
            artifact_path = output / ARTIFACT_NAME
            manifest_path = output / "manifest.json"

            artifact_path.write_bytes(artifact_path.read_bytes() + b"replacement")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["source"].update({
                "asset": SOURCE_ASSET,
                "url": SOURCE_URL,
                "bytes": SOURCE_BYTES,
                "sha256": SOURCE_SHA256,
                "expected_bytes": SOURCE_BYTES,
                "expected_sha256": SOURCE_SHA256,
                "matches_official_pinned_asset": True,
                "checksum_check": "matched-pinned-size-and-sha256",
            })
            manifest["artifact"].update({
                "bytes": artifact_path.stat().st_size,
                "sha256": _sha256(artifact_path),
            })
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "pinned production artifact"):
                load_nation_bnc_coca_index(artifact_path)

    def test_runtime_loader_requires_adjacent_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)
            (output / "manifest.json").unlink()

            with self.assertRaisesRegex(FileNotFoundError, "manifest does not exist"):
                load_nation_bnc_coca_index(
                    output / ARTIFACT_NAME,
                    _allow_unpinned_source_for_tests=True,
                )

    def test_verified_runtime_loader_checks_manifest_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "fixture.zip"
            output = root / "out"
            _make_zip(source)
            _build_fixture(source, output)

            rank, _meta = load_verified_nation_bnc_coca_index(
                output / ARTIFACT_NAME,
                require_official_source=False,
            )
            self.assertEqual(rank["acceptance"]["head"], "accept")

            with self.assertRaisesRegex(ValueError, "pinned official source"):
                load_verified_nation_bnc_coca_index(output / ARTIFACT_NAME)

            with (output / ARTIFACT_NAME).open("ab") as artifact:
                artifact.write(b"tampered")
            with self.assertRaisesRegex(ValueError, "artifact SHA-256 mismatch"):
                load_verified_nation_bnc_coca_index(
                    output / ARTIFACT_NAME,
                    require_official_source=False,
                )


if __name__ == "__main__":
    unittest.main()
