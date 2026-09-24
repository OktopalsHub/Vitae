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
        bucket = (settings.object_storage_bucket or "").strip()
        if not bucket:
            raise StorageError("OBJECT_STORAGE_BUCKET is required for S3 storage")
        try:
            import boto3
        except ImportError as exc:
            raise StorageError("boto3 is required for S3 object storage") from exc
        kwargs = {"config": __import__("botocore").config.Config(signature_version="s3v4")}
        # R2 and other S3-compatible endpoints require path-style addressing only when the provider needs it.
        # boto3 defaults to virtual-hosted addressing, which R2 supports.
        endpoint = (settings.object_storage_endpoint or "").strip()
        if endpoint:
            kwargs["endpoint_url"] = endpoint
        region = (settings.object_storage_region or "").strip()
        if region:
            kwargs["region_name"] = region
        self.bucket = bucket
        self.client = boto3.client("s3", **kwargs)

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
    if backend == "s3":
        return S3ObjectStorage()
    if backend != "local":
        raise StorageError(f"unsupported object storage backend: {backend}")
    return LocalObjectStorage(project_path("data", "objects"))
