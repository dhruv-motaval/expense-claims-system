import io
from unittest.mock import MagicMock, patch

from app import db as db_config


def test_s3_sets_keys_on_upload(seeded, monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "expense-claims-system-dhruv-motaval")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    db_config._storage_instance = None

    mock_s3 = MagicMock()
    mock_s3.put_object.return_value = {}
    with patch("boto3.client", return_value=mock_s3):
        from app.db import get_storage_backend
        storage = get_storage_backend()
        obj = storage.upload_image(b"fake", "image/jpeg")
        assert obj.key is not None


def test_receipt_view_authorization(seeded):
    r_riya = seeded.post("/api/auth/login", json={"email": "riya@example.com"})
    r_neha = seeded.post("/api/auth/login", json={"email": "neha@example.com"})
    h_riya = {"Authorization": f"Bearer {r_riya.json()['token']}"}
    h_neha = {"Authorization": f"Bearer {r_neha.json()['token']}"}

    data = {"text": "CAFE\n10 Oct 2026\nCoffee\n\u20b9500"}
    r = seeded.post("/api/receipts/extract", headers=h_riya, data=data)
    rid = r.json()["receipt_id"]

    img = seeded.get(f"/api/receipts/image/{rid}", headers=h_neha)
    assert img.status_code == 403
