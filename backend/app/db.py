import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://expense:expense@localhost:5432/expense_claims")

_engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in DATABASE_URL:
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
STORAGE_DIR = os.getenv("STORAGE_DIR") or os.path.join(os.path.dirname(__file__), "..", "..", "storage")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
S3_KEY_PREFIX = os.getenv("S3_KEY_PREFIX", "receipts")
UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "10"))

# --- tiered storage (docs/FINAL-DESIGN.md Part C) ---------------------------
# Hot tier: local disk (or MinIO) serves active claims. Cold tier: S3 holds the
# archive after payment. MinIO keeps a paid-cache/ copy for PAID_CACHE_DAYS
# after payment so recently-paid receipts still serve from local storage; the
# MinIO lifecycle rule expires that prefix on its own.
COLD_S3_BUCKET_NAME = os.getenv("COLD_S3_BUCKET_NAME") or S3_BUCKET_NAME
S3_STORAGE_CLASS = os.getenv("S3_STORAGE_CLASS", "STANDARD_IA")   # paid archive class
# MinIO doubles as the hot object store when configured (S3 API compatible).
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT")                      # e.g. http://localhost:9000
MINIO_ACCESS_KEY = os.getenv("MINIO_ROOT_USER") or os.getenv("MINIO_ACCESS_KEY") or ""
MINIO_SECRET_KEY = os.getenv("MINIO_ROOT_PASSWORD") or os.getenv("MINIO_SECRET_KEY") or ""
MINIO_BUCKET_NAME = os.getenv("MINIO_BUCKET_NAME", "receipts-hot")
PAID_CACHE_DAYS = int(os.getenv("PAID_CACHE_DAYS", "7"))
PAID_CACHE_PREFIX = "paid-cache"
PRESIGNED_URL_EXPIRES = int(os.getenv("PRESIGNED_URL_EXPIRES", "300"))


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


_storage_instance = None


def get_storage_backend():
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    from .storage import S3Storage

    # When MinIO is configured it is the HOT object store, even though
    # STORAGE_BACKEND remains "local" for backwards-compatible local-disk
    # deployments. This keeps the compose setup aligned with the tiered design.
    if MINIO_ENDPOINT:
        _storage_instance = S3Storage(
            bucket_name=MINIO_BUCKET_NAME,
            region=AWS_REGION,
            key_prefix=S3_KEY_PREFIX,
            endpoint_url=MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
        )
        _ensure_bucket(_storage_instance)
        return _storage_instance

    if STORAGE_BACKEND == "s3":
        if not S3_BUCKET_NAME:
            raise ValueError("S3_BUCKET_NAME must be set when STORAGE_BACKEND=s3")
        _storage_instance = S3Storage(bucket_name=S3_BUCKET_NAME, region=AWS_REGION,
                                      key_prefix=S3_KEY_PREFIX)
        return _storage_instance

    from .storage import LocalStorage
    _storage_instance = LocalStorage(storage_dir=STORAGE_DIR)
    return _storage_instance


def _ensure_bucket(storage):
    """Create a configured S3-compatible hot bucket if it does not exist."""
    try:
        storage.s3.head_bucket(Bucket=storage.bucket_name)
    except Exception:
        storage.s3.create_bucket(Bucket=storage.bucket_name)


def get_cold_storage():
    """The archive (cold) S3 backend — always S3-compatible, never local."""
    from .storage import S3Storage
    if not COLD_S3_BUCKET_NAME:
        raise ValueError("COLD_S3_BUCKET_NAME (or S3_BUCKET_NAME) must be set for cold tier")
    return S3Storage(bucket_name=COLD_S3_BUCKET_NAME, region=AWS_REGION)
