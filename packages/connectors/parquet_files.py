"""Reading a Parquet file where it is: its metadata (DRIVN-61) and pages of its rows (DRIVN-62).

Nothing is copied, and the file is never written to (Section 5). Its statistics hold values from
the data, so their minimums and maximums are never read (ADR 0004).
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from connectors.csv_files import (
    MAX_CELL_CHARACTERS,
    ROW_NOT_READABLE,
    CsvColumn,
    CsvPage,
    duckdb_connection,
    literal_path,
)

MAGIC = b"PAR1"
# A file whose footer is encrypted starts and ends with this instead.
ENCRYPTED_MAGIC = b"PARE"
ENCRYPTED = "This Parquet file is encrypted, so DrivnX can't read it."
NOT_PARQUET = "This file isn't a Parquet file, so it can't be read as one."
PAGE_OUT_OF_MEMORY = "DrivnX ran out of memory showing this page."


class ParquetError(Exception):
    """This file can't be read as a Parquet file. The message is written for the user."""


@dataclass(frozen=True)
class ParquetMetadata:
    row_count: int
    row_group_count: int
    created_by: str | None
    columns: tuple[CsvColumn, ...]


# The file on its own: a folder named like `id=x` would otherwise add a column, or replace one.
# Partitioned datasets are out of scope (DRIVN-60).
ONE_FILE = "read_parquet(?, hive_partitioning = false)"


# The magic at each end, and the footer's 4-byte length before the last one.
SMALLEST = 3 * len(MAGIC)


def _magic(path: Path) -> bytes:
    with path.open("rb") as file:
        return file.read(len(MAGIC))


def is_parquet(path: Path) -> bool:
    """A Parquet file starts and ends with the same magic bytes, whatever it is named (AC-29).

    Both ends, as `PAR1` and `PARE` are text a CSV header can start with. A file cut short has
    lost its last ones, and is read as what's left: binary that is neither Parquet nor text.
    """
    with path.open("rb") as file:
        head = file.read(len(MAGIC))
        if head not in (MAGIC, ENCRYPTED_MAGIC) or file.seek(0, 2) < SMALLEST:
            return False
        file.seek(-len(MAGIC), 2)
        return file.read(len(MAGIC)) == head


def read_metadata(path: Path, temp_directory: Path) -> ParquetMetadata:
    """What the file's footer says, without reading a row (AC-07)."""
    if _magic(path) == ENCRYPTED_MAGIC:
        raise ParquetError(ENCRYPTED)
    readable = literal_path(path)
    with duckdb_connection(temp_directory) as connection:
        try:
            row_count, row_group_count, created_by = connection.execute(
                "SELECT num_rows, num_row_groups, created_by FROM parquet_file_metadata(?)",
                [readable],
            ).fetchone()
            columns = connection.execute(
                f"SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM {ONE_FILE})",
                [readable],
            ).fetchall()
        except duckdb.Error:
            # A footer that's missing or can't be parsed. DuckDB's message names the path.
            raise ParquetError(NOT_PARQUET) from None
    return ParquetMetadata(
        row_count=row_count,
        row_group_count=row_group_count,
        created_by=created_by,
        columns=tuple(CsvColumn(name, duckdb_type) for name, duckdb_type in columns),
    )


def read_page(path: Path, offset: int, limit: int, temp_directory: Path) -> CsvPage:
    """One page of rows as text, in the file's order. Nothing is kept once this returns (AC-10)."""
    # Opened here first, so a file DrivnX may not read says so, not that it isn't Parquet.
    _magic(path)
    with duckdb_connection(temp_directory) as connection:
        # A moment as the file stores it, in UTC, not in this computer's time zone.
        connection.execute("SET TimeZone = 'UTC'")
        readable = literal_path(path)
        # DuckDB's messages name the path, and a row's can quote its values.
        try:
            described = connection.execute(
                f"DESCRIBE SELECT * FROM {ONE_FILE}", [readable]
            ).fetchall()
        except duckdb.Error:
            # Its footer can't be read: the file changed since it was connected.
            raise ParquetError(NOT_PARQUET) from None
        # By position, so no name from the file is written into the SQL.
        named = ", ".join(f"c{n}" for n in range(len(described)))
        shown = ", ".join(
            # Cut one character over, so a longer value can be told apart.
            f"left({_as_text(n, column_type)}, {MAX_CELL_CHARACTERS + 1})"
            for n, (_, column_type, *_) in enumerate(described)
        )
        try:
            rows = connection.execute(
                f"SELECT {shown} FROM {ONE_FILE} AS listed({named}) LIMIT ? OFFSET ?",
                [readable, limit, offset],
            ).fetchall()
        except duckdb.OutOfMemoryException:
            raise ParquetError(PAGE_OUT_OF_MEMORY) from None
        except duckdb.Error:
            # Still a Parquet file, but a value on the way to this page can't be read.
            raise ParquetError(ROW_NOT_READABLE) from None
    return CsvPage([name for name, *_ in described], rows, 0)


def _as_text(n: int, column_type: str) -> str:
    # Binary as a database preview shows `bytea`: `\x` and every byte in lowercase hex.
    if column_type == "BLOB":
        return f"'\\x' || lower(hex(c{n}))"
    return f"CAST(c{n} AS VARCHAR)"
