"""Streaming-safe file metadata and source immutability helpers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class FileMetadata:
    """A JSON-serializable snapshot of a source file."""

    path: str
    length: int
    last_write_time: str
    last_write_time_ns: int
    sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_file_metadata(
    path: str | Path,
    *,
    compute_hash: bool = False,
    hash_chunk_size: int = 1024 * 1024,
) -> FileMetadata:
    """Capture file length, write time, and optionally a chunked SHA-256 hash.

    The hash is calculated incrementally and never requires the complete file in
    memory. ``compute_hash`` is opt-in because hashing a multi-gigabyte source is
    a separate, potentially long operation from a quick metadata scan.
    """

    file_path = Path(path)
    stat = file_path.stat()
    digest: str | None = None
    if compute_hash:
        hasher = hashlib.sha256()
        with file_path.open("rb") as source:
            while chunk := source.read(hash_chunk_size):
                hasher.update(chunk)
        digest = hasher.hexdigest()

    write_time = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
    return FileMetadata(
        path=str(file_path),
        length=stat.st_size,
        last_write_time=write_time,
        last_write_time_ns=stat.st_mtime_ns,
        sha256=digest,
    )


# Descriptive alias for callers that prefer the longer name.
capture_file_metadata = get_file_metadata


def _metadata_dict(value: FileMetadata | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, FileMetadata):
        return value.to_dict()
    return dict(value)


def compare_file_metadata(
    before: FileMetadata | Mapping[str, Any],
    after: FileMetadata | Mapping[str, Any],
) -> dict[str, Any]:
    """Compare source snapshots and return a JSON-serializable result."""

    before_dict = _metadata_dict(before)
    after_dict = _metadata_dict(after)
    length_equal = before_dict.get("length") == after_dict.get("length")
    write_time_equal = (
        before_dict.get("last_write_time_ns") == after_dict.get("last_write_time_ns")
        if "last_write_time_ns" in before_dict and "last_write_time_ns" in after_dict
        else before_dict.get("last_write_time") == after_dict.get("last_write_time")
    )
    hash_checked = bool(before_dict.get("sha256") and after_dict.get("sha256"))
    hash_equal = (
        before_dict.get("sha256") == after_dict.get("sha256")
        if hash_checked
        else None
    )
    unchanged = length_equal and write_time_equal and (hash_equal is not False)
    return {
        "length_equal": length_equal,
        "last_write_time_equal": write_time_equal,
        "hash_checked": hash_checked,
        "hash_equal": hash_equal,
        "unchanged": unchanged,
        "before": before_dict,
        "after": after_dict,
    }


# Short alias used by small pipeline utilities.
compare_metadata = compare_file_metadata


def metadata_json(value: FileMetadata | Mapping[str, Any]) -> dict[str, Any]:
    """Return a plain dictionary suitable for ``json.dump``."""

    return _metadata_dict(value)


def assert_file_unchanged(
    before: FileMetadata | Mapping[str, Any],
    after: FileMetadata | Mapping[str, Any],
) -> dict[str, Any]:
    """Return the comparison or raise when a source changed."""

    comparison = compare_file_metadata(before, after)
    if not comparison["unchanged"]:
        raise RuntimeError(
            f"Source file changed during processing: {comparison['before'].get('path')}"
        )
    return comparison
