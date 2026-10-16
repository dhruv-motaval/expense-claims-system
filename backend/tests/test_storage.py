import io
from unittest.mock import MagicMock, patch

from app import db as db_config


def test_s3_storage_with_mocks(monkeypatch):
    monkeypatch.setenv("STORAGE_BACKEND", "s3")
    monkeypatch.setenv("S3_BUCKET_NAME", "test-bucket")
    monkeypatch.setenv("AWS_REGION", "ap-south-1")

    # reset storage singleton
    db_config._storage_instance = None

    mock_s3 = MagicMock()
    with patch("boto3.client", return_value=mock_s3):
        from app.db import get_storage_backend
        storage = get_storage_backend()
        storage.upload_image(b"fake", "image/png")


def test_upload_oversized(seeded):
    r = seeded.post("/api/auth/login", json={"email": "riya@example.com"})
    token = r.json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    big = b"x" * (20 * 1024 * 1024)  # 20MB, default 10MB limit
    files = {"file": ("big.png", io.BytesIO(big), "image/png")}
    r = seeded.post("/api/receipts/upload", headers=headers, files=files)
    assert r.status_code == 422
