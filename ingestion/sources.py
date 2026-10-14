"""List and read source files from GCS, or from a local folder during development."""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

SUPPORTED_EXTENSIONS = (".xlsx", ".csv")


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
    # Reading a bucket doesn't need a project, but the client insists on one; "<none>" is what the
    # library itself uses for anonymous clients. Set GCS_PROJECT to bill or audit against a project.
    client = storage.Client(project=get_settings().gcs_project or "<none>")
    return [
        SourceFile(
            path=blob.name,
            generation=str(blob.generation),
            updated_at=blob.updated,
            read=blob.download_as_bytes,
        )
        for blob in client.list_blobs(bucket_name, prefix=prefix)
        if blob.name.lower().endswith(SUPPORTED_EXTENSIONS)
    ]
