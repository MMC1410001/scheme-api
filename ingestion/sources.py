"""List and read source files from GCS, or from a local folder during development."""
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

SUPPORTED_EXTENSIONS = (".xlsx", ".csv")
# (connect, read) seconds per request, and the most one download may take including the library's
# own retries: a stalled download can't hold up the loader (or Ctrl-C) for long.
DOWNLOAD_TIMEOUT = (10, 60)
DOWNLOAD_RETRY_SECONDS = 120


@dataclass
class SourceFile:
    path: str
    generation: str  # changes whenever the file is re-uploaded
    updated_at: datetime
    read: Callable[[], bytes]


def month_prefixes(template: str, year: int, month: int) -> list[str]:
    """Folder candidates for one month: zero-padded first ("…/2026/01/"), then unpadded ("…/2026/1/").

    The month folder's naming is only confirmed for two-digit months (10, 11), so both are tried.
    """
    padded = template.format(year=year, month=month)
    unpadded = re.sub(r"\{month:[^}]*\}", "{month}", template).format(year=year, month=month)
    return list(dict.fromkeys([padded, unpadded]))


def list_files(prefix: str) -> list[SourceFile]:
    settings = get_settings()
    if settings.local_data_dir:
        return _list_local(Path(settings.local_data_dir), prefix)
    return _list_gcs(settings.gcs_bucket, prefix)


def _list_local(root: Path, prefix: str) -> list[SourceFile]:
    folder = root / prefix
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            stat = path.stat()
            files.append(
                SourceFile(
                    path=path.relative_to(root).as_posix(),
                    generation=str(stat.st_mtime_ns),
                    updated_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc),
                    read=path.read_bytes,
                )
            )
    return files


def _list_gcs(bucket_name: str, prefix: str) -> list[SourceFile]:
    # Imported here so local development works without GCS credentials.
    from google.cloud import storage

    if not bucket_name:
        raise RuntimeError("GCS_BUCKET is not set (or set LOCAL_DATA_DIR for local files)")
    return [
        SourceFile(
            path=blob.name,
            generation=str(blob.generation),
            updated_at=blob.updated,
            read=lambda name=blob.name, generation=blob.generation: _download(bucket_name, name, generation),
        )
        for blob in _gcs_client().list_blobs(bucket_name, prefix=prefix)
        if blob.name.lower().endswith(SUPPORTED_EXTENSIONS)
    ]


_clients = threading.local()


def _gcs_client():
    """One client per thread: the loader downloads several files at once, and the Google client
    is not documented as safe to share between threads."""
    from google.cloud import storage

    if not hasattr(_clients, "client"):
        settings = get_settings()
        if info := settings.gcs_credentials_info:
            # The key from GCS_CREDENTIALS_JSON; the project defaults to the key's own project.
            project = {"project": settings.gcs_project} if settings.gcs_project else {}
            _clients.client = storage.Client.from_service_account_info(info, **project)
        else:
            # The library finds the key file (GOOGLE_APPLICATION_CREDENTIALS) or the server's service account.
            # Reading a bucket doesn't need a project, but the client insists on one; "<none>" is what the
            # library itself uses for anonymous clients. Set GCS_PROJECT to bill or audit against a project.
            _clients.client = storage.Client(project=settings.gcs_project or "<none>")
    return _clients.client


def _download(bucket_name: str, name: str, generation: int) -> bytes:
    # Pinned to the generation that was listed, so the bytes always match the recorded generation
    # even if the file is re-uploaded meanwhile (the next run then loads the new one).
    from google.cloud.storage.retry import DEFAULT_RETRY

    blob = _gcs_client().bucket(bucket_name).blob(name, generation=generation)
    return blob.download_as_bytes(timeout=DOWNLOAD_TIMEOUT, retry=DEFAULT_RETRY.with_timeout(DOWNLOAD_RETRY_SECONDS))
