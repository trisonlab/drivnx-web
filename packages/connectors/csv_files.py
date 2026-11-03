"""Reading a CSV where it is. Nothing is copied, and the file is never written to (Section 5)."""

import codecs
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import duckdb
import fsspec

READ_CHUNK_BYTES = 1024 * 1024
# DuckDB can't read these directly, so they are decoded into memory. A 94 MB file measured about
# 530 MB of RAM. It can read UTF-16 LE, except for characters above U+FFFF.
DECODED_IN_MEMORY_LIMIT_BYTES = 100 * 1024 * 1024
DECODED_IN_MEMORY = {
    "Windows-1252": "cp1252",
    "UTF-16 BE": "utf-16-be",
    "UTF-16 LE": "utf-16-le",
}
READ_BY_DUCKDB = {"UTF-8": "utf-8", "UTF-16 LE": "utf-16", "Latin-1": "latin-1"}
BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"
BOM_UTF32_LE = b"\xff\xfe\x00\x00"
NOT_TEXT = "This file isn't text, so it can't be read as a CSV."
NOT_CSV = "This file can't be read as a CSV."
SNIFF = """
    SELECT Delimiter, HasHeader, Columns
    FROM sniff_csv(?, encoding = ?, ignore_errors = ?, compression = 'none')
"""
NORMALISED_TYPES = {
    "BIGINT": "integer",
    "INTEGER": "integer",
    "SMALLINT": "integer",
    "TINYINT": "integer",
    "HUGEINT": "integer",
    "UBIGINT": "integer",
    "DOUBLE": "number",
    "FLOAT": "number",
    "DECIMAL": "number",
    "BOOLEAN": "boolean",
    "DATE": "date",
    "TIME": "time",
    "TIMESTAMP": "timestamp",
    "TIMESTAMP WITH TIME ZONE": "timestamp",
    "VARCHAR": "string",
}
# Bytes that are printable in Windows-1252 but control characters in Latin-1.
WINDOWS_1252_ONLY = bytes(range(0x80, 0xA0))


class CsvError(Exception):
    """This file can't be used as a CSV. The message is written for the user."""


@dataclass(frozen=True)
class CsvDetection:
    delimiter: str
    encoding: str
    has_header: bool


@dataclass(frozen=True)
class CsvColumn:
    name: str
    duckdb_type: str


@dataclass(frozen=True)
class CsvScan:
    detection: CsvDetection
    columns: tuple[CsvColumn, ...]


def normalised_type(duckdb_type: str) -> str:
    """The type vocabulary ADR 0003's contract rules use. Anything unknown stays text."""
    return NORMALISED_TYPES.get(duckdb_type.split("(")[0], "string")


def _literal(path: Path) -> str:
    """DuckDB treats * ? [ in a path as wildcards, which could open a different file."""
    return str(path).translate({ord(c): f"[{c}]" for c in "*?["})


def _chunks(path: Path) -> Iterator[bytes]:
    with path.open("rb") as file:
        while chunk := file.read(READ_CHUNK_BYTES):
            yield chunk


def _has_surrogate_pairs(path: Path) -> bool:
    """Whether a UTF-16 LE file has characters above U+FFFF, checking all of it is valid."""
    decoder = codecs.getincrementaldecoder("utf-16-le")()
    size = 0
    characters = 0
    try:
        for chunk in _chunks(path):
            size += len(chunk)
            characters += len(decoder.decode(chunk))
        decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        raise CsvError(NOT_CSV) from None
    # Every other character takes two bytes, and a surrogate pair takes four.
    return characters * 2 != size


