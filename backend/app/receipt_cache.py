"""In-memory receipt content cache: TTL (1h) + LRU (64 MB), delete-invalidated.

Rules (docs/FINAL-DESIGN.md Part C §cache):
- Only raw receipt BYTES are cached, keyed by receipt id (+ checksum). The cache
  never authorizes: callers check `can_view_receipt`/auth BEFORE touching it.
- A failed/denied lookup is therefore never served — cache hits are keyed the
  same way permission-checked downloads are, and any permission change is
  immaterial because the bytes themselves are not sensitive beyond the check.
- delete_receipt / delete-invalidation clears the entry so a deleted file is
  never served from memory after its removal.
"""
import threading
import time
from collections import OrderedDict

TTL_SECONDS = int(__import__("os").getenv("RECEIPT_CACHE_TTL_SECONDS", "3600"))     # 1h
MAX_BYTES = int(__import__("os").getenv("RECEIPT_CACHE_MAX_BYTES", str(64 * 1024 * 1024)))  # 64 MB


class ReceiptCache:
    def __init__(self, ttl: int = TTL_SECONDS, max_bytes: int = MAX_BYTES):
        self._ttl = ttl
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        self._store: OrderedDict[str, tuple[float, int, bytes]] = OrderedDict()
        self._hits = 0
        self._misses = 0

    def get(self, key: str, checksum: str | None = None) -> bytes | None:
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                self._misses += 1
                return None
            at, size, data = entry
            if time.monotonic() - at > self._ttl:
                del self._store[key]
                self._misses += 1
                return None
            self._store.move_to_end(key)
            self._hits += 1
            return data

    def put(self, key: str, data: bytes) -> None:
        if not data:
            return
        size = len(data)
        if size > self._max_bytes:
            return  # one oversized object would evict the whole cache; skip it
        with self._lock:
            self._store[key] = (time.monotonic(), size, data)
            self._evict_locked()

    def _evict_locked(self) -> None:
        total = sum(size for _, size, _ in self._store.values())
        while total > self._max_bytes and self._store:
            _, (__, size, ___) = self._store.popitem(last=False)  # LRU end
            total -= size

    def invalidate(self, key: str) -> None:
        with self._lock:
            self._store.pop(key, None)

    def invalidate_receipt(self, receipt_id: int) -> None:
        """Delete-invalidation: removes every derived key for this receipt."""
        prefix = f"receipt:{receipt_id}:"
        with self._lock:
            for k in [k for k in self._store if k.startswith(prefix)]:
                del self._store[k]

    def stats(self) -> dict:
        with self._lock:
            return {"entries": len(self._store),
                    "bytes": sum(size for _, size, _ in self._store.values()),
                    "hits": self._hits, "misses": self._misses}


cache = ReceiptCache()
