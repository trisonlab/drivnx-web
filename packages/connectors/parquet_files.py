"""Reading a Parquet file where it is, starting from its metadata (DRIVN-60, DRIVN-61).

Nothing is copied, and the file is never written to (Section 5). Its statistics hold values from
the data, so their minimums and maximums are never read (ADR 0004).
"""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from connectors.csv_files import CsvColumn, duckdb_connection, literal_path

MAGIC = b"PAR1"
# A file whose footer is encrypted starts and ends with this instead.
ENCRYPTED_MAGIC = b"PARE"
ENCRYPTED = "This Parquet file is encrypted, so DrivnX can't read it."
NOT_PARQUET = "This file isn't a Parquet file, so it can't be read as one."


class ParquetError(Exception):
    """This file can't be read as a Parquet file. The message is written for the user."""


@dataclass(frozen=True)
class ParquetMetadata:
    row_count: int
    row_group_count: int
    created_by: str | None
    columns: tuple[CsvColumn, ...]


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
                "SELECT column_name, column_type FROM (DESCRIBE SELECT * FROM read_parquet(?))",
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
