from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

import boto3

from .settings import Settings, get_settings


class ObjectStore(ABC):
    @abstractmethod
    def put_bytes(self, key: str, data: bytes, content_type: str) -> str: ...

    @abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abstractmethod
    def presigned_put(self, key: str, content_type: str) -> str: ...

    @abstractmethod
    def presigned_get(self, key: str) -> str: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...


class LocalObjectStore(ObjectStore):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("object key escapes storage root")
        return path

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def presigned_put(self, key: str, content_type: str) -> str:
        return f"/v1/dev-uploads/{key}"

    def presigned_get(self, key: str) -> str:
        return f"/v1/dev-objects/{key}"

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()


class S3ObjectStore(ObjectStore):
    def __init__(self, settings: Settings):
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
        )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def presigned_put(self, key: str, content_type: str) -> str:
        return self.client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=900,
        )

    def presigned_get(self, key: str) -> str:
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=900
        )

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except self.client.exceptions.ClientError:
            return False


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def get_object_store(settings: Settings | None = None) -> ObjectStore:
    settings = settings or get_settings()
    if settings.storage_backend == "s3":
        return S3ObjectStore(settings)
    return LocalObjectStore(settings.storage_root)
