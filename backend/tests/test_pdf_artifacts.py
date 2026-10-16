"""Regression tests: merchant extraction, PDF artifact persistence, cleanup.

Uses synthetic in-memory PDFs (deterministic fixtures) and mocked S3 clients.
No real receipts and no live AWS access in the automated suite.
"""
import io
import json
from unittest.mock import MagicMock, patch

import fitz

from app.extractors import extract_from_text

from app.extractors import extract_from_text


def make_text_pdf(lines, pages=1):
    """Build a deterministic synthetic text PDF in memory."""
    doc = fitz.open()
    for _ in range(pages):
        page = doc.new_page()
        y = 72
        for line in lines:
            page.insert_text((72, y), line, fontsize=11)
            y += 18
    data = doc.tobytes()
    doc.close()
    return data


def login_headers(client, email="riya@example.com"):
    r = client.post("/api/auth/login", json={"email": email})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def fetch_receipt(rid):
    import app.db as db_config
    from app.models import Receipt
    s = db_config.SessionLocal()
    rec = s.get(Receipt, rid)
    s.expunge(rec)
    s.close()
    return rec


def reset_storage_singleton():
    import app.db as db_config
    db_config._storage_instance = None


def fake_s3_client():
    """Real SigV4-signing client with fake credentials; makes no network calls."""
    import boto3
    return boto3.client(
        "s3", region_name="ap-southeast-2",
        aws_access_key_id="testing", aws_secret_access_key="testing",
        config=boto3.session.Config(signature_version="s3v4"))


# --- merchant extraction regression (synthetic fixtures, known fields) ------

class TestMerchantExtraction:
    def test_merchant_skips_date_and_time_lines(self):
        r = extract_from_text(
            "Aug 4, 2026\n12:48 AM\nThanks for riding, Dhruv\nWe hope you enjoyed your ride.\nTotal\n124.96")
        assert r.merchant == "Thanks for riding, Dhruv"
        assert str(r.date) == "2026-08-04"
        assert float(r.amount) == 124.96

    def test_merchant_normal_receipt_unchanged(self):
        r = extract_from_text("UBER\n12 Aug 2026\nTrip to Airport\n320")
        assert r.merchant == "UBER"
        assert str(r.date) == "2026-08-12"
        assert float(r.amount) == 320.0

    def test_merchant_named_receipt(self):
        r = extract_from_text("CAFE COFFEE DAY\n10 Oct 2026\nCoffee\n500")
        assert r.merchant == "CAFE COFFEE DAY"

    def test_merchant_fallback_all_noise(self):
        r = extract_from_text("Aug 4, 2026\n12:48 AM\n124.96")
        assert r.merchant == "Aug 4, 2026"

    def test_time_only_line_never_merchant(self):
        r = extract_from_text("12:48 AM\nHOTEL PLAZA\nRoom charge\n2500")
        assert "AM" not in r.merchant


# --- PDF artifacts (local backend) -------------------------------------------

class TestPdfArtifactsLocal:
    def test_pdf_upload_persists_artifacts(self, seeded):
        data = make_text_pdf(["UBER", "12 Aug 2026", "Trip to Airport", "320"])
        files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
        r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
        assert r.status_code == 200, r.text
        rid = r.json()["receipt_id"]

        import app.db as db_config
        from app.models import Receipt
        from app.storage import get_storage

        rec = fetch_receipt(rid)
        original_key = rec.s3_image_key or rec.image_path

        storage = get_storage()
        assert storage.get_object(original_key) == data  # original preserved byte-for-byte
        assert rec.s3_pages_keys == [f"receipts/{rid}/pages/page-001.jpg"]
        assert rec.s3_source_text_key.endswith("ocr/source.txt")
        assert rec.s3_extracted_key.endswith("extracted/receipt.json")
        parsed = json.loads(storage.get_object(rec.s3_extracted_key))
        assert parsed["merchant"] == "UBER"
        assert b"UBER" in storage.get_object(rec.s3_source_text_key)

    def test_multipage_pdf_order_preserved(self, seeded):
        data = make_text_pdf(["PAGE TEXT"], pages=3)
        files = {"file": ("multi.pdf", io.BytesIO(data), "application/pdf")}
        r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
        assert r.status_code == 200
        rid = r.json()["receipt_id"]

        import app.db as db_config
        from app.models import Receipt
        rec = fetch_receipt(rid)
        assert list(rec.s3_pages_keys) == [f"receipts/{rid}/pages/page-{i:03d}.jpg" for i in (1, 2, 3)]

    def test_pages_endpoint_authorized_only(self, seeded):
        data = make_text_pdf(["UBER", "12 Aug 2026", "Trip", "320"])
        files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
        r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
        assert r.status_code == 200
        rid = r.json()["receipt_id"]

        owner = login_headers(seeded, "riya@example.com")
        other = login_headers(seeded, "neha@example.com")

        pages = seeded.get(f"/api/receipts/pages/{rid}", headers=owner)
        assert pages.status_code == 200
        listing = pages.json()["pages"]
        assert listing[0]["page"] == 1
        assert listing[0]["url"] == f"/api/receipts/pages/{rid}/1"

        denied = seeded.get(f"/api/receipts/pages/{rid}", headers=other)
        assert denied.status_code == 403

        denied_image = seeded.get(f"/api/receipts/image/{rid}", headers=other)
        assert denied_image.status_code == 403

        # page image bytes served after authorization
        content = seeded.get(f"/api/receipts/pages/{rid}/1", headers=owner)
        assert content.status_code == 200
        assert content.headers["content-type"].startswith("image/jpeg")
        denied_page = seeded.get(f"/api/receipts/pages/{rid}/1", headers=other)
        assert denied_page.status_code == 403
        not_found = seeded.get(f"/api/receipts/pages/{rid}/9", headers=owner)
        assert not_found.status_code == 404

    def test_original_pdf_bytes_preserved(self, seeded):
        data = make_text_pdf(["UBER", "12 Aug 2026", "Trip", "320"])
        files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
        r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
        rid = r.json()["receipt_id"]

        import app.db as db_config
        from app.models import Receipt
        from app.storage import get_storage
        rec = fetch_receipt(rid)
        original_key = rec.s3_image_key or rec.image_path
        assert get_storage().get_object(original_key) == data


