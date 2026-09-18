from __future__ import annotations

from pathlib import Path

import boto3

from .settings import RenderSettings, get_render_settings


class RenderStore:
    def get_bytes(self, key: str) -> bytes:
        raise NotImplementedError

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        raise NotImplementedError


class LocalRenderStore(RenderStore):
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("object key escapes storage root")
        return path

    def get_bytes(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return key


class S3RenderStore(RenderStore):
    def __init__(self, settings: RenderSettings):
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
        )

    def get_bytes(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return key


def get_render_store(settings: RenderSettings | None = None) -> RenderStore:
    settings = settings or get_render_settings()
    if settings.storage_backend == "s3":
        return S3RenderStore(settings)
    return LocalRenderStore(settings.storage_root)

