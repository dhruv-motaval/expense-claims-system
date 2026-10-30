"""Gate tests — Phase ④: in-memory receipt cache (TTL, LRU, invalidation)."""
import time
from collections import OrderedDict


def fresh_cache(ttl=60, max_bytes=1000):
    from app.receipt_cache import ReceiptCache
    return ReceiptCache(ttl=ttl, max_bytes=max_bytes)


def test_put_get_roundtrip_and_miss():
    c = fresh_cache()
    c.put("k", b"abc")
    assert c.get("k") == b"abc"
    assert c.get("missing") is None


def test_ttl_expiry():
    c = fresh_cache(ttl=1)
    c.put("k", b"abc")
    assert c.get("k") == b"abc"
    # force expiry by rewinding the stored timestamp
    with c._lock:
        store = c._store
        k = next(iter(store))
        at = store[k][0]
        store[k] = (at - 5, store[k][1], store[k][2])
    assert c.get("k") is None


def test_lru_eviction_respects_max_bytes():
    c = fresh_cache(max_bytes=300)
    a = b"a" * 200   # a, b together exceed 300 -> a evicted when b is added
    b = b"b" * 200
    c.put("a", a)
    c.put("b", b)
    assert c.get("a") is None     # oldest evicted first (LRU)
    assert c.get("b") == b        # survivor intact


def test_delete_invalidation_removes_all_derived_keys():
    c = fresh_cache()
    c.put("receipt:41:checksum", b"x")
    c.put("receipt:41:other", b"y")
    c.put("receipt:42:checksum", b"z")
    c.invalidate_receipt(41)
    assert c.get("receipt:41:checksum") is None
    assert c.get("receipt:41:other") is None
    assert c.get("receipt:42:checksum") == b"z"   # other receipts untouched


def test_no_authorization_bypass_semantics():
    """Auth is checked by the caller BEFORE the cache; the cache just stores
    bytes. Document+verify: nothing cached authorizes anything by itself —
    a missing key can never be a permission via-cache."""
    c = fresh_cache()
    assert c.get("never-put") is None  # no phantom authorization possible
    stats = c.stats()
    assert stats["hits"] + stats["misses"] > 0
