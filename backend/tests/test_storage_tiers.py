"""Gate tests — Phase ④: tiered storage (promote idempotent + verify, routing,
paid-cache fallback, expiry reconciliation, and payment-never-depends-on-promotion)."""
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pytest

from fastapi.testclient import TestClient


@pytest.fixture
def c(seeded):
    return seeded


def cold_mock(key_prefix="receipts"):
    """An S3Storage stand-in: real S3Storage with a botocore client replaced
    by an in-memory dict store. No AWS, no network."""
    from app.storage import S3Storage
    s = S3Storage.__new__(S3Storage)                # skip boto3 client creation
    s.bucket_name = "cold-bucket"
    s.region = "us-east-1"
    s.sse = "AES256"
    s.key_prefix = key_prefix
    s.s3 = MagicMock()
    s._objects = {}

    def put_object(Bucket, Key, Body=None, StorageClass=None, ContentType=None, **kw):
        s._objects[Key] = (bytes(Body), StorageClass, ContentType)

    def get_object(Bucket, Key):
        if Key not in s._objects:
            class E(Exception):
                pass
            err = OSError(f"key {Key}")
            raise KeyError(Key)
        return {"Body": MagicMock(read=lambda: s._objects[Key][0])}

    s.s3.put_object.side_effect = put_object
    s.s3.get_object.side_effect = get_object
    orig_delete = s.delete_object

    def delete_object(key):
        s._objects.pop(key, None)
        return True

    s.delete_object = delete_object
    return s


def test_promote_verifies_and_is_idempotent(c):
    from app.storage import TieredStorage, LocalStorage
    from app import db as db_config
    hot = LocalStorage(db_config.STORAGE_DIR)
    cold = cold_mock()
    t = TieredStorage(hot=hot, cold=cold)

    content = b"receipt-bytes-001"
    uploaded = hot.upload_image(content, "image/jpeg")

    key = t.promote(uploaded.key, 777, "receipt.jpg", content)
    assert key.endswith("777/receipt.jpg")
    stored, storage_class, _ = cold._objects[key]
    assert stored == content
    assert storage_class == "STANDARD_IA", "paid archive must use the reduced-redundancy class"

    # idempotent re-run: same key, same verified result, no error
    key2 = t.promote(uploaded.key, 777, "receipt.jpg", content)
    assert key2 == key

    # tampered archive body: verification must delete the copy and raise
    def tampered_get(Bucket, Key):
        return {"Body": MagicMock(read=lambda: b"tampered")}

    cold.s3.get_object.side_effect = tampered_get
    with pytest.raises(Exception) as ei:
        t.promote(uploaded.key, 888, "bad-receipt.jpg", content)
    assert "checksum" in str(ei.value)
    assert not any("888/bad-receipt.jpg" in k for k in cold._objects), "bad copy must be deleted"
    cold.s3.get_object = MagicMock(side_effect=lambda Bucket, Key: {"Body": MagicMock(read=lambda: cold._objects[Key][0])})


def test_routing_by_tier_and_paid_cache_fallback(c):
    from app.storage import TieredStorage, LocalStorage
    from app import db as db_config
    hot = LocalStorage(db_config.STORAGE_DIR)
    cold = cold_mock()
    t = TieredStorage(hot=hot, cold=cold)

    up = hot.upload_image(b"cold-tier-bytes", "image/jpeg")
    # the archive holds the copy under the same canonical key; paid-cache is a
    # hot-tier file that a real MinIO lifecycle rule would expire
    cold._objects[up.key] = (b"cold-tier-bytes", "STANDARD_IA", "image/jpeg")
    paid_cache_file = hot.storage_dir / "paid-cache" / "555" / "receipt.jpg"
    paid_cache_file.parent.mkdir(parents=True, exist_ok=True)
    paid_cache_file.write_bytes(b"cold-tier-bytes")

    # HOT routing goes to hot storage
    data, served = t.get_content(up.key, tier="HOT")
    assert served == "hot" and data == b"cold-tier-bytes"

    # COLD with a live paid-cache copy: served from cache, not S3
    data, served = t.get_content(up.key, tier="COLD", paid_cache_key="paid-cache/555/receipt.jpg")
    assert served == "paid-cache"

    # COLD with the paid cache expired: falls back to the archive
    data, served = t.get_content(up.key, tier="COLD", paid_cache_key="paid-cache/missing")
    assert served == "cold" and data == b"cold-tier-bytes"

    # COLD when cold is None but paid-cache exists (degraded local deployment)
    t3 = TieredStorage(hot=hot, cold=None)
    data, served = t3.get_content(up.key, tier="COLD", paid_cache_key="paid-cache/555/receipt.jpg")
    assert served == "paid-cache"


def test_paid_cache_needs_object_store_hot(c):
    from app.storage import TieredStorage, LocalStorage
    from app import db as db_config
    t = TieredStorage(hot=LocalStorage(db_config.STORAGE_DIR), cold=cold_mock())
    with pytest.raises(Exception):
        t.put_paid_cache(1, "x.jpg", b"abc")   # local disk cannot hold a paid cache


