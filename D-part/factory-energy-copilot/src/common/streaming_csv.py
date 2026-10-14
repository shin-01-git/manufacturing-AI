"""Bounded, single-pass CSV reading for large source files."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import csv
import io
from pathlib import Path
from typing import Callable, Iterable, Iterator, Any


_ENCODING_CANDIDATES = ("utf-8-sig", "utf-8", "cp949", "euc-kr")
_DELIMITER_CANDIDATES = (",", ";", "\t", "|")


@dataclass
class CSVScanResult:
    path: str
    encoding: str
    delimiter: str
    header: list[str]
    column_count: int
    head_samples: list[dict[str, str]]
    tail_samples: list[dict[str, str]]
    csv_record_count: int
    physical_row_count: int
    physical_rows_scanned: int
    partial: bool
    max_rows: int | None
    sample_size: int
    tail_size: int
    errors: list[str] = field(default_factory=list)
    error_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "encoding": self.encoding,
            "delimiter": self.delimiter,
            "header": self.header,
            "column_count": self.column_count,
            "head_samples": self.head_samples,
            "tail_samples": self.tail_samples,
            "csv_record_count": self.csv_record_count,
            "physical_row_count": self.physical_row_count,
            "physical_rows_scanned": self.physical_rows_scanned,
            "partial": self.partial,
            "max_rows": self.max_rows,
            "sample_size": self.sample_size,
            "tail_size": self.tail_size,
            "errors": self.errors,
            "error_count": self.error_count,
        }


class _CountingTextIO:
    """Count physical text lines consumed by csv.reader."""

    def __init__(self, stream: io.TextIOBase) -> None:
        self._stream = stream
        self.physical_lines = 0

    def __iter__(self) -> "_CountingTextIO":
        return self

    def __next__(self) -> str:
        line = next(self._stream)
        self.physical_lines += 1
        return line


def _decode_prefix(data: bytes) -> tuple[str, str]:
    for encoding in _ENCODING_CANDIDATES:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return encoding, text
    raise UnicodeError("CSV prefix could not be decoded as UTF-8 or Korean legacy encoding")


def detect_encoding(path: str | Path, *, sample_bytes: int = 64 * 1024) -> str:
    """Detect a safe encoding from a bounded binary prefix."""

    with Path(path).open("rb") as source:
        prefix = source.read(sample_bytes)
    encoding, _ = _decode_prefix(prefix)
    return encoding


def detect_delimiter(
    path: str | Path,
    *,
    encoding: str | None = None,
    sample_bytes: int = 64 * 1024,
) -> str:
    """Detect a CSV delimiter from a bounded prefix, defaulting to comma."""

    actual_encoding = encoding or detect_encoding(path, sample_bytes=sample_bytes)
    with Path(path).open("rb") as source:
        prefix = source.read(sample_bytes)
    _, text = _decode_prefix(prefix)
    try:
        dialect = csv.Sniffer().sniff(text, delimiters="".join(_DELIMITER_CANDIDATES))
        return dialect.delimiter
    except csv.Error:
        first_line = next((line for line in text.splitlines() if line.strip()), "")
        counts = {candidate: first_line.count(candidate) for candidate in _DELIMITER_CANDIDATES}
        delimiter, count = max(counts.items(), key=lambda item: item[1])
        return delimiter if count else ","


def _open_csv(
    path: str | Path,
    *,
    encoding: str | None,
    delimiter: str | None,
    buffer_size: int,
) -> tuple[io.BufferedReader, io.TextIOWrapper, _CountingTextIO, str, str]:
    file_path = Path(path)
    actual_encoding = encoding or detect_encoding(file_path)
    actual_delimiter = delimiter or detect_delimiter(file_path, encoding=actual_encoding)
    binary = file_path.open("rb", buffering=0)
    buffered = io.BufferedReader(binary, buffer_size=buffer_size)
    text = io.TextIOWrapper(buffered, encoding=actual_encoding, newline="")
    return buffered, text, _CountingTextIO(text), actual_encoding, actual_delimiter


class StreamingCSVReader:
    """Read a CSV once while retaining only bounded samples."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_rows: int | None = None,
        sample_size: int = 5,
        tail_size: int = 5,
        encoding: str | None = None,
        delimiter: str | None = None,
        chunk_size: int = 1024 * 1024,
    ) -> None:
        if max_rows is not None and max_rows < 0:
            raise ValueError("max_rows must be non-negative or None")
        if sample_size < 0 or tail_size < 0:
            raise ValueError("sample_size and tail_size must be non-negative")
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self.path = Path(path)
        self.max_rows = max_rows
        self.sample_size = sample_size
        self.tail_size = tail_size
        self.encoding = encoding
        self.delimiter = delimiter
        self.chunk_size = chunk_size

    def scan(
        self,
        row_callback: Callable[[dict[str, str]], None] | None = None,
    ) -> CSVScanResult:
        """Scan the file once and optionally process each valid row immediately."""

        buffered, text, counting_text, encoding, delimiter = _open_csv(
            self.path,
            encoding=self.encoding,
            delimiter=self.delimiter,
            buffer_size=self.chunk_size,
        )
        try:
            reader = csv.reader(counting_text, delimiter=delimiter, strict=False)
            try:
                raw_header = next(reader)
            except StopIteration as exc:
                raise ValueError(f"CSV is empty: {self.path}") from exc
            header = list(raw_header)
            if header and header[0].startswith("\ufeff"):
                header[0] = header[0].lstrip("\ufeff")
            if not header or any(not name for name in header):
                raise ValueError(f"CSV header contains an empty column: {self.path}")

            errors: list[str] = []
            error_count = 0
            duplicate_names = len(set(header)) != len(header)
            if duplicate_names:
                errors.append("duplicate header names")
                error_count += 1
            head: list[dict[str, str]] = []
            tail: deque[dict[str, str]] = deque(maxlen=self.tail_size)
            processed = 0
            consumed = 0
            partial = False
            while True:
                try:
                    row = next(reader)
                except StopIteration:
                    break
                consumed += 1
                if self.max_rows is not None and consumed > self.max_rows:
                    partial = True
                    break
                processed += 1
                if len(row) != len(header):
                    error_count += 1
                    message = (
                        f"record {processed}: expected {len(header)} columns, got {len(row)}"
                    )
                    if len(errors) < 20:
                        errors.append(message)
                    continue
                record = dict(zip(header, row))
                if len(head) < self.sample_size:
                    head.append(record)
                if self.tail_size:
                    tail.append(record)
                if row_callback is not None:
                    row_callback(record)

            # csv.reader.line_num is the physical CSV-line count consumed,
            # including any quoted line breaks. Header lines are excluded.
            header_physical_lines = 1
            physical_rows_scanned = max(counting_text.physical_lines - header_physical_lines, 0)
            physical_row_count = physical_rows_scanned
            return CSVScanResult(
                path=str(self.path),
                encoding=encoding,
                delimiter=delimiter,
                header=header,
                column_count=len(header),
                head_samples=head,
                tail_samples=list(tail),
                csv_record_count=processed,
                physical_row_count=physical_row_count,
                physical_rows_scanned=physical_rows_scanned,
                partial=partial,
                max_rows=self.max_rows,
                sample_size=self.sample_size,
                tail_size=self.tail_size,
                errors=errors,
                error_count=error_count,
            )
        finally:
            text.close()
            # TextIOWrapper.close() closes the buffered stream and binary stream.
            del buffered


