import os
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class StoredObject:
    key: str
    url: Optional[str] = None
    content_type: Optional[str] = None
    size: Optional[int] = None


class StorageBackend(ABC):
    @abstractmethod
    def upload_image(self, content: bytes, content_type: str) -> StoredObject:
        pass

    @abstractmethod
    def upload_text(self, content: bytes, suffix: str = "txt") -> StoredObject:
        pass

    @abstractmethod
    def upload_json(self, content: bytes) -> StoredObject:
        pass

    @abstractmethod
    def upload_artifact(self, receipt_id: int, suffix: str, content: bytes, content_type: str) -> StoredObject:
        """Store a processing artifact under the receipt's key space, e.g. pages/page-001.jpg."""

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        pass

    @abstractmethod
    def delete_object(self, key: str) -> bool:
        pass

    @abstractmethod
    def get_download_url(self, key: str, expires_in: int = 300) -> str:
        pass

class LocalStorage(StorageBackend):
    def __init__(self, storage_dir: str):
        self.storage_dir = Path(storage_dir)
        self.receipts_dir = self.storage_dir / 'receipts'
        self.receipts_dir.mkdir(parents=True, exist_ok=True)

    def _make_key(self, ext: str) -> str:
        unique = f'{uuid.uuid4().hex}.{ext.lstrip(".")}'
        return str(Path('receipts') / unique)

    def upload_image(self, content: bytes, content_type: str) -> StoredObject:
        ext = 'jpg'
        if content_type == 'image/png':
            ext = 'png'
        elif content_type == 'image/webp':
            ext = 'webp'
        elif content_type == 'image/jpeg':
            ext = 'jpg'
        key = self._make_key(ext)
        full_path = self.storage_dir / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'wb') as f:
            f.write(content)
        return StoredObject(key=key)

    def upload_text(self, content: bytes, suffix: str = 'txt') -> StoredObject:
        key = self._make_key(suffix)
        full_path = self.storage_dir / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'wb') as f:
            f.write(content)
        return StoredObject(key=key)

    def upload_json(self, content: bytes) -> StoredObject:
        key = self._make_key('json')
        full_path = self.storage_dir / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'wb') as f:
            f.write(content)
        return StoredObject(key=key)

    def upload_artifact(self, receipt_id: int, suffix: str, content: bytes, content_type: str) -> StoredObject:
        suffix = suffix.lstrip('/')
        key = f'receipts/{receipt_id}/{suffix}'
        full_path = self.storage_dir / key
        full_path.parent.mkdir(parents=True, exist_ok=True)
        with open(full_path, 'wb') as f:
            f.write(content)
        return StoredObject(key=key, content_type=content_type, size=len(content))

    def get_object(self, key: str) -> bytes:
        full_path = self.storage_dir / key
        if not full_path.exists():
            raise FileNotFoundError(f'Object not found: {key}')
        return full_path.read_bytes()

    def delete_object(self, key: str) -> bool:
        full_path = self.storage_dir / key
        try:
            if full_path.exists():
                full_path.unlink()
            return True
        except Exception:
            return False

    def get_download_url(self, key: str, expires_in: int = 300) -> str:
        return f'/storage/{key}'


