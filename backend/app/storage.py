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
                 key_prefix: str = 'receipts'):
        try:
            import boto3
            from botocore.config import Config
        except ImportError:
            raise RuntimeError('boto3 is required for S3 storage backend')
        self.bucket_name = bucket_name
        self.region = region
        self.sse = sse
        self.key_prefix = key_prefix.strip('/')
        self.s3 = boto3.client('s3', region_name=region,
                               config=boto3.session.Config(signature_version='s3v4'))

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
        extra_args = {'ContentType': content_type, 'ServerSideEncryption': self.sse}
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content, **extra_args)
        return StoredObject(key=key, content_type=content_type, size=len(content))

    def upload_text(self, content: bytes, suffix: str = 'txt') -> StoredObject:
        key = self._make_key(suffix)
        extra_args = {'ContentType': 'text/plain', 'ServerSideEncryption': self.sse}
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content, **extra_args)
        return StoredObject(key=key)

    def upload_json(self, content: bytes) -> StoredObject:
        key = self._make_key('json')
        extra_args = {'ContentType': 'application/json', 'ServerSideEncryption': self.sse}
        self.s3.put_object(Bucket=self.bucket_name, Key=key, Body=content, **extra_args)
        return StoredObject(key=key)

    def get_object(self, key: str) -> bytes:
        try:
            resp = self.s3.get_object(Bucket=self.bucket_name, Key=key)
            return resp['Body'].read()
        except Exception as e:
            try:
                from botocore.exceptions import ClientError
                if isinstance(e, ClientError) and e.response.get('Error', {}).get('Code') == 'NoSuchKey':
                    raise FileNotFoundError(f'Object not found: {key}')
            except Exception:
                pass
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