def scan_csv(
    path: str | Path,
    *,
    max_rows: int | None = None,
    sample_size: int = 5,
    tail_size: int = 5,
    encoding: str | None = None,
    delimiter: str | None = None,
    chunk_size: int = 1024 * 1024,
    row_callback: Callable[[dict[str, str]], None] | None = None,
) -> CSVScanResult:
    """Convenience wrapper around :class:`StreamingCSVReader`."""

    return StreamingCSVReader(
        path,
        max_rows=max_rows,
        sample_size=sample_size,
        tail_size=tail_size,
        encoding=encoding,
        delimiter=delimiter,
        chunk_size=chunk_size,
    ).scan(row_callback=row_callback)


def iter_csv_rows(
    path: str | Path,
    *,
    max_rows: int | None = None,
    encoding: str | None = None,
    delimiter: str | None = None,
    chunk_size: int = 1024 * 1024,
) -> Iterator[dict[str, str]]:
    """Yield valid CSV records without materializing the file."""

    rows: list[dict[str, str]] = []

    def collect(record: dict[str, str]) -> None:
        rows.append(record)

    # This helper is intentionally implemented as a generator over a bounded
    # one-record callback queue, not as a list-backed full-file API.
    buffered, text, counting_text, actual_encoding, actual_delimiter = _open_csv(
        path,
        encoding=encoding,
        delimiter=delimiter,
        buffer_size=chunk_size,
    )
    del actual_encoding, actual_delimiter
    try:
        reader = csv.reader(counting_text, delimiter=delimiter or detect_delimiter(path, encoding=encoding), strict=False)
        header = next(reader)
        if header and header[0].startswith("\ufeff"):
            header[0] = header[0].lstrip("\ufeff")
        yielded = 0
        for row in reader:
            if max_rows is not None and yielded >= max_rows:
                break
            if len(row) != len(header):
                continue
            yielded += 1
            yield dict(zip(header, row))
    finally:
        text.close()
        del buffered, rows, collect