class S3Storage(StorageBackend):
    def __init__(self, bucket_name: str, region: str = 'us-east-1', sse: str = 'AES256',
                 key_prefix: str = 'receipts', endpoint_url: str | None = None,
                 access_key: str | None = None, secret_key: str | None = None):
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise RuntimeError('boto3 is required for S3 storage backend')
        self.bucket_name = bucket_name
        self.region = region
        # MinIO's local development server does not provide AWS KMS/SSE
        # configuration. Keep SSE for real AWS S3, but omit it for an
        # S3-compatible endpoint unless that endpoint explicitly supports it.
        self.sse = sse if endpoint_url is None else None
        self.key_prefix = key_prefix.strip('/')
        # MinIO (or any S3-compatible object store) is addressed via endpoint_url;
        # None keeps the real AWS endpoint. Credentials only passed when overriding.
        session_kwargs = {}
        if access_key and secret_key:
            session_kwargs = {"aws_access_key_id": access_key, "aws_secret_access_key": secret_key}
        self.s3 = boto3.client('s3', region_name=region, endpoint_url=endpoint_url,
                               config=boto3.session.Config(signature_version='s3v4'),
                               **session_kwargs)

    def _put_args(self, content_type: str) -> dict:
        args = {'ContentType': content_type}
        if self.sse:
            args['ServerSideEncryption'] = self.sse
        return args

    def _make_key(self, ext: str) -> str:
        unique = f'{uuid.uuid4().hex}.{ext.lstrip(".")}'
        return f'{self.key_prefix}/{unique}'

    def upload_image(self, content: bytes, content_type: str) -> StoredObject:
        ext = {
            'image/png': 'png',
            'image/webp': 'webp',
            'image/jpeg': 'jpg',
            'application/pdf': 'pdf',
        }.get(content_type, 'bin')
        key = self._make_key(ext)
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content,
                            **self._put_args(content_type))
        return StoredObject(key=key, content_type=content_type, size=len(content))

    def upload_text(self, content: bytes, suffix: str = 'txt') -> StoredObject:
        key = self._make_key(suffix)
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content,
                            **self._put_args('text/plain'))
        return StoredObject(key=key)

    def upload_json(self, content: bytes) -> StoredObject:
        key = self._make_key('json')
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content,
                            **self._put_args('application/json'))
        return StoredObject(key=key)

    def upload_artifact(self, receipt_id: int, suffix: str, content: bytes, content_type: str) -> StoredObject:
        suffix = suffix.lstrip('/')
        key = f'{self.key_prefix}/{receipt_id}/{suffix}'
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content,
                            **self._put_args(content_type))
        return StoredObject(key=key, content_type=content_type, size=len(content))

    def get_object(self, key: str) -> bytes:
        try:
            resp = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            return resp['Body'].read()
        except Exception as e:
            # NoSuchKey maps to the documented "missing file" signal; anything
            # else (network/service problems) propagates untouched as before.
            from botocore.exceptions import ClientError
            if isinstance(e, ClientError) and e.response.get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound'):
                raise FileNotFoundError(f'Object not found: {key}')
            raise

    def delete_object(self, key: str) -> bool:
        try:
            self.s3.delete_object(Bucket=self.bucket_name, Key=key)
            return True
        except Exception:
            return False

    def get_download_url(self, key: str, expires_in: int = 300) -> str:
        try:
            return self.s3.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': key},
                ExpiresIn=expires_in,
            )
        except Exception:
            raise RuntimeError('Failed to generate pre-signed URL')


def get_storage() -> StorageBackend:
    from .db import get_storage_backend
    return get_storage_backend()


# --- tiered storage ----------------------------------------------------------
# Composite backend: HOT serves today (local disk, or MinIO when configured);
# COLD is the S3 archive and is only touched at payment promotion or when the
# paid-cache copy has expired. Every promote is checksum-verified so the archive
# copy is provably the same bytes, and a failed promote never blocks payment.

