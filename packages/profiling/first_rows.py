"""Measuring a table's first rows, or every row, through DuckDB. It knows nothing about where
they came from."""

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

import duckdb

FIRST_ROWS = 100_000
# Unicode's whitespace. `\s` alone only covers ASCII here.
BLANK = r"[\t-\r\x{85}\pZ]*"
# Whether a value in `cell` fits a column's type. Text always does, so isn't here. An integer has
# to be a whole number, as a cast alone turns 1.5 into 2. Comparing it with that cast would round
# the same large number two ways.
FITS = {
    "integer": (
        "TRY_CAST(cell AS HUGEINT) IS NOT NULL"
        " AND floor(TRY_CAST(cell AS DOUBLE)) = TRY_CAST(cell AS DOUBLE)"
    ),
    "number": "TRY_CAST(cell AS DOUBLE) IS NOT NULL",
    "boolean": "TRY_CAST(cell AS BOOLEAN) IS NOT NULL",
    "date": "TRY_CAST(cell AS DATE) IS NOT NULL",
    "time": "TRY_CAST(cell AS TIME) IS NOT NULL",
    "timestamp": "TRY_CAST(cell AS TIMESTAMP) IS NOT NULL",
}

PATTERNS_KEPT = 10
PATTERN_CHARACTERS = 40
# Far enough to fill a pattern, as a huge cell would take time to mask for nothing.
CHARACTERS_MASKED = 100
# A letter is anything not known to be something else, as DuckDB's tables don't have the newest ones.
# A combining mark goes with its letter, so `é` gives one `A` however it's encoded. Any number
# becomes 9, as a `½` left alone would be the value itself (ADR 0004).
MASKED = (
    rf"regexp_replace(regexp_replace(left(cell, {CHARACTERS_MASKED}),"
    r" '[^\pM\pN\pP\pS\pZ\pC]\pM*', 'A', 'g'), '\pN', '9', 'g')"
)


@dataclass(frozen=True)
class MaskedPattern:
    pattern: str
    count: int
    cut: bool


@dataclass(frozen=True)
class ColumnMetrics:
    missing: int
    missing_percent: float | None
    distinct: int
    distinct_percent: float | None
    not_fitting: int
    shortest: int | None
    longest: int | None
    average: float | None
    patterns: tuple[MaskedPattern, ...]

    def saved_metrics(self) -> dict[str, Any]:
        """What's saved for this column. With nothing to divide by or measure, a metric isn't."""
        metrics = {
            "missing_count": self.missing,
            "missing_percent": self.missing_percent,
            "distinct_count": self.distinct,
            "distinct_percent": self.distinct_percent,
            "not_fitting_count": self.not_fitting,
            "shortest_length": self.shortest,
            "longest_length": self.longest,
            "average_length": self.average,
            "masked_patterns": [asdict(found) for found in self.patterns] or None,
        }
        return {name: value for name, value in metrics.items() if value is not None}


@dataclass(frozen=True)
class MeasuredRows:
    rows_read: int
    whole_file: bool
    columns: tuple[ColumnMetrics, ...]
    # Rows that are an exact copy of an earlier one, counted only when every row is measured.
    duplicate_rows: int | None = None


def _percent(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 2) if whole else None


def _cells(column: str, table: str, every_row: bool = False) -> str:
    """A column's first rows, or every row, as `cell`, with missing values as NULL."""
    first_rows_only = "" if every_row else f"WHERE rowid < {FIRST_ROWS}"
    return f"""
        WITH cells AS (
            SELECT CASE
                WHEN {column} IS NULL OR regexp_full_match({column}, '{BLANK}') THEN NULL
                ELSE {column}
            END AS cell
            FROM {table}
            {first_rows_only}
        )
    """


def _masked_patterns(
    connection: duckdb.DuckDBPyConnection, column: str, table: str
) -> tuple[MaskedPattern, ...]:
    rows = connection.execute(
        f"""
        {_cells(column, table)}
        SELECT left(masked, {PATTERN_CHARACTERS}) AS pattern,
               length(masked) > {PATTERN_CHARACTERS} OR length(cell) > {CHARACTERS_MASKED} AS cut,
               count(*) AS found
        FROM (SELECT cell, {MASKED} AS masked FROM cells WHERE cell IS NOT NULL)
        GROUP BY pattern, cut
        ORDER BY found DESC, pattern, cut
        LIMIT {PATTERNS_KEPT}
        """
    ).fetchall()
    return tuple(MaskedPattern(pattern, found, cut) for pattern, cut, found in rows)


def _measure_column(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    position: int,
    rows_read: int,
    column_type: str,
    formats: Mapping[str, str],
    every_row: bool,
) -> ColumnMetrics:
    column = f"c{position}"
    # A format comes from what DuckDB sniffed. A plain cast only knows the ISO one, and would say
    # that no value in a column of `03/25/2024` fits. It sniffs one format per type, and types a
    # second column through that cast, so a value can fit either way.
    written_as = formats.get(column_type)
    fits = FITS.get(column_type, "TRUE")
    if written_as:
        fits = f"try_strptime(cell, ?) IS NOT NULL OR {fits}"
    row = connection.execute(
        f"""
        {_cells(column, table, every_row)}
        SELECT count(cell), count(DISTINCT cell),
               count(cell) FILTER (WHERE NOT coalesce({fits}, FALSE)),
               min(length(cell)), max(length(cell)), avg(length(cell))
        FROM cells
        """,
        [written_as] if written_as else [],
    ).fetchone()
    present, distinct, not_fitting, shortest, longest, average = row
    missing = rows_read - present
    return ColumnMetrics(
        missing,
        _percent(missing, rows_read),
        distinct,
        _percent(distinct, present),
        not_fitting,
        shortest,
        longest,
        round(average, 2) if average is not None else None,
        _masked_patterns(connection, column, table),
    )


def measure_rows(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    types: Sequence[str],
    formats: Mapping[str, str],
    every_row: bool,
) -> MeasuredRows:
    """Measure a table's first columns, one for each of `types`, on its first rows or every row.

    The table's columns are named by position, `c0`, `c1` and so on. One short query per column
    keeps a wide file's queries small. A `LIMIT` in them ran out of memory where `rowid` doesn't.
    """
    loaded = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    rows_read = loaded if every_row else min(loaded, FIRST_ROWS)
    return MeasuredRows(
        rows_read,
        every_row or loaded <= FIRST_ROWS,
        tuple(
            _measure_column(
                connection, table, position, rows_read, column_type, formats, every_row
            )
            for position, column_type in enumerate(types)
        ),
        _duplicate_rows(connection, table) if every_row else None,
    )


def _duplicate_rows(connection: duckdb.DuckDBPyConnection, table: str) -> int:
    """Every column read is compared, so a row is a copy only if all of it is the same."""
    return connection.execute(
        f"SELECT count(*) - (SELECT count(*) FROM (SELECT DISTINCT * FROM {table})) FROM {table}"
    ).fetchone()[0]
