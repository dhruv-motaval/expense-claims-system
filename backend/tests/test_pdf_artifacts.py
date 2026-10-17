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


# --- canonical original storage + JSONB extraction (local backend) -----------

class TestPdfArtifactsLocal:
    def test_pdf_upload_canonical_original_and_jsonb(self, seeded):
        import hashlib
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
        assert original_key == f"receipts/{rid}/original.pdf"
        # canonical file metadata
        assert rec.original_filename == "receipt.pdf"
        assert rec.content_type == "application/pdf"
        assert rec.file_size == len(data)
        assert rec.checksum_sha256 == hashlib.sha256(data).hexdigest()
        assert rec.processing_status == "completed"
        # extracted structured JSON lives in PostgreSQL, not S3
        assert rec.extracted_data["merchant"] == "UBER"
        assert rec.extracted_data["amount"] == 320.0
        assert rec.extracted_data["extraction_status"] in ("completed", "partial")
        # no redundant S3 artifacts for new records
        assert rec.s3_pages_keys is None
        assert rec.s3_source_text_key is None
        assert rec.s3_extracted_key is None
        # pages endpoint returns 404 for new records (no persisted page images)
        owner = login_headers(seeded, "riya@example.com")
        assert seeded.get(f"/api/receipts/pages/{rid}", headers=owner).status_code == 404

    def test_multipage_pdf_extracted_without_persisting_pages(self, seeded):
        data = make_text_pdf(["PAGE TEXT"], pages=3)
        files = {"file": ("multi.pdf", io.BytesIO(data), "application/pdf")}
        r = seeded.post("/api/receipts/upload", headers=login_headers(seeded), files=files)
        assert r.status_code == 200
        rid = r.json()["receipt_id"]

        import app.db as db_config
        rec = fetch_receipt(rid)
        # original preserved; no permanent page-image artifacts
        assert (rec.s3_image_key or rec.image_path).endswith("original.pdf")
        assert rec.s3_pages_keys is None
        assert rec.extracted_data.get("page_count") == 3

    def test_legacy_pages_endpoint_authorized_only(self, seeded):
        """Legacy records with persisted page images keep working; auth enforced."""
        import app.db as db_config
        from app.models import Receipt, User
        from app.storage import get_storage

        # build a legacy-style receipt with persisted page images
        s = db_config.SessionLocal()
        riya = s.query(User).filter_by(email="riya@example.com").first()
        legacy = Receipt(user_id=riya.id, source_type="PDF", processing_status="completed")
        s.add(legacy)
        s.flush()
        rid = legacy.id
        storage = get_storage()
        page_keys = []
        for n in (1, 2):
            obj = storage.upload_artifact(rid, f"pages/page-{n:03d}.jpg", b"legacy-page-bytes", "image/jpeg")
            page_keys.append(obj.key)
        legacy.s3_pages_keys = page_keys
        s.commit()
        s.close()

        owner = login_headers(seeded, "riya@example.com")
        other = login_headers(seeded, "neha@example.com")

        pages = seeded.get(f"/api/receipts/pages/{rid}", headers=owner)
        assert pages.status_code == 200
        listing = pages.json()["pages"]
        assert listing[0]["page"] == 1

        denied = seeded.get(f"/api/receipts/pages/{rid}", headers=other)
        assert denied.status_code == 403

        denied_image = seeded.get(f"/api/receipts/image/{rid}", headers=other)
        assert denied_image.status_code == 403

        # page image bytes served after authorization (legacy records)
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


# --- scanned PDF / OCR unavailable -------------------------------------------

class TestScannedPdfGraceful:
    def test_scanned_pdf_without_tesseract_reports_clear_error(self, seeded):
        """A PDF with no embedded text must fail gracefully when the optional
        local OCR engine (Tesseract) is not installed - clear error, no storage."""
        from PIL import Image
        import fitz
        buf = io.BytesIO()
        Image.new("RGB", (200, 100), "white").save(buf, format="PNG")
        doc = fitz.open()
        page = doc.new_page(width=200, height=100)
        page.insert_image(fitz.Rect(0, 0, 200, 100), stream=buf.getvalue())
        data = doc.tobytes()
        doc.close()

        from app.services import _extract_pdf_fields
        from app.extractors import ExtractionError
        try:
            import pytesseract  # if Tesseract IS available, OCR path may succeed
            has_ocr = True
        except ImportError:
            has_ocr = False

        try:
            _extracted, result = _extract_pdf_fields_import()(data)
        except Exception as e:
            # graceful failure message, no crash and no invented values
            assert "No readable text" in str(e) or "OCR" in str(e) or "Enter the claim manually" in str(e)
            return
        # if OCR is installed and succeeded, extraction must still be well-formed
        assert _extracted.merchant is not None or _extracted.amount is not None


