"""Reading a Parquet file where it is: its metadata (DRIVN-61) and pages of its rows (DRIVN-62).

Nothing is copied, and the file is never written to (Section 5). Its statistics hold values from
the data, so their minimums and maximums are never read (ADR 0004).
"""

import random
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


# A fast profile's sample: this many slices of this many rows, one in each equal stretch of the
# file, each starting at a row chosen with the run's seed (decided with the user on 2026-11-26).
SLICES = 8
SLICE_ROWS = 12_500

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
        shown = ", ".join(
            # Cut one character over, so a longer value can be told apart.
            f"left({_as_text(n, column_type)}, {MAX_CELL_CHARACTERS + 1})"
            for n, (_, column_type, *_) in enumerate(described)
        )
        try:
            rows = connection.execute(
                f"SELECT {shown} FROM {_listed(described)} LIMIT ? OFFSET ?",
                [readable, limit, offset],
            ).fetchall()
        except duckdb.OutOfMemoryException:
            raise ParquetError(PAGE_OUT_OF_MEMORY) from None
        except duckdb.Error:
            # Still a Parquet file, but a value on the way to this page can't be read.
            raise ParquetError(ROW_NOT_READABLE) from None
    return CsvPage([name for name, *_ in described], rows, 0)


def sample_slices(row_count: int, seed: int) -> list[tuple[int, int]] | None:
    """Where a fast profile reads, as `(first row, rows)`: `SLICES` slices, one in each equal
    stretch of the file, each starting at a row chosen with `seed`. `None` for a file no larger
    than the sample, which is read whole (AC-12, AC-30)."""
    if row_count <= SLICES * SLICE_ROWS:
        return None
    chooser = random.Random(seed)
    slices = []
    for n in range(SLICES):
        begin, end = n * row_count // SLICES, (n + 1) * row_count // SLICES
        slices.append((begin + chooser.randrange(end - begin - SLICE_ROWS + 1), SLICE_ROWS))
    return slices


def load_rows(
    connection: duckdb.DuckDBPyConnection,
    path: Path,
    table: str,
    slices: list[tuple[int, int]] | None = None,
) -> list[tuple[str, str]]:
    """Load the file's rows for a profile into `table`, as text columns `c0`, `c1`, …, as the
    preview shows them but uncut: only those in `slices` when given, else every row. Returns the
    file's columns, by name and DuckDB's type, in order. DuckDB's errors are left to the caller,
    as they can quote a row."""
    connection.execute("SET TimeZone = 'UTC'")
    readable = literal_path(path)
    described = connection.execute(f"DESCRIBE SELECT * FROM {ONE_FILE}", [readable]).fetchall()
    shown = ", ".join(
        f"{_as_text(n, column_type)} AS c{n}" for n, (_, column_type, *_) in enumerate(described)
    )
    columns = [(name, column_type) for name, column_type, *_ in described]
    if slices is None:
        connection.execute(
            f"CREATE TABLE {table} AS SELECT {shown} FROM {_listed(described)}", [readable]
        )
        return columns
    # DuckDB's own row number, which lets it skip the row groups no slice is in. With the file's
    # columns renamed, a column of the file's own with this name can't be taken for it.
    wanted = " OR ".join(
        f"file_row_number BETWEEN {int(begin)} AND {int(begin) + int(rows) - 1}"
        for begin, rows in slices
    )
    connection.execute(
        f"CREATE TABLE {table} AS SELECT {shown} FROM {_listed(described)} WHERE {wanted}",
        [readable],
    )
    return columns


def _listed(described: list[tuple]) -> str:
    """The file with its columns renamed by position, so no name from it is written into SQL."""
    named = ", ".join(f"c{n}" for n in range(len(described)))
    return f"{ONE_FILE} AS listed({named})"


def _as_text(n: int, column_type: str) -> str:
    # Binary as a database preview shows `bytea`: `\x` and every byte in lowercase hex.
    if column_type == "BLOB":
        return f"'\\x' || lower(hex(c{n}))"
    return f"CAST(c{n} AS VARCHAR)"