# --- PDF artifacts (mocked S3, no real AWS) -----------------------------------

class TestPdfArtifactsS3Mocked:
    def _enable_s3(self, monkeypatch):
        import app.db as db_config
        monkeypatch.setattr(db_config, "STORAGE_BACKEND", "s3")
        monkeypatch.setattr(db_config, "S3_BUCKET_NAME", "test-bucket")
        monkeypatch.setattr(db_config, "AWS_REGION", "ap-southeast-2")
        monkeypatch.setattr(db_config, "S3_KEY_PREFIX", "receipts")
        db_config._storage_instance = None

    def _teardown(self):
        import app.db as db_config
        db_config._storage_instance = None

    def test_s3_artifact_keys_persisted(self, seeded, monkeypatch):
        import app.db as db_config
        self._enable_s3(monkeypatch)
        mock_s3 = MagicMock()  # records calls, no network
        try:
            with patch("boto3.client", return_value=mock_s3):
                data = make_text_pdf(["UBER", "12 Aug 2026", "Trip", "320"])
                files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
                r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
                assert r.status_code == 200, r.text
                rid = r.json()["receipt_id"]

                rec = fetch_receipt(rid)
                assert rec.s3_image_key == f"receipts/{rid}/original.pdf"
                assert rec.s3_pages_keys == [f"receipts/{rid}/pages/page-001.jpg"]
                assert rec.s3_source_text_key == f"receipts/{rid}/ocr/source.txt"
                assert rec.s3_extracted_key.endswith("extracted/receipt.json")

                put_keys = sorted(c.kwargs["Key"] for c in mock_s3.put_object.call_args_list)
                assert put_keys == [
                    f"receipts/{rid}/extracted/receipt.json",
                    f"receipts/{rid}/ocr/source.txt",
                    f"receipts/{rid}/original.pdf",
                    f"receipts/{rid}/pages/page-001.jpg",
                ]
        finally:
            db_config._storage_instance = None

    def test_presigned_pages_expiry(self, seeded, monkeypatch):
        import app.db as db_config
        self._enable_s3(monkeypatch)
        # MagicMock client for uploads (no network); presigned URLs generated by
        # a real SigV4-signing client with fake credentials (offline).
        mock_s3 = MagicMock()
        mock_s3.generate_presigned_url = fake_s3_client().generate_presigned_url
        try:
            with patch("boto3.client", return_value=mock_s3):
                from urllib.parse import parse_qs, urlparse
                from app.storage import get_storage

                data = make_text_pdf(["UBER", "12 Aug 2026", "Trip", "320"])
                files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
                r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
                assert r.status_code == 200
                rid = r.json()["receipt_id"]

                rec = fetch_receipt(rid)
                page_keys = list(rec.s3_pages_keys or [])
                assert page_keys

                url = get_storage().get_download_url(page_keys[0], db_config.PRESIGNED_URL_EXPIRES)
                assert parse_qs(urlparse(url).query).get("X-Amz-Expires") == ["300"]
        finally:
            db_config._storage_instance = None

    def test_partial_failure_cleans_up_and_reports_failure(self, seeded, monkeypatch):
        """Second artifact upload fails -> 422, no DB row, first upload deleted."""
        import app.db as db_config
        from app.models import Receipt
        self._enable_s3(monkeypatch)
        mock_s3 = MagicMock()
        mock_s3.put_object.side_effect = None

        def put_side_effect(**kwargs):
            if "original.pdf" in kwargs.get("Key", ""):
                return MagicMock()  # original upload succeeds
            raise RuntimeError("simulated S3 outage")

        mock_s3.put_object.side_effect = put_side_effect

        try:
            with patch("boto3.client", return_value=mock_s3):
                s = db_config.SessionLocal()
                baseline_count = s.query(Receipt).count()
                s.close()

                data = make_text_pdf(["UBER", "12 Aug 2026", "Trip", "320"])
                files = {"file": ("receipt.pdf", io.BytesIO(data), "application/pdf")}
                r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
                assert r.status_code == 422
                assert r.status_code == 422
                assert "storage failed" in r.json()["detail"].lower()

                # no orphaned DB record beyond the pre-existing seed data
                s = db_config.SessionLocal()
                count = s.query(Receipt).count()
                s.close()
                assert count == baseline_count

                # cleanup deleted the original that succeeded before the failure
                deleted = [c.kwargs["Key"] for c in mock_s3.delete_object.call_args_list]
                assert any("original.pdf" in k for k in deleted)
        finally:
            self._teardown()
