import copy
import csv
import hashlib
import io
import json
import stat
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from unittest import mock

from scripts.fetch_ellipse import (
    download_pinned_archive,
    EllipseVerificationError,
    load_manifest,
    provision_verified,
    verify_source,
)


CANARY = "PRIVATE-ELLIPSE-CANARY-7cf539"
TEST_MEMBER = "ELLIPSE_Final_github_test.csv"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _csv_payload(
    columns: list[str],
    *,
    identifier: str,
    split: str,
    text: str,
    prompt: str,
) -> bytes:
    values = {
        "text_id_kaggle": identifier,
        "full_text": text,
        "gender": "Female",
        "grade": "10",
        "race_ethnicity": "Asian/Pacific Islander",
        "num_words": "8",
        "num_words2": "8",
        "num_words3": "8",
        "num_sent": "1",
        "num_para": "1",
        "num_word_div_para": "8",
        "MTLD": "20.0",
        "TTR": "50.0",
        "Type": "8",
        "Token": "8",
        "task": "Independent",
        "SES": "Not economically disadvantaged",
        "prompt": prompt,
        "Overall": "3.0",
        "Cohesion": "3.0",
        "Syntax": "3.0",
        "Vocabulary": "3.0",
        "Phraseology": "3.0",
        "Grammar": "3.0",
        "Conventions": "3.0",
        "set": split,
    }
    text_output = io.StringIO(newline="")
    writer = csv.writer(text_output, lineterminator="\n")
    writer.writerow(columns)
    writer.writerow([values.get(column, "fixture") for column in columns])
    return text_output.getvalue().encode("utf-8")


def _fixture_archive(
    root: Path,
    *,
    train_columns: list[str] | None = None,
    outer_extra: tuple[str, bytes | zipfile.ZipInfo] | None = None,
    nested_extra: tuple[str, bytes] | None = None,
) -> tuple[Path, dict[str, object]]:
    manifest = copy.deepcopy(load_manifest())
    columns = list(manifest["final_csv_contract"]["columns_in_order"])
    train_header = train_columns or columns
    train = _csv_payload(
        train_header,
        identifier=f"train-{CANARY}",
        split="train",
        text=f"A private train essay {CANARY}.",
        prompt=f"Private prompt {CANARY}",
    )
    test = _csv_payload(
        columns,
        identifier=f"test-{CANARY}",
        split="test",
        text=f"A different private test essay {CANARY}.",
        prompt=f"Private prompt {CANARY}",
    )

    nested_buffer = io.BytesIO()
    with zipfile.ZipFile(nested_buffer, "w", compression=zipfile.ZIP_DEFLATED) as nested:
        nested.writestr(TEST_MEMBER, test)
        if nested_extra is not None:
            nested.writestr(*nested_extra)
    nested_bytes = nested_buffer.getvalue()

    fixed_members = {
        "ELLIPSE_Final_github_train.csv": train,
        "ELLIPSE_Final_github_test.zip": nested_bytes,
        "ELL_Rubrics.docx": b"PK fixture rubric",
        "README.md": b"Fixture README with no password value",
        "ellipsis_raw_rater_scores_anon_all_essay.zip": b"PK opaque raw fixture",
    }
    manifest_members = {
        item["relative_path"]: item for item in manifest["members"]
    }
    for name, payload in fixed_members.items():
        manifest_members[name]["bytes"] = len(payload)
        manifest_members[name]["sha256"] = _sha256(payload)

    manifest["nested_test_archive"]["encrypted_member_required"] = False
    manifest["nested_test_archive"]["member"]["bytes"] = len(test)
    manifest["nested_test_archive"]["member"]["sha256"] = _sha256(test)
    manifest["nested_test_archive"]["maximum_compression_ratio"] = 100.0
    contract = manifest["final_csv_contract"]
    contract["splits"] = {
        "train": {
            "rows": 1,
            "prompts": 1,
            "set_value": "train",
            "allowed_missing_cells": {},
        },
        "test": {
            "rows": 1,
            "prompts": 1,
            "set_value": "test",
            "allowed_missing_cells": {},
        },
    }
    contract["combined"] = {
        "rows": 2,
        "columns": 26,
        "prompts": 1,
        "unique_ids": 2,
        "id_overlap_between_splits": 0,
        "exact_text_duplicates": 0,
        "prompt_sets_identical_between_splits": True,
    }

    archive_contract = manifest["outer_archive"]
    archive_contract["maximum_members"] = 8
    archive_contract["maximum_total_uncompressed_bytes"] = 1024 * 1024
    archive_contract["maximum_member_bytes"] = 1024 * 1024
    archive_contract["maximum_compression_ratio"] = 100.0
    archive_root = "ELLIPSE-fixture/"
    source = root / "fixture.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(source, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(archive_root, b"")
            for name, payload in fixed_members.items():
                archive.writestr(f"{archive_root}{name}", payload)
            if outer_extra is not None:
                name, payload_or_info = outer_extra
                if isinstance(payload_or_info, zipfile.ZipInfo):
                    archive.writestr(payload_or_info, b"target")
                else:
                    archive.writestr(name, payload_or_info)

    source_bytes = source.read_bytes()
    archive_contract["accepted_variants"] = [
        {
            "id": "unit-fixture",
            "root": archive_root,
            "bytes": len(source_bytes),
            "sha256": _sha256(source_bytes),
            "provenance": "unit test only",
        }
    ]
    return source, manifest