def detect_encoding(path: Path) -> str:
    """Read the whole file once, so a bad byte late in the file can't cause a failure later.

    UTF-16 is the exception: its BOM settles the encoding, and `_readable_path` checks the rest.
    """
    with path.open("rb") as file:
        start = file.read(4)
    if start.startswith(BOM_UTF32_LE):
        raise CsvError(NOT_TEXT)
    if start.startswith(BOM_UTF16_LE):
        return "UTF-16 LE"
    if start.startswith(BOM_UTF16_BE):
        return "UTF-16 BE"

    utf8 = codecs.getincrementaldecoder("utf-8")()
    is_utf8 = True
    windows_1252_only = False
    for chunk in _chunks(path):
        if b"\x00" in chunk:
            raise CsvError(
                "This file has no readable text encoding. If it is UTF-16, save it as UTF-8."
            )
        windows_1252_only = windows_1252_only or (
            len(chunk.translate(None, WINDOWS_1252_ONLY)) != len(chunk)
        )
        if is_utf8:
            try:
                utf8.decode(chunk)
            except UnicodeDecodeError:
                is_utf8 = False
    if is_utf8:
        try:
            utf8.decode(b"", final=True)
            return "UTF-8"
        except UnicodeDecodeError:
            pass
    if start.startswith(BOM_UTF8):
        raise CsvError(
            "This file is marked as UTF-8 but has bytes that aren't. "
            "Save it as UTF-8 and connect it again."
        )
    if windows_1252_only:
        try:
            for chunk in _chunks(path):
                chunk.decode("cp1252")
        except UnicodeDecodeError:
            raise CsvError(NOT_TEXT) from None
        return "Windows-1252"
    return "Latin-1"


@contextmanager
def duckdb_connection(temp_directory: Path) -> Iterator[duckdb.DuckDBPyConnection]:
    """An in-memory DuckDB per job, spilling inside the workspace (ADR 0002 point 7)."""
    connection = duckdb.connect(
        ":memory:",
        config={
            "temp_directory": str(temp_directory),
            # Nothing may leave the machine or arrive from outside it.
            "autoinstall_known_extensions": "false",
            "autoload_known_extensions": "false",
        },
    )
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def _readable_path(
    connection: duckdb.DuckDBPyConnection, path: Path, encoding: str
) -> Iterator[tuple[str, str]]:
    """Yield the path and encoding DuckDB should use for this file."""
    if encoding in READ_BY_DUCKDB and not (
        encoding == "UTF-16 LE" and _has_surrogate_pairs(path)
    ):
        yield _literal(path), READ_BY_DUCKDB[encoding]
        return
    if path.stat().st_size > DECODED_IN_MEMORY_LIMIT_BYTES:
        raise CsvError(
            f"This {encoding} file is over 100 MB, which is too large to read in this encoding. "
            "Save it as UTF-8 and connect it again."
        )
    memory = fsspec.filesystem("memory")
    connection.register_filesystem(memory)
    name = f"{uuid.uuid4().hex}.csv"
    try:
        decoded = path.read_bytes().decode(DECODED_IN_MEMORY[encoding])
    except UnicodeDecodeError:
        raise CsvError(NOT_CSV) from None
    # A BOM in the file survives as a UTF-8 BOM, which DuckDB skips.
    memory.pipe_file(f"/{name}", decoded.encode())
    try:
        yield f"memory://{name}", "utf-8"
    finally:
        memory.rm_file(f"/{name}")


def _sniff(
    connection: duckdb.DuckDBPyConnection, readable: str, encoding: str
) -> tuple[str, bool, list[dict[str, str]]]:
    """Ask DuckDB twice, because each answer is wrong on files where the other is right.

    A strict sniff skips a header to make stray commas look like columns, and gives up on a file
    with one uneven row. A lenient one gets those delimiters right, but reads a title line above
    the header as the header, so it only overrules the strict one when they disagree.
    """
    try:
        strict = connection.execute(SNIFF, [readable, encoding, False]).fetchone()
        lenient = connection.execute(SNIFF, [readable, encoding, True]).fetchone()
    except duckdb.Error:
        raise CsvError(NOT_CSV) from None
    assert strict is not None and lenient is not None
    return strict if strict[0] == lenient[0] else lenient


def scan_csv(path: Path, temp_directory: Path) -> CsvScan:
    """Work out how the file is written and what its columns are, without reading every row."""
    if path.stat().st_size == 0:
        raise CsvError("This file is empty.")
    encoding = detect_encoding(path)
    with (
        duckdb_connection(temp_directory) as connection,
        _readable_path(connection, path, encoding) as (readable, duckdb_encoding),
    ):
        delimiter, has_header, columns = _sniff(connection, readable, duckdb_encoding)
    return CsvScan(
        CsvDetection(delimiter, encoding, has_header),
        tuple(CsvColumn(column["name"], column["type"]) for column in columns),
    )
