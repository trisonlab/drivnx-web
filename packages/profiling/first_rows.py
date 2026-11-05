"""Measuring a table's first rows through DuckDB. It knows nothing about where they came from."""

from dataclasses import dataclass

import duckdb

FIRST_ROWS = 100_000
# Unicode's whitespace. `\s` alone only covers ASCII here.
BLANK = r"[\t-\r\x{85}\pZ]*"


@dataclass(frozen=True)
class FirstRows:
    rows_read: int
    whole_file: bool
    missing: tuple[int, ...]


def measure_first_rows(
    connection: duckdb.DuckDBPyConnection, table: str, column_count: int
) -> FirstRows:
    """Measure the table's first `column_count` columns, and only its first rows.

    Columns are used by position, so a name from the file never reaches the SQL, and one called
    `rowid` can't hide DuckDB's own. A `LIMIT` here instead ran out of memory where this doesn't.
    """
    loaded = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    aliases = ", ".join(f"c{position}" for position in range(column_count))
    counts = ", ".join(
        f"count(*) FILTER (WHERE c{position} IS NULL OR regexp_full_match(c{position}, '{BLANK}'))"
        for position in range(column_count)
    )
    missing = connection.execute(
        f"SELECT {counts} FROM {table} AS measured({aliases}) WHERE rowid < {FIRST_ROWS}"
    ).fetchone()
    return FirstRows(min(loaded, FIRST_ROWS), loaded <= FIRST_ROWS, tuple(missing))
