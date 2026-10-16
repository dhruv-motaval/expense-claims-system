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

    if STORAGE_BACKEND == "s3":
        if not S3_BUCKET_NAME:
            raise ValueError("S3_BUCKET_NAME must be set when STORAGE_BACKEND=s3")
        from .storage import S3Storage

        _storage_instance = S3Storage(bucket_name=S3_BUCKET_NAME, region=AWS_REGION,
                                      key_prefix=S3_KEY_PREFIX)
        return _storage_instance
    else:
        from .storage import LocalStorage

        _storage_instance = LocalStorage(storage_dir=STORAGE_DIR)
        return _storage_instance
