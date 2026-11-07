"""A column's values from a table's first rows, or every row, computed when asked for and never
saved (ADR 0004). Like `first_rows`, it knows nothing about where they came from."""

from collections.abc import Mapping
from dataclasses import dataclass

import duckdb

from profiling.first_rows import FIRST_ROWS, FITS, _cells

FREQUENT_VALUES = 10
HISTOGRAM_BUCKETS = 10
NUMBERS = ("integer", "number")
DATES = ("date", "timestamp")
# A value as its type, or NULL when it doesn't fit, so it takes no part in the range or the
# statistics. An integer has to fit as the profile counts it.
_TYPED = {
    "integer": f"CASE WHEN {FITS['integer']} THEN TRY_CAST(cell AS HUGEINT) END",
    "number": "TRY_CAST(cell AS DOUBLE)",
    "date": "TRY_CAST(cell AS DATE)",
    "timestamp": "TRY_CAST(cell AS TIMESTAMP)",
}
# Bucket edges add up in floating point, so 0.1 + 0.06 is 0.1599999999999998. 15 significant digits
# is as many as a double holds exactly.
_EDGE = {"number": "printf('%.15g', edge)"}
# Infinity and NaN, which a file can write and a cast reads, have no place on a range or in a bucket.
_FINITE = ("number", *DATES)
# A date written the way DuckDB sniffed that the file writes it, as the profile reads it. Its
# `try_strptime` reads `infinity` as 1900-01-01 whatever the format, so a cast that sees an infinite
# date goes first.
_WRITTEN_AS = {
    "date": (
        "CASE WHEN isfinite(TRY_CAST(cell AS DATE)) IS NOT FALSE"
        " THEN coalesce(try_strptime(cell, ?)::DATE, TRY_CAST(cell AS DATE)) END"
    ),
    "timestamp": (
        "CASE WHEN isfinite(TRY_CAST(cell AS TIMESTAMP)) IS NOT FALSE"
        " THEN coalesce(try_strptime(cell, ?), TRY_CAST(cell AS TIMESTAMP)) END"
    ),
}


@dataclass(frozen=True)
class Frequent:
    value: str
    count: int


@dataclass(frozen=True)
class Bucket:
    """Values above `low` up to `high`. The first bucket's `low` is the minimum, and holds it."""

    low: str
    high: str
    count: int


@dataclass(frozen=True)
class ColumnValues:
    rows_read: int
    whole_file: bool
    minimum: str | None
    maximum: str | None
    frequent: tuple[Frequent, ...]
    # Only for numbers and dates, as below.
    histogram: tuple[Bucket, ...] | None
    # Mean and deviation only for numbers. An average date means little, but a median is a real one.
    mean: str | None
    median: str | None
    standard_deviation: str | None


def values_for(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    position: int,
    column_type: str,
    formats: Mapping[str, str],
    every_row: bool,
) -> ColumnValues:
    """A column's range, most frequent values and, by its type, histogram and statistics, on the
    table's first rows or every row. The table's columns are named by position, as `measure_rows`
    takes them. Every value is text."""
    loaded = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    rows_read = loaded if every_row else min(loaded, FIRST_ROWS)
    written_as = formats.get(column_type) if column_type in _WRITTEN_AS else None
    typed = _WRITTEN_AS[column_type] if written_as else _TYPED.get(column_type)
    parameters = [written_as] if written_as else []
    cells = f"""
        {_cells(f"c{position}", table, every_row)},
        typed AS (
            SELECT {"CASE WHEN isfinite(v) THEN v END" if column_type in _FINITE else "v"} AS typed
            FROM (SELECT {typed or "cell"} AS v FROM cells)
        )
    """
    if column_type in NUMBERS:
        statistics = "avg(typed::DOUBLE), median(typed::DOUBLE), stddev_samp(typed::DOUBLE)"
    elif column_type in DATES:
        statistics = "NULL, quantile_disc(typed, 0.5), NULL"
    else:
        statistics = "NULL, NULL, NULL"
    minimum, maximum, lowest, highest, mean, median, deviation = connection.execute(
        f"""
        {cells}
        SELECT min(typed)::VARCHAR, max(typed)::VARCHAR, min(typed), max(typed), {statistics}
        FROM typed
        """,
        parameters,
    ).fetchone()
    frequent = connection.execute(
        f"""
        {_cells(f"c{position}", table, every_row)}
        SELECT cell, count(*) AS found FROM cells WHERE cell IS NOT NULL
        GROUP BY cell ORDER BY found DESC, cell LIMIT {FREQUENT_VALUES}
        """
    ).fetchall()
    return ColumnValues(
        rows_read,
        every_row or loaded <= FIRST_ROWS,
        minimum,
        maximum,
        tuple(Frequent(value, count) for value, count in frequent),
        _histogram(connection, cells, parameters, column_type, lowest, highest, minimum)
        if typed is not None and (column_type in NUMBERS or column_type in DATES)
        else None,
        None if mean is None else str(mean),
        None if median is None else str(median),
        None if deviation is None else str(deviation),
    )


def _histogram(
    connection: duckdb.DuckDBPyConnection,
    cells: str,
    parameters: list[str],
    column_type: str,
    lowest: object,
    highest: object,
    minimum: str | None,
) -> tuple[Bucket, ...] | None:
    """Up to 10 buckets of equal width. Integers over a short range take fewer."""
    if minimum is None:
        return None
    highs, counts = connection.execute(
        f"""
        {cells}
        SELECT list_transform(
                   map_keys(found), lambda edge: {_EDGE.get(column_type, "edge::VARCHAR")}
               ),
               map_values(found)
        FROM (
            SELECT histogram(typed, equi_width_bins(?, ?, {HISTOGRAM_BUCKETS}, false)) AS found
            FROM typed
        )
        """,
        [*parameters, lowest, highest],
    ).fetchone()
    lows = [minimum, *highs[:-1]]
    return tuple(
        Bucket(low, high, count) for low, high, count in zip(lows, highs, counts, strict=True)
    )
