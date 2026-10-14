import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from ingestion import sources
from ingestion.sources import month_prefixes

TEMPLATE = "sap-exports/{year}/{month:02d}/"


def test_two_digit_month_has_one_candidate():
    assert month_prefixes(TEMPLATE, 2026, 10) == ["sap-exports/2026/10/"]


def test_single_digit_month_tries_padded_then_unpadded():
    assert month_prefixes(TEMPLATE, 2027, 1) == ["sap-exports/2027/01/", "sap-exports/2027/1/"]


def test_flat_folder_template_is_used_as_is():
    assert month_prefixes("sap-exports/", 2026, 10) == ["sap-exports/"]


class FakeBlob:
    def __init__(self, name, generation, downloads=None):
        self.name, self.generation, self.updated = name, generation, datetime(2026, 9, 1, tzinfo=timezone.utc)
        self.downloads = downloads

    def download_as_bytes(self, timeout=None, retry=None):
        self.downloads.append((self.name, self.generation, timeout, threading.current_thread().name, retry))
        return f"{self.name}@{self.generation}".encode()


class FakeClient:
    made = []
    downloads = []

    def __init__(self, project=None):
        FakeClient.made.append(threading.current_thread().name)

    def list_blobs(self, bucket, prefix):
        return [FakeBlob(f"{prefix}a.csv", 11), FakeBlob(f"{prefix}b.csv", 22), FakeBlob(f"{prefix}notes.txt", 33)]

    def bucket(self, name):
        class Bucket:
            def blob(self, blob_name, generation=None):
                return FakeBlob(blob_name, generation, FakeClient.downloads)

        return Bucket()


@pytest.fixture
def fake_gcs(monkeypatch):
    from google.cloud import storage

    FakeClient.made.clear()
    FakeClient.downloads.clear()
    monkeypatch.setattr(storage, "Client", FakeClient)
    monkeypatch.setattr(sources, "_clients", threading.local())
    return FakeClient


def test_gcs_files_read_their_own_listed_version(fake_gcs):
    files = sources._list_gcs("bucket", "sap-exports/2026/09/")
    assert [(f.path, f.generation) for f in files] == [("sap-exports/2026/09/a.csv", "11"), ("sap-exports/2026/09/b.csv", "22")]
    # Each read gets its own file (no late binding of the loop variable), at the listed generation.
    assert [f.read() for f in files] == [b"sap-exports/2026/09/a.csv@11", b"sap-exports/2026/09/b.csv@22"]
    assert all(timeout == sources.DOWNLOAD_TIMEOUT for _, _, timeout, _, _ in fake_gcs.downloads)
    # The library's retries are bounded too, so one stalled download can't hold the loader up for long.
    assert all(retry._timeout == sources.DOWNLOAD_RETRY_SECONDS for *_, retry in fake_gcs.downloads)


def test_each_thread_gets_its_own_gcs_client(fake_gcs):
    files = sources._list_gcs("bucket", "p/")
    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="read") as pool:
        barrier = threading.Barrier(2)

        def read(f):
            barrier.wait()  # both threads read at the same time
            return f.read()

        assert list(pool.map(read, files)) == [b"p/a.csv@11", b"p/b.csv@22"]
    reader_threads = {name for name in fake_gcs.made if name.startswith("read")}
    assert len(reader_threads) == 2  # one client per reading thread, none shared