def test_payment_never_depends_on_promotion(c):
    """STORAGE_BACKEND=local (tests): cold tier is None — paying must still
    succeed and the receipt must remain HOT with no archival tickets."""
    riya = {"Authorization": f"Bearer {c.post('/api/auth/login', json={'email': 'riya@example.com'}).json()['token']}"}
    arjun = {"Authorization": f"Bearer {c.post('/api/auth/login', json={'email': 'arjun@example.com'}).json()['token']}"}
    priya = {"Authorization": f"Bearer {c.post('/api/auth/login', json={'email': 'priya@example.com'}).json()['token']}"}
    r = c.post("/api/claims", headers=riya, json={
        "merchant": "NoColdCorp", "transaction_date": "2026-10-11", "category": "OTHER",
        "amount": 42, "receipt_no": "NC-1"})
    cid = r.json()["id"]
    assert c.post(f"/api/claims/{cid}/submit", headers=riya).status_code == 200
    assert c.post(f"/api/claims/{cid}/approve", headers=arjun).status_code == 200
    assert c.post(f"/api/claims/{cid}/pay", headers=priya).status_code == 200
    detail = c.get(f"/api/claims/{cid}", headers=riya).json()
    assert detail["status"] == "PAID"
    assert not any(a["action"] in ("RECEIPT_ARCHIVED", "ARCHIVAL_FAILED") for a in detail["audit"]), \
        "no cold tier configured: no archival tickets expected"


def test_lifecycle_rule_install_reports_not_fatal(c):
    """apply_paid_cache_lifecycle must never be fatal and must report success
    or failure honestly. With a mock client it succeeds; with a failing client
    it returns False."""
    from app.storage import TieredStorage, LocalStorage
    from app import db as db_config
    hot = LocalStorage(db_config.STORAGE_DIR)
    t = TieredStorage(hot=hot, cold=None)
    # local disk hot tier has no object-store client -> honest False
    assert t.apply_paid_cache_lifecycle(7) is False


def test_lifecycle_rule_uses_prefix_and_days(c):
    from app.storage import TieredStorage, LocalStorage
    from app import db as db_config
    hot = LocalStorage(db_config.STORAGE_DIR)
    hot.s3 = MagicMock()
    hot.bucket_name = "hot-bucket"
    t = TieredStorage(hot=hot, cold=None)
    assert t.apply_paid_cache_lifecycle(7) is True
    cfg = hot.s3.put_bucket_lifecycle_configuration.call_args.kwargs["LifecycleConfiguration"]
    rule = cfg["Rules"][0]
    assert rule["Filter"]["Prefix"].startswith("paid-cache/")
    assert rule["Expiration"]["Days"] == 7
    assert rule["Status"] == "Enabled"


def test_expired_paid_cache_is_reconciled_and_ticketed(c, monkeypatch):
    """A lifecycle-deleted cache copy clears the stale pointer exactly once."""
    from sqlalchemy import select

    from app import services
    from app.db import SessionLocal
    from app.models import AuditEvent, Claim, Receipt, Status, User

    db = SessionLocal()
    owner = db.scalar(select(User).where(User.email == "riya@example.com"))
    receipt = Receipt(
        user_id=owner.id,
        image_path="receipts/expired.jpg",
        s3_image_key="receipts/expired.jpg",
        original_filename="expired.jpg",
        storage_tier="COLD",
        paid_cache_key="paid-cache/999/expired.jpg",
    )
    claim = Claim(
        claim_number="CLM-EXPIRE-1",
        user_id=owner.id,
        merchant="ExpiredCacheTest",
        transaction_date=date.today(),
        category="OTHER",
        amount=10,
        currency="INR",
        status=Status.PAID.value,
        receipt=receipt,
        paid_at=datetime.now(timezone.utc),
    )
    db.add(claim)
    db.commit()
    db.refresh(receipt)

    class ColdFallback:
        cold = object()

        def get_content(self, key, tier, paid_cache_key):
            assert tier == "COLD"
            assert paid_cache_key == "paid-cache/999/expired.jpg"
            return b"archived", "cold"

    monkeypatch.setattr(services, "get_tiered_storage", lambda: ColdFallback())
    try:
        content, served_from = services.get_receipt_content(db, owner, receipt)
        assert content == b"archived"
        assert served_from == "cold"
        assert receipt.paid_cache_key is None
        actions = db.scalars(select(AuditEvent.action).where(AuditEvent.claim_id == claim.id)).all()
        assert "CACHE_EXPIRED" in actions
        assert "COLD_TIER_ACCESSED" in actions
    finally:
        db.query(AuditEvent).filter(AuditEvent.claim_id == claim.id).delete()
        db.delete(claim)
        db.delete(receipt)
        db.commit()
        db.close()
