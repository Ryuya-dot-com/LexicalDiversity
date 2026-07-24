import base64
import hashlib
import io
import os
import stat
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from ldfreq import wordlists as WL


class WordlistRegistryTests(unittest.TestCase):
    def test_invalid_base64_secret_is_ignored_with_warning(self):
        WL.MATERIALIZATION_WARNINGS.clear()

        with patch.dict(os.environ, {"LDFREQ_BAD_B64": "not-base64!!"}):
            self.assertIsNone(WL._decode_env_b64("LDFREQ_BAD_B64"))

        self.assertTrue(any("LDFREQ_BAD_B64" in warning for warning in WL.MATERIALIZATION_WARNINGS))
        WL.MATERIALIZATION_WARNINGS.clear()

    def test_materialized_file_updates_when_secret_payload_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime_dir = Path(tmp)
            stale_path = runtime_dir / "list.txt"
            stale_path.write_bytes(b"old")

            payload = base64.b64encode(b"new").decode("ascii")
            with patch.object(WL, "_RUNTIME_DIR", str(runtime_dir)):
                with patch.dict(os.environ, {"LDFREQ_TEST_B64": payload}, clear=False):
                    os.environ.pop("LDFREQ_TEST_PATH", None)
                    WL._materialize_file_b64("LDFREQ_TEST_B64", "list.txt", "LDFREQ_TEST_PATH")

            self.assertEqual(stale_path.read_bytes(), b"new")
            if os.name == "posix":
                self.assertEqual(stat.S_IMODE(stale_path.stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE(runtime_dir.stat().st_mode), 0o700)

    def test_availability_checks_required_files_not_just_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ngsl = root / "ngsl"
            bnc = root / "bnc"
            range_dir = root / "range"
            ngsl.mkdir()
            bnc.mkdir()
            range_dir.mkdir()

            self.assertFalse(WL._ngsl_available(str(ngsl)))
            self.assertFalse(WL._bnc_coca_headwords_available(str(bnc)))
            self.assertFalse(WL._range_available(str(range_dir)))

            (ngsl / "NGSL_1.2_stats.csv").write_text("Lemma,SFI Rank\nword,1\n", encoding="utf-8")
            (bnc / "headwords 1st 1000.txt").write_text("word\n", encoding="utf-8")
            (range_dir / "basewrd1.txt").write_text("word\n", encoding="utf-8")

            self.assertTrue(WL._ngsl_available(str(ngsl)))
            self.assertTrue(WL._bnc_coca_headwords_available(str(bnc)))
            self.assertTrue(WL._range_available(str(range_dir)))

    def test_official_nation_headwords_are_hash_verified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            expected = {}
            for band, name in enumerate(WL.NATION_HEADWORD_FILES, start=1):
                payload = f"word{chr(96 + band)}\n".encode("utf-8")
                (root / name).write_bytes(payload)
                expected[name] = (len(payload), hashlib.sha256(payload).hexdigest())

            with patch.object(WL, "NATION_HEADWORD_FILES", expected):
                rank, meta = WL.load_verified_nation_headwords(str(root))
                self.assertEqual(meta["entries"], 10)
                (root / next(iter(expected))).write_text("tampered\n", encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    WL.load_verified_nation_headwords(str(root))

    def test_invalid_secret_zip_leaves_no_partial_target(self):
        payload = io.BytesIO()
        with zipfile.ZipFile(payload, "w") as archive:
            archive.writestr("safe/list.txt", "safe")
            archive.writestr("../escape.txt", "unsafe")

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            WL, "_RUNTIME_DIR", tmp
        ), patch.dict(
            os.environ,
            {"LDFREQ_TEST_ZIP_B64": base64.b64encode(payload.getvalue()).decode("ascii")},
            clear=False,
        ):
            os.environ.pop("LDFREQ_TEST_ZIP_PATH", None)
            WL._materialize_zip_b64(
                "LDFREQ_TEST_ZIP_B64", "private-target", "LDFREQ_TEST_ZIP_PATH"
            )
            self.assertFalse((Path(tmp) / "private-target").exists())
            self.assertNotIn("LDFREQ_TEST_ZIP_PATH", os.environ)
            WL.MATERIALIZATION_WARNINGS.clear()

    def test_public_rights_gate_hides_permission_pending_resources(self):
        with patch.object(WL, "_entry_available", return_value=True):
            with patch.dict(
                os.environ,
                {
                    "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
                    "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "",
                    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "0",
                },
                clear=False,
            ):
                public_ids = {entry["id"] for entry in WL.available()}

            restricted_ids = {entry["id"] for entry in WL.restricted_installed()}

        self.assertIn("nj8", public_ids)
        self.assertIn("ngsl", public_ids)
        self.assertNotIn("bnc_coca", public_ids)
        self.assertNotIn("bnc_coca_families", public_ids)

        self.assertIn("bnc_coca", restricted_ids)
        self.assertIn("bnc_coca_families", restricted_ids)

    def test_server_only_mode_requires_allowlist_and_rights_attestation(self):
        with patch.object(WL, "_entry_available", return_value=True):
            with patch.dict(
                os.environ,
                {
                    "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
                    "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "bnc_coca,nation_bnc_coca_families",
                    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "0",
                },
                clear=False,
            ):
                without_attestation = {entry["id"] for entry in WL.available()}

            with patch.dict(
                os.environ,
                {
                    "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
                    "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "bnc_coca,nation_bnc_coca_families,bnc_coca_families,range_baseword",
                    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "1",
                },
                clear=False,
            ):
                server_only = {entry["id"] for entry in WL.available()}

        self.assertNotIn("bnc_coca", without_attestation)
        self.assertNotIn("nation_bnc_coca_families", without_attestation)
        self.assertIn("bnc_coca", server_only)
        self.assertIn("nation_bnc_coca_families", server_only)
        self.assertNotIn("bnc_coca_families", server_only)
        self.assertNotIn("range_baseword", server_only)

    def test_targeted_rights_gate_only_checks_the_requested_resource(self):
        nj8 = WL.by_id("nj8")
        with patch.object(WL, "_entry_available", return_value=True) as available:
            selected = WL.available_by_id("nj8")
        self.assertIs(selected, nj8)
        available.assert_called_once_with(nj8)

        with patch.object(WL, "_entry_available", return_value=True) as available, patch.dict(
            os.environ,
            {
                "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "",
                "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "0",
            },
            clear=False,
        ):
            selected = WL.available_by_id("bnc_coca_families")
        self.assertIsNone(selected)
        available.assert_not_called()

    def test_antbnc_is_not_public_server_only_eligible(self):
        with patch.dict(
            os.environ,
            {
                "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "antbnc",
                "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "0",
            },
            clear=False,
        ):
            self.assertFalse(WL.server_only_enabled("antbnc"))

        with patch.dict(
            os.environ,
            {
                "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "antbnc",
                "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "1",
            },
            clear=False,
        ):
            self.assertFalse(WL.server_only_enabled("antbnc"))

    def test_restricted_secrets_are_not_materialized_before_rights_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            encoded = base64.b64encode(b"private payload").decode("ascii")
            with patch.object(WL, "_RUNTIME_DIR", tmp), patch.dict(
                os.environ,
                {
                    "LDFREQ_ALLOW_LOCAL_RESTRICTED": "0",
                    "LDFREQ_SERVER_ONLY_RESOURCE_IDS": "antbnc",
                    "LDFREQ_SERVER_ONLY_RIGHTS_ACKNOWLEDGED": "1",
                    "LDFREQ_ANTBNC_TXT_B64": encoded,
                    "LDFREQ_BNCCOCA_ZIP_B64": encoded,
                    "LDFREQ_NATION_BNCCOCA_RUNTIME_ZIP_B64": encoded,
                },
                clear=True,
            ):
                WL._materialize_deployment_data()

            self.assertFalse((Path(tmp) / "antbnc_lemmas_ver_004.txt").exists())
            self.assertFalse((Path(tmp) / "NationBNCCOCA").exists())
            self.assertFalse((Path(tmp) / "nation_bnc_coca_25000").exists())

    def test_local_override_requires_explicit_local_serving_mode(self):
        encoded = base64.b64encode(b"head -> form").decode("ascii")
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            WL, "_RUNTIME_DIR", tmp
        ), patch.dict(
            os.environ,
            {
                "LDFREQ_SERVING_MODE": "public",
                "LDFREQ_ALLOW_LOCAL_RESTRICTED": "1",
                "LDFREQ_ANTBNC_TXT_B64": encoded,
            },
            clear=True,
        ):
            WL._materialize_deployment_data()
            self.assertFalse((Path(tmp) / "antbnc_lemmas_ver_004.txt").exists())

        with tempfile.TemporaryDirectory() as tmp, patch.object(
            WL, "_RUNTIME_DIR", tmp
        ), patch.dict(
            os.environ,
            {
                "LDFREQ_SERVING_MODE": "local",
                "LDFREQ_ALLOW_LOCAL_RESTRICTED": "1",
                "LDFREQ_ANTBNC_TXT_B64": encoded,
            },
            clear=True,
        ):
            WL._materialize_deployment_data()
            self.assertTrue((Path(tmp) / "antbnc_lemmas_ver_004.txt").is_file())


if __name__ == "__main__":
    unittest.main()
