import io
import unittest
import zipfile
from unittest.mock import patch

from ldfreq.uploads import documents_from_uploads


class FakeUpload:
    def __init__(self, name, data):
        self.name = name
        self._data = data

    def getvalue(self):
        return self._data


def _zip_bytes(entries, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class UploadTests(unittest.TestCase):
    def test_documents_from_uploads_accepts_txt_and_zip_members(self):
        uploads = [
            FakeUpload("plain.txt", b"alpha beta"),
            FakeUpload(
                "batch.zip",
                _zip_bytes({
                    "one.txt": b"gamma",
                    "nested/two.txt": b"delta",
                    "ignored.csv": b"epsilon",
                }),
            ),
        ]

        docs, warnings = documents_from_uploads(
            uploads,
            max_file_bytes=100,
            max_total_bytes=300,
            max_documents=10,
        )

        self.assertEqual(warnings, [])
        self.assertEqual([doc["name"] for doc in docs], [
            "plain.txt",
            "batch.zip:one.txt",
            "batch.zip:nested/two.txt",
        ])
        self.assertEqual([doc["source"] for doc in docs], ["upload", "zip", "zip"])

    def test_documents_from_uploads_enforces_limits_and_reports_warnings(self):
        uploads = [
            FakeUpload("too-big.txt", b"abcdef"),
            FakeUpload("ok.txt", b"abc"),
            FakeUpload("overflow.txt", b"def"),
            FakeUpload("bad.zip", b"not a zip"),
        ]

        docs, warnings = documents_from_uploads(
            uploads,
            max_file_bytes=5,
            max_total_bytes=5,
            max_documents=10,
        )

        self.assertEqual([doc["name"] for doc in docs], ["ok.txt"])
        self.assertTrue(any("too-big.txt" in warning for warning in warnings))
        self.assertTrue(any("total extracted text limit" in warning for warning in warnings))
        self.assertTrue(any("invalid ZIP" in warning for warning in warnings))

    def test_documents_from_uploads_enforces_document_limit(self):
        upload = FakeUpload(
            "batch.zip",
            _zip_bytes({"a.txt": b"a", "b.txt": b"b"}),
        )

        docs, warnings = documents_from_uploads(
            [upload],
            max_file_bytes=10,
            max_total_bytes=10,
            max_documents=1,
        )

        self.assertEqual([doc["name"] for doc in docs], ["batch.zip:a.txt"])
        self.assertTrue(any("document limit" in warning for warning in warnings))

    def test_redacted_warnings_do_not_include_source_filenames(self):
        docs, warnings = documents_from_uploads(
            [FakeUpload("student-name.txt", b"abcdef")],
            max_file_bytes=3,
            max_total_bytes=10,
            max_documents=1,
            redact_names=True,
        )

        self.assertEqual(docs, [])
        self.assertTrue(any("Upload 001" in warning for warning in warnings))
        self.assertFalse(any("student-name" in warning for warning in warnings))

    def test_zip_compression_ratio_limit_blocks_highly_compressed_member(self):
        upload = FakeUpload(
            "compressed.zip",
            _zip_bytes(
                {"essay.txt": b"a" * 10_000},
                compression=zipfile.ZIP_DEFLATED,
            ),
        )

        docs, warnings = documents_from_uploads(
            [upload],
            max_file_bytes=20_000,
            max_total_bytes=20_000,
            max_documents=1,
            max_compression_ratio=10,
        )

        self.assertEqual(docs, [])
        self.assertTrue(any("compression ratio" in warning for warning in warnings))

    def test_archive_byte_limit_is_checked_before_expansion(self):
        upload = FakeUpload("batch.zip", _zip_bytes({"essay.txt": b"text"}))

        docs, warnings = documents_from_uploads(
            [upload],
            max_file_bytes=100,
            max_total_bytes=100,
            max_documents=1,
            max_archive_bytes=4,
        )

        self.assertEqual(docs, [])
        self.assertTrue(any("archive size" in warning for warning in warnings))

    def test_document_limit_stops_before_opening_remaining_zip_members(self):
        upload = FakeUpload(
            "student-batch.zip",
            _zip_bytes({"first.txt": b"one", "second.txt": b"two"}),
        )
        opened = []
        original_open = zipfile.ZipFile.open

        def recording_open(archive, member, *args, **kwargs):
            opened.append(member.filename if isinstance(member, zipfile.ZipInfo) else member)
            return original_open(archive, member, *args, **kwargs)

        with patch.object(zipfile.ZipFile, "open", new=recording_open):
            docs, warnings = documents_from_uploads(
                [upload],
                max_file_bytes=10,
                max_total_bytes=10,
                max_documents=1,
                redact_names=True,
            )

        self.assertEqual([doc["text"] for doc in docs], ["one"])
        self.assertEqual(opened, ["first.txt"])
        self.assertTrue(any("remaining ZIP members were not opened" in warning for warning in warnings))
        self.assertFalse(any("student-batch" in warning for warning in warnings))
        self.assertFalse(any("second.txt" in warning for warning in warnings))

    def test_declared_archive_size_is_rejected_before_opening_members(self):
        upload = FakeUpload(
            "student-batch.zip",
            _zip_bytes({"first.txt": b"12345", "second.txt": b"67890"}),
        )

        with patch.object(zipfile.ZipFile, "open") as mocked_open:
            docs, warnings = documents_from_uploads(
                [upload],
                max_file_bytes=10,
                max_total_bytes=8,
                max_documents=10,
                redact_names=True,
            )

        self.assertEqual(docs, [])
        mocked_open.assert_not_called()
        self.assertTrue(any("declared uncompressed text size" in warning for warning in warnings))
        self.assertFalse(any("student-batch" in warning for warning in warnings))
        self.assertFalse(any("first.txt" in warning for warning in warnings))

    def test_cumulative_total_is_checked_before_opening_zip_member(self):
        uploads = [
            FakeUpload("first-student.txt", b"123456"),
            FakeUpload("second-student.zip", _zip_bytes({"essay.txt": b"78901"})),
        ]

        with patch.object(zipfile.ZipFile, "open") as mocked_open:
            docs, warnings = documents_from_uploads(
                uploads,
                max_file_bytes=10,
                max_total_bytes=10,
                max_documents=10,
                redact_names=True,
            )

        self.assertEqual([doc["text"] for doc in docs], ["123456"])
        mocked_open.assert_not_called()
        self.assertTrue(any("member was not opened" in warning for warning in warnings))
        self.assertFalse(any("first-student" in warning for warning in warnings))
        self.assertFalse(any("second-student" in warning for warning in warnings))
        self.assertFalse(any("essay.txt" in warning for warning in warnings))

    def test_archive_member_count_is_capped_before_opening_members(self):
        upload = FakeUpload(
            "student-batch.zip",
            _zip_bytes({"one.txt": b"1", "two.txt": b"2", "three.txt": b"3"}),
        )

        with patch.object(zipfile.ZipFile, "open") as mocked_open:
            docs, warnings = documents_from_uploads(
                [upload],
                max_file_bytes=10,
                max_total_bytes=10,
                max_documents=10,
                max_archive_members=2,
                redact_names=True,
            )

        self.assertEqual(docs, [])
        mocked_open.assert_not_called()
        self.assertTrue(any("ZIP member count" in warning for warning in warnings))
        self.assertFalse(any("student-batch" in warning for warning in warnings))
        self.assertFalse(any("one.txt" in warning for warning in warnings))


if __name__ == "__main__":
    unittest.main()
