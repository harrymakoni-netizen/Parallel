"""Object storage for captures, models, raw outputs, fields and reports.

Two backends behind one interface: a local directory (development, single
host) and any S3-compatible service. Clients only ever receive short-lived
signed URLs; storage credentials stay on the server.
"""

from __future__ import annotations

import abc
import base64
import hashlib
import hmac
import shutil
import time
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from .settings import Settings, get_settings


class StorageError(RuntimeError):
    pass


def _check_key(key: str) -> str:
    if not key or key.startswith("/") or ".." in key.split("/") or "\\" in key or "\0" in key:
        raise StorageError(f"invalid storage key {key!r}")
    return key


class Storage(abc.ABC):
    @abc.abstractmethod
    def put_file(self, key: str, path: Path, media_type: str = "application/octet-stream") -> None: ...

    @abc.abstractmethod
    def put_bytes(self, key: str, data: bytes, media_type: str = "application/octet-stream") -> None: ...

    @abc.abstractmethod
    def put_stream(self, key: str, stream: BinaryIO, media_type: str, max_bytes: int) -> tuple[int, str]:
        """Store a stream; returns (size, sha256). Raises StorageError above max_bytes."""

    @abc.abstractmethod
    def get_to_path(self, key: str, dest: Path) -> Path: ...

    @abc.abstractmethod
    def get_bytes(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def signed_url(self, key: str, filename: str | None = None, seconds: int | None = None) -> str: ...

    @abc.abstractmethod
    def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix`` (which must end with '/'); returns the count."""

    @abc.abstractmethod
    def size(self, key: str) -> int: ...

    @abc.abstractmethod
    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        """Every stored object key under ``prefix``, in sorted order (used by backups)."""


class LocalStorage(Storage):
    def __init__(self, root: Path, settings: Settings):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings

    def path(self, key: str) -> Path:
        p = (self.root / _check_key(key)).resolve()
        if not str(p).startswith(str(self.root.resolve()) + "/"):
            raise StorageError("key escapes storage root")
        return p

    def put_file(self, key: str, path: Path, media_type: str = "application/octet-stream") -> None:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(dest)  # atomic publication

    def put_bytes(self, key: str, data: bytes, media_type: str = "application/octet-stream") -> None:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        tmp.write_bytes(data)
        tmp.replace(dest)

    def put_stream(self, key: str, stream: BinaryIO, media_type: str, max_bytes: int) -> tuple[int, str]:
        dest = self.path(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        h = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as f:
                while chunk := stream.read(1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise StorageError(f"upload exceeds the {max_bytes} byte limit")
                    h.update(chunk)
                    f.write(chunk)
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return size, h.hexdigest()

    def get_to_path(self, key: str, dest: Path) -> Path:
        src = self.path(key)
        if not src.exists():
            raise StorageError(f"object {key} not found")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    def get_bytes(self, key: str) -> bytes:
        p = self.path(key)
        if not p.exists():
            raise StorageError(f"object {key} not found")
        return p.read_bytes()

    def exists(self, key: str) -> bool:
        return self.path(key).exists()

    def delete(self, key: str) -> None:
        self.path(key).unlink(missing_ok=True)

    def delete_prefix(self, prefix: str) -> int:
        if not prefix.endswith("/"):
            raise StorageError("prefix must end with '/'")
        base = self.path(prefix.rstrip("/"))
        if not base.exists():
            return 0
        n = sum(1 for p in base.rglob("*") if p.is_file())
        shutil.rmtree(base)
        return n

    def size(self, key: str) -> int:
        return self.path(key).stat().st_size

    def iter_keys(self, prefix: str = "") -> Iterator[str]:
        root = self.root.resolve()
        for p in sorted(root.rglob("*")):
            if p.is_file() and not p.name.endswith(".part"):  # skip unpublished partial writes
                key = p.relative_to(root).as_posix()
                if key.startswith(prefix):
                    yield key

    def signed_url(self, key: str, filename: str | None = None, seconds: int | None = None) -> str:
        exp = int(time.time()) + (seconds or self.settings.signed_url_seconds)
        sig = sign_blob(self.settings.secret_key, key, exp)
        fn = f"&filename={quote(filename)}" if filename else ""
        return f"{self.settings.public_base_url}/api/v1/blobs?key={quote(key)}&exp={exp}&sig={sig}{fn}"

    def iter_file(self, key: str, chunk: int = 1 << 20) -> Iterator[bytes]:
        with open(self.path(key), "rb") as f:
            while b := f.read(chunk):
                yield b


def sign_blob(secret: str, key: str, exp: int) -> str:
    mac = hmac.new(secret.encode(), f"{key}\n{exp}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def verify_blob(secret: str, key: str, exp: int, sig: str) -> bool:
    if exp < time.time():
        return False
    return hmac.compare_digest(sign_blob(secret, key, exp), sig)


class S3Storage(Storage):
    """Any S3-compatible object store. Tested against an S3 API emulator (moto); see test_storage_s3.py."""

    def __init__(self, settings: Settings):
        import boto3

        self.settings = settings
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_id,
            aws_secret_access_key=settings.s3_secret_access_key,
        )

    def put_file(self, key, path, media_type="application/octet-stream"):
        self.client.upload_file(
            str(path), self.bucket, _check_key(key), ExtraArgs={"ContentType": media_type}
        )

    def put_bytes(self, key, data, media_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=_check_key(key), Body=data, ContentType=media_type)

    def put_stream(self, key, stream, media_type, max_bytes):
        import tempfile

        with tempfile.NamedTemporaryFile() as tmp:
            h, size = hashlib.sha256(), 0
            while chunk := stream.read(1 << 20):
                size += len(chunk)
                if size > max_bytes:
                    raise StorageError(f"upload exceeds the {max_bytes} byte limit")
                h.update(chunk)
                tmp.write(chunk)
            tmp.flush()
            self.put_file(key, Path(tmp.name), media_type)
        return size, h.hexdigest()

    def get_to_path(self, key, dest):
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(self.bucket, _check_key(key), str(dest))
        return dest

    def get_bytes(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=_check_key(key))["Body"].read()

    def exists(self, key):
        from botocore.exceptions import ClientError

        try:
            self.client.head_object(Bucket=self.bucket, Key=_check_key(key))
            return True
        except ClientError as e:  # only "not found" means absent; credentials or network errors propagate
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=_check_key(key))

    def delete_prefix(self, prefix):
        if not prefix.endswith("/"):
            raise StorageError("prefix must end with '/'")
        n = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=_check_key(prefix)):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                self.client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
                n += len(keys)
        return n

    def size(self, key):
        return int(self.client.head_object(Bucket=self.bucket, Key=_check_key(key))["ContentLength"])

    def iter_keys(self, prefix=""):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            yield from (o["Key"] for o in page.get("Contents", []))  # S3 lists keys in sorted order

    def signed_url(self, key, filename=None, seconds=None):
        params = {"Bucket": self.bucket, "Key": _check_key(key)}
        if filename:
            safe = "".join(c for c in filename if c.isprintable() and c not in '"\\;') or "download"
            params["ResponseContentDisposition"] = f'attachment; filename="{safe}"'
        return self.client.generate_presigned_url(
            "get_object", Params=params, ExpiresIn=seconds or self.settings.signed_url_seconds
        )


@lru_cache
def get_storage() -> Storage:
    s = get_settings()
    if s.storage_backend == "s3":
        return S3Storage(s)
    return LocalStorage(s.storage_root, s)