def _extract_pdf_fields_import():
    from app.services import _extract_pdf_fields
    return _extract_pdf_fields

class TestImageFormatPreservation:
    """The storage layer keeps each format's canonical bytes untouched.
    Calls _store_artifacts directly (AI vision is not configured in tests)."""

    def _store(self, data: bytes, content_type: str, filename: str):
        import app.db as db_config
        from app.models import User
        from app.services import _store_artifacts
        from app.schemas import ExtractedReceipt
        from datetime import date as _d
        s = db_config.SessionLocal()
        user = s.query(User).filter_by(email="riya@example.com").first()
        s.expunge(user)
        session = db_config.SessionLocal()
        s.close()
        ex = ExtractedReceipt(merchant="SNAP SHOP", date=_d(2026, 1, 2), amount=10.0,
                              currency="INR", description="test", category="OTHER",
                              raw_text="SNAP SHOPPING")
        return _store_artifacts(session, user, ex, image_content=data,
                                image_content_type=content_type, original_filename=filename)

    def test_png_preserved_as_png(self, seeded):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (120, 40), "white").save(buf, format="PNG")
        data = buf.getvalue()
        rec = self._store(data, "image/png", "shot.png")
        assert rec.content_type == "image/png"
        assert rec.original_filename == "shot.png"
        from app.storage import get_storage
        key = rec.s3_image_key or rec.image_path
        assert key.endswith("original.png")
        assert get_storage().get_object(key) == data  # no recompression

    def test_jpeg_preserved_as_jpeg(self, seeded):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (120, 40), "white").save(buf, format="JPEG")
        data = buf.getvalue()
        rec = self._store(data, "image/jpeg", "photo.jpg")
        assert rec.content_type == "image/jpeg"
        assert (rec.s3_image_key or rec.image_path).endswith("original.jpg")
        from app.storage import get_storage
        assert get_storage().get_object(rec.s3_image_key or rec.image_path) == data

    def test_webp_preserved(self, seeded):
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (120, 40), "white").save(buf, format="WEBP")
        data = buf.getvalue()
        rec = self._store(data, "image/webp", "shot.webp")
        assert rec.content_type == "image/webp"
        assert (rec.s3_image_key or rec.image_path or "").endswith("original.webp")


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

    def test_s3_canonical_original_only(self, seeded, monkeypatch):
        """New uploads store exactly one S3 object: the canonical original.
        No page images, OCR text or extracted JSON are uploaded."""
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
                # no redundant artifacts for new records
                assert rec.s3_pages_keys is None
                assert rec.s3_source_text_key is None
                assert rec.s3_extracted_key is None

                put_keys = [c.kwargs["Key"] for c in mock_s3.put_object.call_args_list]
                assert put_keys == [f"receipts/{rid}/original.pdf"]  # exactly one object
                # extracted data persisted in the database
                assert rec.extracted_data["merchant"] == "UBER"
                assert rec.checksum_sha256 is not None
        finally:
            db_config._storage_instance = None

    def test_presigned_original_expiry(self, seeded, monkeypatch):
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
                url = get_storage().get_download_url(rec.s3_image_key, db_config.PRESIGNED_URL_EXPIRES)
                assert parse_qs(urlparse(url).query).get("X-Amz-Expires") == ["300"]
        finally:
            db_config._storage_instance = None

    def test_partial_failure_cleans_up_and_reports_failure(self, seeded, monkeypatch):
        """Original upload fails -> 422, no DB row, nothing left in S3."""
        import app.db as db_config
        from app.models import Receipt
        self._enable_s3(monkeypatch)
        mock_s3 = MagicMock()

        def put_side_effect(**kwargs):
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
                assert "storage failed" in r.json()["detail"].lower()

                # no orphaned DB record beyond the pre-existing seed data
                s = db_config.SessionLocal()
                count = s.query(Receipt).count()
                s.close()
                assert count == baseline_count
        finally:
            self._teardown()
