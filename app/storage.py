"""Durable artifact storage.

Local storage is intended for development and single-container deployments.
Production can use S3-compatible object storage so generated CVs survive
container replacement and can be served independently from the web process.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import BinaryIO

from app.config import get_settings, project_path


class StorageError(RuntimeError):
    pass


class ObjectStorage:
    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        raise NotImplementedError

    def read(self, key: str) -> bytes:
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        raise NotImplementedError


class LocalObjectStorage(ObjectStorage):
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _path(self, key: str) -> Path:
        candidate = (self.root / key).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise StorageError("storage key escapes object storage root")
        return candidate

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        del content_type
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def read(self, key: str) -> bytes:
        path = self._path(key)
        if not path.is_file():
            raise StorageError("artifact not found")
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStorage(ObjectStorage):
    def __init__(self) -> None:
        settings = get_settings()
        bucket = (settings.r2_bucket_name or "").strip()
        account_id = (settings.r2_account_id or "").strip()
        access_key_id = (settings.r2_access_key_id or "").strip()
        secret_access_key = (settings.r2_secret_access_key or "").strip()
        if not bucket or not account_id or not access_key_id or not secret_access_key:
            raise StorageError(
                "R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, and R2_BUCKET_NAME are required"
            )
        try:
            import boto3
        except ImportError as exc:
            raise StorageError("boto3 is required for S3 object storage") from exc
        kwargs = {"config": __import__("botocore").config.Config(signature_version="s3v4")}
        endpoint = f"https://{account_id}.r2.cloudflarestorage.com"
        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            region_name="auto",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            **kwargs,
        )

    def put(self, key: str, data: bytes, *, content_type: str = "application/octet-stream") -> str:
        self.client.upload_fileobj(
            io.BytesIO(data),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
        )
        return key

    def read(self, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
            body: BinaryIO = response["Body"]
            return body.read()
        except Exception as exc:
            raise StorageError("artifact not found") from exc

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False


def get_storage() -> ObjectStorage:
    settings = get_settings()
    backend = (settings.object_storage_backend or "local").strip().lower()
    if backend == "r2":
        return S3ObjectStorage()
    if backend != "local":
        raise StorageError(f"unsupported object storage backend: {backend}")
    return LocalObjectStorage(project_path("data", "objects"))
