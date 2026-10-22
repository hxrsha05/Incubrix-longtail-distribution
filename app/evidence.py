"""Where certification evidence is stored: always on local disk, and also in S3 when S3_ENABLED=true.

The result carries a download link: a presigned S3 URL, or our own /api/v1/evidence/... route for the local copy.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol


class S3Like(Protocol):  # the two boto3 client calls we use, so tests can pass a fake
    def put_object(self, **kwargs: Any) -> Any: ...
    def generate_presigned_url(self, op: str, Params: dict[str, Any], ExpiresIn: int) -> str: ...


class EvidenceStore:
    def __init__(self, directory: str | Path, s3: S3Like | None = None, bucket: str = "", link_ttl_s: int = 3600) -> None:
        self.directory, self.s3, self.bucket, self.link_ttl_s = Path(directory), s3, bucket, link_ttl_s

    def save(self, destination_id: str, name: str, content: dict[str, Any] | str) -> dict[str, str | None]:
        """Write one evidence file. Returns where it went: local_path, s3_key, download_url."""
        text = content if isinstance(content, str) else json.dumps(content, indent=2, sort_keys=True, default=str)
        rel = f"{destination_id}/{name}"
        path = self.directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        result: dict[str, str | None] = {"local_path": str(path).replace("\\", "/"), "s3_key": None, "download_url": f"/api/v1/evidence/{rel}"}
        if self.s3 is not None:
            key = f"certification/{rel}"
            self.s3.put_object(Bucket=self.bucket, Key=key, Body=text.encode(), ContentType="application/json" if name.endswith(".json") else "text/plain")
            result["s3_key"] = key
            result["download_url"] = self.s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=self.link_ttl_s)
        return result

    def read(self, rel: str) -> Path | None:
        """Resolve a requested path inside the evidence folder (and refuse anything that escapes it)."""
        base = self.directory.resolve()
        path = (base / rel).resolve()
        if base not in path.parents or not path.is_file():
            return None
        return path


def make_s3_client(endpoint_url: str, access_key: str, secret_key: str):  # pragma: no cover - needs a real S3
    import boto3
    from botocore.config import Config

    return boto3.client("s3", endpoint_url=endpoint_url, aws_access_key_id=access_key, aws_secret_access_key=secret_key,
                        region_name="us-east-1", config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))