class TieredStorage:
    def __init__(self, hot: StorageBackend, cold: "S3Storage | None" = None):
        self.hot = hot
        self.cold = cold

    # -- routing by receipt.storage_tier -------------------------------------
    def get_content(self, key: str, tier: str = "HOT",
                    paid_cache_key: str | None = None) -> tuple[bytes, str]:
        """Read the receipt bytes and report where they physically came from:
        'hot' | 'paid-cache' | 'cold' — callers ticket this as served_from."""
        if tier == "COLD":
            if paid_cache_key:
                try:
                    return self.hot.get_object(paid_cache_key), "paid-cache"
                except FileNotFoundError:
                    pass
            if self.cold is not None:
                return self.cold.get_object(key), "cold"
        return self.hot.get_object(key), "hot"

    # -- promotion (payment path; must be idempotent and criticism-proof) -----
    def promote(self, hot_key: str, receipt_id: int, filename: str,
                content: bytes | None = None) -> str:
        """Copy a receipt's canonical file to the cold S3 tier (STANDARD_IA).

        Returns the cold key. Idempotent: a second call re-uploads the same
        verified bytes to the same key. Raises on verification failure — the
        caller decides to ticket ARCHIVAL_FAILED and continue; payment never
        depends on this."""
        import hashlib
        if self.cold is None:
            raise RuntimeError("No cold storage configured")
        cold_key = f'{self.cold.key_prefix}/{receipt_id}/{filename or "receipt.bin"}'
        if content is None:
            content = self.hot.get_object(hot_key)
        self.cold.s3.put_object(
            Bucket=self.cold.bucket_name, Key=cold_key, Body=content,
            StorageClass=S3_COLD_STORAGE_CLASS,
            ContentType=_content_type_for(filename))
        # checksum-verify what actually landed in the archive
        resp = self.cold.s3.get_object(Bucket=self.cold.bucket_name, Key=cold_key)
        body = resp['Body'].read()
        if hashlib.sha256(body).hexdigest() != hashlib.sha256(content).hexdigest():
            self.cold.delete_object(cold_key)
            raise RuntimeError("Cold copy failed checksum verification; archive copy deleted")
        return cold_key

    # -- paid cache (MinIO / hot tier, expired by lifecycle rule) --------------
    def put_paid_cache(self, receipt_id: int, filename: str, content: bytes) -> str:
        if getattr(self.hot, "s3", None) is None:
            raise RuntimeError("Paid cache requires an object-store hot tier (MinIO/S3)")
        key = f'{PAID_CACHE_PREFIX}/{receipt_id}/{filename or "receipt.bin"}'
        self.hot.s3.put_object(Bucket=self.hot.bucket_name, Key=key, Body=content,
                               ContentType=_content_type_for(filename))
        return key

    def delete_paid_cache(self, paid_cache_key: str) -> None:
        try:
            self.hot.delete_object(paid_cache_key)
        except Exception:
            pass

    def apply_paid_cache_lifecycle(self, days: int) -> bool:
        """Install the MinIO (or S3) lifecycle rule expiring paid-cache/ copies
        after `days` days. Never fatal at startup: lifecycle rules can also be
        provisioned in infrastructure, so failures only report False."""
        try:
            self.hot.s3.put_bucket_lifecycle_configuration(
                Bucket=self.hot.bucket_name,
                LifecycleConfiguration={
                    "Rules": [{
                        "ID": "expire-paid-cache",
                        "Status": "Enabled",
                        "Filter": {"Prefix": PAID_CACHE_PREFIX + "/"},
                        "Expiration": {"Days": days},
                    }],
                })
            return True
        except Exception:
            return False

    def sweep_expired_paid_cache(self, paid_copies: list) -> int:
        """Proactive sweep for deployments without lifecycle support: delete
        paid-cache copies whose payment is older than PAID_CACHE_DAYS.
        `paid_copies` is a list of (paid_cache_key, paid_at) tuples resolved by
        the caller (paid_at taken from the linked claim). Callers ticket
        CACHE_EXPIRED per receipt. Returns the number of keys deleted."""
        import datetime
        cutoff = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=PAID_CACHE_DAYS)
        deleted = 0
        for key, paid_at in paid_copies:
            if not key or paid_at is None:
                continue
            if paid_at.tzinfo is None:
                paid_at = paid_at.replace(tzinfo=datetime.timezone.utc)
            if paid_at < cutoff:
                self.delete_paid_cache(key)
                deleted += 1
        return deleted


S3_COLD_STORAGE_CLASS = "STANDARD_IA"
PAID_CACHE_PREFIX = "paid-cache"


def _content_type_for(filename: str | None) -> str:
    import mimetypes
    guess, _ = mimetypes.guess_type(filename or "")
    return guess or "application/octet-stream"


def get_tiered_storage() -> TieredStorage:
    """Wired tiering: hot from get_storage(); cold from db.get_cold_storage()
    when a cold bucket is configured. Local-disk hot tier cannot hold a paid
    cache (no lifecycle/S3 API) — promotion is then copy-to-cold only."""
    from .db import get_storage_backend, get_cold_storage, COLD_S3_BUCKET_NAME
    hot = get_storage_backend()
    cold = None
    if COLD_S3_BUCKET_NAME and getattr(hot, "bucket_name", None) != COLD_S3_BUCKET_NAME:
        try:
            cold = get_cold_storage()
        except Exception:
            cold = None
    return TieredStorage(hot=hot, cold=cold)
