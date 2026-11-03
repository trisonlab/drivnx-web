"""Reading a CSV where it is. Nothing is copied, and the file is never written to (Section 5)."""

import codecs
import os
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

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
ROW_NOT_READABLE = "A row in this file can't be read, so this page can't be shown."
NOT_REGULAR_FILE = "This path is not a regular file, so it can't be read as a CSV."
# DuckDB treats # as a comment if it fits the sample, which removes rows from the file's table. It
# can only be turned off, so a real comment line is shown as a row.
SNIFF = """
    SELECT Delimiter, HasHeader, Columns
    FROM sniff_csv(?, encoding = ?, compression = 'none', comment = '', {options})
"""
QUOTE_AND_SKIP = """
    SELECT Quote, SkipRows
    FROM sniff_csv(?, encoding = ?, delim = ?, header = ?, compression = 'none', comment = '')
"""
MAX_CELL_CHARACTERS = 1000
# Not parallel: its reader can invent a row where a read buffer ends inside a quoted value, can't
# pad rows in a file with line breaks in quotes, and made memory grow page after page. Cells are cut
# here, one character over so a longer one can be told apart, to keep huge ones out of Python.
READ_PAGE = f"""
    SELECT left(COLUMNS(*), {MAX_CELL_CHARACTERS + 1})
    FROM read_csv(
        ?,
        encoding = ?,
        delim = ?,
        header = ?,
        all_varchar = true,
        compression = 'none',
        comment = '',
        parallel = false,
        {{options}}
    )
    LIMIT ? OFFSET ?
"""
NO_QUOTE_SEEN = "(empty)"
STANDARD_QUOTE = "quote = '\"', escape = '\"'"
PADDED = "null_padding = true"
LAX = "strict_mode = false"
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


@dataclass(frozen=True)
class CsvPage:
    columns: list[str]
    rows: list[tuple[str | None, ...]]
    skipped_lines: int


def normalised_type(duckdb_type: str) -> str:
    """The type vocabulary ADR 0003's contract rules use. Anything unknown stays text."""
    return NORMALISED_TYPES.get(duckdb_type.split("(")[0], "string")


def _literal(path: Path) -> str:
    """DuckDB treats * ? [ in a path as wildcards, which could open a different file."""
    return str(path).translate({ord(c): f"[{c}]" for c in "*?["})


def _open_regular(path: Path) -> BinaryIO:
    """Open a regular file. A FIFO blocks whoever opens it, and a file can change after a check."""
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise CsvError(NOT_REGULAR_FILE)
    return os.fdopen(descriptor, "rb")


def _chunks(path: Path) -> Iterator[bytes]:
    with _open_regular(path) as file:
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
    with _open_regular(path) as file:
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
        # Not accepted in the config above. A slow read would draw on the user's terminal.
        connection.execute("SET enable_progress_bar = false")
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
    # Sniffing on several threads through a Python filesystem keeps using more memory, page by page.
    connection.execute("SET threads = 1")
    memory = fsspec.filesystem("memory")
    connection.register_filesystem(memory)
    name = f"{uuid.uuid4().hex}.csv"
    try:
        with _open_regular(path) as file:
            decoded = file.read().decode(DECODED_IN_MEMORY[encoding])
    except UnicodeDecodeError:
        raise CsvError(NOT_CSV) from None
    # A BOM in the file survives as a UTF-8 BOM, which DuckDB skips.
    memory.pipe_file(f"/{name}", decoded.encode())
    try:
        yield f"memory://{name}", "utf-8"
    finally:
        memory.rm_file(f"/{name}")


def _sniff_with(
    connection: duckdb.DuckDBPyConnection, readable: str, encoding: str, options: str
) -> tuple[str, bool, list[dict[str, str]]] | None:
    try:
        return connection.execute(
            SNIFF.format(options=options), [readable, encoding]
        ).fetchone()
    except duckdb.Error:
        return None


def _sniff(
    connection: duckdb.DuckDBPyConnection, readable: str, encoding: str
) -> tuple[str, bool, list[dict[str, str]]]:
    """Ask DuckDB twice, because each answer is wrong on files where the other is right.

    A strict sniff skips a header to make stray commas look like columns, and gives up on a file
    with one uneven row. A lenient one gets those delimiters right, but reads a title line above
    the header as the header, so it only overrules the strict one when they disagree. Only the lax
    sniff can read a file with mixed line endings, so it comes last.
    """
    strict = _sniff_with(connection, readable, encoding, "ignore_errors = false")
    lenient = _sniff_with(connection, readable, encoding, "ignore_errors = true")
    if strict is not None and lenient is not None:
        return strict if strict[0] == lenient[0] else lenient
    found = (
        strict
        or lenient
        or _sniff_with(connection, readable, encoding, "strict_mode = false")
    )
    if found is None:
        raise CsvError(NOT_CSV)
    return found


def _refuse_empty(path: Path) -> None:
    with _open_regular(path) as file:
        if not file.read(1):
            raise CsvError("This file is empty.")


def scan_csv(path: Path, temp_directory: Path) -> CsvScan:
    """Work out how the file is written and what its columns are, without reading every row."""
    _refuse_empty(path)
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


def _ways_to_read(
    connection: duckdb.DuckDBPyConnection, file_settings: list[str | bool]
) -> tuple[list[str], int]:
    """The read options to try in order, and how many lines they leave out above the table.

    The sniff only samples the start of the file. If it saw no quotes, the standard quote is tried
    first, so that a quoted value further on is still read as one. Padding gives short rows blanks
    and an extra field its own column. An explicit skip stops DuckDB working out a file with an
    extra field, so it's only used when the sniff found a number to keep the same on every page.
    Lax reading is the only way to read mixed line endings, but it drops an extra field past the
    sample without saying so. It's only for files the sniff can't read, and it mustn't also pad.
    """
    try:
        found = connection.execute(QUOTE_AND_SKIP, file_settings).fetchone()
    except duckdb.Error:
        return [PADDED, LAX], 0
    assert found is not None
    quote, skipped_lines = found
    skip = f"skip = {skipped_lines:d}"
    as_detected = f"{skip}, {PADDED}"
    if quote != NO_QUOTE_SEEN:
        return [as_detected], skipped_lines
    return [
        f"{skip}, {STANDARD_QUOTE}",
        f"{skip}, {STANDARD_QUOTE}, {PADDED}",
        as_detected,
    ], skipped_lines


def read_page(
    path: Path,
    encoding: str,
    delimiter: str,
    has_header: bool,
    offset: int,
    limit: int,
    temp_directory: Path,
) -> CsvPage:
    """Read one page of rows as the file's own text. Nothing is kept once this returns.

    A row inside the table is never skipped: a page that hid one would look complete, so a file no
    way of reading can manage is an error. Lines above the table can be, and `skipped_lines` says
    how many.
    """
    _refuse_empty(path)
    with (
        duckdb_connection(temp_directory) as connection,
        _readable_path(connection, path, encoding) as (readable, duckdb_encoding),
    ):
        file_settings: list[str | bool] = [
            readable,
            duckdb_encoding,
            delimiter,
            has_header,
        ]
        ways_to_read, skipped_lines = _ways_to_read(connection, file_settings)
        for options in ways_to_read:
            try:
                result = connection.execute(
                    READ_PAGE.format(options=options), [*file_settings, limit, offset]
                )
                rows = result.fetchall()
            except duckdb.Error:
                # DuckDB's message quotes the row, so it must not travel any further.
                continue
            return CsvPage(
                [column[0] for column in result.description], rows, skipped_lines
            )
    raise CsvError(ROW_NOT_READABLE)
