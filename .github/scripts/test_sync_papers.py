"""Exercise sync staging, PDF validation, and failure behavior offline."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sync_papers


def pdf(text):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream",
    ]
    data = b"%PDF-1.4\n"
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    start = len(data)
    data += b"xref\n0 6\n0000000000 65535 f \n"
    for offset in offsets[1:]:
        data += f"{offset:010d} 00000 n \n".encode()
    data += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return data


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.old = pdf("Old title")
        self.new = pdf("New title")
        self.destination = self.directory / "paper.pdf"
        self.destination.write_bytes(self.old)
        self.sources = {
            "paper.pdf": {"repo": "owner/repo", "ref": "main", "path": "ms/paper.pdf"}
        }
        self.calls = []
        self.payload = self.new
        self.addCleanup(patch.stopall)
        patch.object(sync_papers, "load_sources", return_value=self.sources).start()
        patch.object(sync_papers, "download", side_effect=self.download).start()

    def download(self, url, api=False):
        self.calls.append(url)
        return json.dumps({"sha": "a" * 40}).encode() if api else self.payload

    def test_dry_run_leaves_pdf_unchanged_and_reports_diff(self):
        report = sync_papers.sync(self.directory)
        self.assertEqual(self.destination.read_bytes(), self.old)
        self.assertIn("-Old title", report)
        self.assertIn("+New title", report)
        self.assertIn("Pages: 1 → 1", report)
        self.assertIn("/" + "a" * 40 + "/ms/paper.pdf", self.calls[1])

    def test_apply_then_rerun_is_idempotent(self):
        sync_papers.sync(self.directory, apply=True)
        self.assertEqual(self.destination.read_bytes(), self.new)
        report = sync_papers.sync(self.directory, apply=True)
        self.assertIn("All mapped PDFs match", report)

    def test_invalid_pdf_does_not_modify_destination(self):
        for payload in (b"<html>Error</html>", b"%PDF-broken"):
            with self.subTest(payload=payload):
                self.payload = payload
                with self.assertRaises(
                    (ValueError, sync_papers.subprocess.CalledProcessError)
                ):
                    sync_papers.sync(self.directory, apply=True)
                self.assertEqual(self.destination.read_bytes(), self.old)

    def test_later_failure_leaves_all_destinations_unchanged(self):
        self.sources["second.pdf"] = {
            "repo": "owner/other",
            "ref": "main",
            "path": "paper.pdf",
        }
        (self.directory / "second.pdf").write_bytes(self.old)
        with patch.object(
            sync_papers,
            "download",
            side_effect=[
                json.dumps({"sha": "a" * 40}).encode(),
                self.new,
                OSError("offline"),
            ],
        ):
            with self.assertRaisesRegex(OSError, "offline"):
                sync_papers.sync(self.directory, apply=True)
        self.assertEqual(self.destination.read_bytes(), self.old)

    def test_ref_is_resolved_once_per_repository_and_encoded(self):
        self.sources["paper.pdf"]["ref"] = "draft/revision"
        self.sources["second.pdf"] = dict(self.sources["paper.pdf"])
        (self.directory / "second.pdf").write_bytes(self.old)
        sync_papers.sync(self.directory)
        self.assertEqual(len([u for u in self.calls if "api.github.com" in u]), 1)
        self.assertIn("draft%2Frevision", self.calls[0])


class DownloadTests(unittest.TestCase):
    def test_oversize_download_is_rejected(self):
        with patch.object(sync_papers, "MAX_BYTES", 4), patch.object(
            sync_papers, "urlopen"
        ) as opened:
            opened.return_value.__enter__.return_value.read.return_value = b"12345"
            with self.assertRaisesRegex(ValueError, "exceeds"):
                sync_papers.download("https://example.org/paper.pdf")


if __name__ == "__main__":
    unittest.main()