class EllipseFetchTests(unittest.TestCase):
    def test_download_step_hashes_opaque_archive_without_content_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(root)
            source_bytes = source.read_bytes()
            manifest["outer_archive"]["accepted_variants"][0]["id"] = (
                "github-pinned-commit"
            )
            manifest["outer_archive"]["pinned_download_url"] = (
                "https://example.invalid/pinned.zip"
            )

            response = io.BytesIO(source_bytes)
            response.headers = {"Content-Length": str(len(source_bytes))}
            destination = root / "downloaded.zip"
            with mock.patch(
                "scripts.fetch_ellipse.urllib.request.urlopen",
                return_value=response,
            ), mock.patch(
                "scripts.fetch_ellipse.zipfile.ZipFile",
                side_effect=AssertionError("download stage must not open ZIP content"),
            ):
                summary = download_pinned_archive(
                    destination,
                    manifest=manifest,
                )

            self.assertEqual(destination.read_bytes(), source_bytes)
            self.assertFalse(summary["content_opened"])
            self.assertNotIn(CANARY, json.dumps(summary, sort_keys=True))

    def test_download_hash_failure_leaves_no_destination(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(root)
            source_bytes = source.read_bytes()
            variant = manifest["outer_archive"]["accepted_variants"][0]
            variant["id"] = "github-pinned-commit"
            variant["sha256"] = "0" * 64
            manifest["outer_archive"]["pinned_download_url"] = (
                "https://example.invalid/pinned.zip"
            )
            response = io.BytesIO(source_bytes)
            response.headers = {"Content-Length": str(len(source_bytes))}
            destination = root / "must-not-exist.zip"

            with mock.patch(
                "scripts.fetch_ellipse.urllib.request.urlopen",
                return_value=response,
            ):
                with self.assertRaisesRegex(
                    EllipseVerificationError,
                    "SHA-256 is not reviewed",
                ):
                    download_pinned_archive(destination, manifest=manifest)
            self.assertFalse(destination.exists())

    def test_verifies_exact_fixture_without_exposing_row_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, manifest = _fixture_archive(Path(temporary))
            verified = verify_source(
                source,
                manifest=manifest,
                test_password=b"fixture-password",
            )

            serialized = json.dumps(verified.summary, sort_keys=True)
            self.assertNotIn(CANARY, serialized)
            self.assertNotIn(CANARY, repr(verified))
            self.assertEqual(verified.summary["data_contract"]["combined"]["rows"], 2)
            self.assertFalse(
                verified.summary["privacy"]["raw_rater_archive_decrypted"]
            )

    def test_wrong_outer_hash_stops_before_zip_is_opened(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, manifest = _fixture_archive(Path(temporary))
            manifest["outer_archive"]["accepted_variants"][0]["sha256"] = "0" * 64
            with mock.patch("scripts.fetch_ellipse.zipfile.ZipFile") as zip_file:
                with self.assertRaisesRegex(
                    EllipseVerificationError,
                    "SHA-256 is not reviewed",
                ):
                    verify_source(
                        source,
                        manifest=manifest,
                        test_password=b"fixture-password",
                    )
            zip_file.assert_not_called()

    def test_rejects_traversal_before_inventory_comparison(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, manifest = _fixture_archive(
                Path(temporary),
                outer_extra=("../escape.csv", b"private"),
            )
            with self.assertRaisesRegex(EllipseVerificationError, "unsafe member path"):
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )

    def test_rejects_unexpected_outer_and_nested_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(
                root,
                outer_extra=("ELLIPSE-fixture/.DS_Store", b"private"),
            )
            with self.assertRaisesRegex(
                EllipseVerificationError,
                "member count|inventory differs",
            ):
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )

            source, manifest = _fixture_archive(
                root,
                nested_extra=("unexpected.csv", b"private"),
            )
            with self.assertRaisesRegex(
                EllipseVerificationError,
                "member count|inventory differs",
            ):
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )

    def test_rejects_casefold_duplicate_and_symbolic_link(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(
                root,
                outer_extra=("ELLIPSE-fixture/README.MD", b"duplicate"),
            )
            with self.assertRaisesRegex(EllipseVerificationError, "duplicate member"):
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )

            link = zipfile.ZipInfo("ELLIPSE-fixture/link")
            link.create_system = 3
            link.external_attr = (stat.S_IFLNK | 0o777) << 16
            source, manifest = _fixture_archive(
                root,
                outer_extra=(link.filename, link),
            )
            with self.assertRaisesRegex(EllipseVerificationError, "symbolic-link"):
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )

    def test_schema_drift_fails_without_echoing_private_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            manifest_template = load_manifest()
            columns = list(
                manifest_template["final_csv_contract"]["columns_in_order"]
            )
            columns[0] = f"renamed-{CANARY}"
            source, manifest = _fixture_archive(
                Path(temporary),
                train_columns=columns,
            )
            with self.assertRaises(EllipseVerificationError) as caught:
                verify_source(
                    source,
                    manifest=manifest,
                    test_password=b"fixture-password",
                )
            self.assertIn("header differs", str(caught.exception))
            self.assertNotIn(CANARY, str(caught.exception))
            self.assertNotIn(source.name, str(caught.exception))

    def test_provisions_only_private_final_data_and_aggregate_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(root)
            verified = verify_source(
                source,
                manifest=manifest,
                test_password=b"fixture-password",
            )
            output = root / "private-output"
            provision_verified(verified, output, manifest=manifest)

            self.assertEqual(
                {path.name for path in output.iterdir()},
                {
                    "ELLIPSE_Final_github_train.csv",
                    TEST_MEMBER,
                    "verification.json",
                },
            )
            verification = (output / "verification.json").read_text(encoding="utf-8")
            self.assertNotIn(CANARY, verification)
            self.assertNotIn("raw_rater_scores", verification)
            self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o700)
            for name in ("ELLIPSE_Final_github_train.csv", TEST_MEMBER, "verification.json"):
                self.assertEqual(stat.S_IMODE((output / name).stat().st_mode), 0o600)

    def test_never_overwrites_existing_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, manifest = _fixture_archive(root)
            verified = verify_source(
                source,
                manifest=manifest,
                test_password=b"fixture-password",
            )
            output = root / "existing"
            output.mkdir()
            sentinel = output / "sentinel.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            with self.assertRaisesRegex(EllipseVerificationError, "already exists"):
                provision_verified(verified, output, manifest=manifest)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")

    def test_rejects_corpus_provisioning_into_public_metadata_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, manifest = _fixture_archive(Path(temporary))
            verified = verify_source(
                source,
                manifest=manifest,
                test_password=b"fixture-password",
            )
            public_destination = PROJECT_ROOT / "benchmarks" / "ellipse" / "payload"

            with self.assertRaisesRegex(
                EllipseVerificationError,
                "allowed only under .research or data/raw",
            ):
                provision_verified(verified, public_destination, manifest=manifest)
            self.assertFalse(public_destination.exists())


if __name__ == "__main__":
    unittest.main()
