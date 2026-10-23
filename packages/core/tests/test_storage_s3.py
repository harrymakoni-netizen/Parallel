"""S3 storage backend against a local S3 API emulator (moto server): the same operations the
local backend is tested for, plus presigned downloads and strict existence checks."""

from __future__ import annotations

import hashlib
import io
import socket

import boto3
import httpx
import pytest
from botocore.exceptions import EndpointConnectionError

from parallel_core.settings import Settings
from parallel_core.storage import S3Storage, StorageError


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def endpoint():
    from moto.server import ThreadedMotoServer

    port = _free_port()
    server = ThreadedMotoServer(ip_address="127.0.0.1", port=port, verbose=False)
    server.start()
    yield f"http://127.0.0.1:{port}"
    server.stop()


def _settings(endpoint: str, key: str = "test") -> Settings:
    return Settings(
        storage_backend="s3",
        s3_endpoint_url=endpoint,
        s3_bucket="parallel-test",
        s3_access_key_id=key,
        s3_secret_access_key="test",
    )


@pytest.fixture(scope="module")
def store(endpoint):
    s = _settings(endpoint)
    boto3.client(
        "s3", endpoint_url=endpoint, region_name=s.s3_region, aws_access_key_id="test", aws_secret_access_key="test"
    ).create_bucket(Bucket=s.s3_bucket)
    return S3Storage(s)


def test_round_trip_and_hash(store, tmp_path):
    data = b"captured frame bytes" * 1000
    size, digest = store.put_stream("projects/p1/a.bin", io.BytesIO(data), "application/octet-stream", 10**6)
    assert size == len(data) and digest == hashlib.sha256(data).hexdigest()
    assert store.exists("projects/p1/a.bin") and store.size("projects/p1/a.bin") == len(data)
    assert store.get_bytes("projects/p1/a.bin") == data
    out = store.get_to_path("projects/p1/a.bin", tmp_path / "x" / "a.bin")
    assert out.read_bytes() == data
    assert not store.exists("projects/p1/missing.bin")


def test_upload_limit_is_enforced(store):
    with pytest.raises(StorageError):
        store.put_stream("projects/p1/big.bin", io.BytesIO(b"x" * 2048), "application/octet-stream", 1024)
    assert not store.exists("projects/p1/big.bin")


def test_signed_url_downloads_with_safe_filename(store):
    store.put_bytes("projects/p1/report.pdf", b"%PDF-1.7 test", "application/pdf")
    url = store.signed_url("projects/p1/report.pdf", filename='re"port;.pdf', seconds=60)
    r = httpx.get(url, timeout=10)
    assert r.status_code == 200 and r.content == b"%PDF-1.7 test"
    assert r.headers["content-disposition"] == 'attachment; filename="report.pdf"'


def test_delete_prefix_only_removes_that_prefix(store):
    for k in ("demo/a/1", "demo/a/2", "demo/b/1"):
        store.put_bytes(k, b"1")
    with pytest.raises(StorageError):
        store.delete_prefix("demo/a")  # a prefix must end with '/'
    assert list(store.iter_keys("demo/")) == ["demo/a/1", "demo/a/2", "demo/b/1"]
    assert store.delete_prefix("demo/a/") == 2
    assert not store.exists("demo/a/1") and store.exists("demo/b/1")


def test_rejects_unsafe_keys(store):
    with pytest.raises(StorageError):
        store.put_bytes("../escape", b"x")


def test_existence_check_does_not_hide_connection_errors(monkeypatch):
    # an unreachable store must fail loudly, not report every object as missing
    monkeypatch.setenv("AWS_MAX_ATTEMPTS", "1")
    unreachable = S3Storage(_settings(f"http://127.0.0.1:{_free_port()}"))
    with pytest.raises(EndpointConnectionError):
        unreachable.exists("projects/p1/a.bin")
