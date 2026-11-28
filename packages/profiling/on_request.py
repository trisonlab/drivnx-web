"""A column's values from a table's first rows, or every row, computed when asked for and never
saved (ADR 0004). Like `first_rows`, it knows nothing about where they came from."""

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, localcontext

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
# A number written plainly, as `-12.50` or `.5`, and the most digits a decimal holds.
_PLAIN = r"[+-]?\d*\.?\d*"
_DECIMAL_DIGITS = 38
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
    blank_is_missing: bool = True,
) -> ColumnValues:
    """A column's range, most frequent values and, by its type, histogram and statistics, on the
    table's first rows or every row. The table's columns are named by position, as `measure_rows`
    takes them. Every value is text. With `blank_is_missing` off, only NULL is missing, as in a
    database (DRIVN-51)."""
    loaded = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    rows_read = loaded if every_row else min(loaded, FIRST_ROWS)
    written_as = formats.get(column_type) if column_type in _WRITTEN_AS else None
    typed = _WRITTEN_AS[column_type] if written_as else _TYPED.get(column_type)
    places = (
        _decimal_places(connection, table, position, every_row, blank_is_missing)
        if column_type == "number"
        else None
    )
    nanoseconds = (
        column_type == "timestamp"
        and not written_as
        and _in_nanoseconds(connection, table, position, every_row, blank_is_missing)
    )
    if places is not None:
        typed = f"TRY_CAST(cell AS DECIMAL({_DECIMAL_DIGITS}, {places}))"
    elif nanoseconds:
        typed = "TRY_CAST(cell AS TIMESTAMP_NS)"
    parameters = [written_as] if written_as else []
    cells = f"""
        {_cells(f"c{position}", table, every_row, blank_is_missing)},
        typed AS (
            SELECT {"CASE WHEN isfinite(v) THEN v END" if column_type in _FINITE else "v"} AS typed
            FROM (SELECT {typed or "cell"} AS v FROM cells)
        )
    """
    if column_type in NUMBERS:
        median = (
            "median(typed::DOUBLE)"
            if places is None
            # The two middle values exactly, whose average a decimal can't always hold.
            else "[quantile_disc(typed, 0.5), -quantile_disc(-typed, 0.5)]"
        )
        statistics = f"avg(typed::DOUBLE), {median}, stddev_samp(typed::DOUBLE)"
    elif column_type in DATES:
        # As text by DuckDB, which keeps a time's nanoseconds (DRIVN-73).
        statistics = "NULL, quantile_disc(typed, 0.5)::VARCHAR, NULL"
    else:
        statistics = "NULL, NULL, NULL"
    # A time in nanoseconds as a whole number of them, which Python's datetime would cut to
    # microseconds.
    extremes = (
        "epoch_ns(min(typed)), epoch_ns(max(typed))" if nanoseconds else "min(typed), max(typed)"
    )
    minimum, maximum, lowest, highest, mean, median, deviation = connection.execute(
        f"""
        {cells}
        SELECT min(typed)::VARCHAR, max(typed)::VARCHAR, {extremes}, {statistics}
        FROM typed
        """,
        parameters,
    ).fetchone()
    frequent = connection.execute(
        f"""
        {_cells(f"c{position}", table, every_row, blank_is_missing)}
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
        (
            _decimal_histogram(connection, cells, places, lowest, highest, minimum)
            if places is not None
            else _nanosecond_histogram(connection, cells, lowest, highest, minimum)
            if nanoseconds
            else _histogram(
                connection,
                cells,
                minimum,
                column_type,
                f"equi_width_bins(?, ?, {HISTOGRAM_BUCKETS}, false)",
                [*parameters, lowest, highest],
            )
        )
        if typed is not None and (column_type in NUMBERS or column_type in DATES)
        else None,
        None if mean is None else str(mean),
        _middle(median, places) if places is not None else None if median is None else str(median),
        None if deviation is None else str(deviation),
    )


def _decimal_places(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    position: int,
    every_row: bool,
    blank_is_missing: bool,
) -> int | None:
    """The places of a decimal that holds every number the column writes, so a value of more
    digits than a double holds keeps them all (DRIVN-73). Only when every number is written
    plainly and within the 38 digits a decimal holds; otherwise None, and they're read as doubles,
    which may round. A sign or a leading zero counts as a digit, which costs a little reach but no
    reading."""
    plain, places, whole = connection.execute(
        f"""
        {_cells(f"c{position}", table, every_row, blank_is_missing)},
        numbers AS (
            SELECT cell AS number, strpos(cell, '.') AS point
            FROM cells WHERE isfinite(TRY_CAST(cell AS DOUBLE))
        )
        SELECT bool_and(regexp_full_match(number, '{_PLAIN}')),
               coalesce(max(CASE WHEN point > 0 THEN length(number) - point ELSE 0 END), 0),
               coalesce(max(CASE WHEN point > 0 THEN point - 1 ELSE length(number) END), 0)
        FROM numbers
        """
    ).fetchone()
    if plain is False or whole + places > _DECIMAL_DIGITS:
        return None
    return places


def _in_nanoseconds(
    connection: duckdb.DuckDBPyConnection,
    table: str,
    position: int,
    every_row: bool,
    blank_is_missing: bool,
) -> bool:
    """Whether every time the column holds fits in nanoseconds, which keep a time finer than a
    microsecond (DRIVN-73) but only reach from 1677 to 2262."""
    (fits,) = connection.execute(
        f"""
        {_cells(f"c{position}", table, every_row, blank_is_missing)}
        SELECT bool_and(TRY_CAST(cell AS TIMESTAMP_NS) IS NOT NULL)
        FROM cells WHERE TRY_CAST(cell AS TIMESTAMP) IS NOT NULL
        """
    ).fetchone()
    return fits is not False


def _nanosecond_histogram(
    connection: duckdb.DuckDBPyConnection,
    cells: str,
    lowest: int | None,
    highest: int | None,
    minimum: str | None,
) -> tuple[Bucket, ...] | None:
    """Ten buckets of equal width with edges in whole nanoseconds, worked out as whole numbers
    (DRIVN-73). A time from 1677 to 2262 has too many for a double to hold."""
    if lowest is None or highest is None:
        return None
    edges = (
        [lowest]
        if lowest == highest
        else [
            lowest + (highest - lowest) * n // HISTOGRAM_BUCKETS
            for n in range(1, HISTOGRAM_BUCKETS + 1)
        ]
    )
    return _histogram(
        connection,
        cells,
        minimum,
        "timestamp",
        "list_transform(?::BIGINT[], lambda edge: make_timestamp_ns(edge))",
        [edges],
    )


def _histogram(
    connection: duckdb.DuckDBPyConnection,
    cells: str,
    minimum: str | None,
    column_type: str,
    bins: str,
    parameters: list[object],
) -> tuple[Bucket, ...] | None:
    """Buckets over `bins`, the SQL for their edges, which each caller gives: up to 10 of equal
    width. Integers over a short range take fewer."""
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
            SELECT histogram(typed, {bins}) AS found FROM typed
        )
        """,
        parameters,
    ).fetchone()
    lows = [minimum, *highs[:-1]]
    return tuple(
        Bucket(low, high, count) for low, high, count in zip(lows, highs, counts, strict=True)
    )


def _decimal_histogram(
    connection: duckdb.DuckDBPyConnection,
    cells: str,
    places: int,
    lowest: Decimal | None,
    highest: Decimal | None,
    minimum: str | None,
) -> tuple[Bucket, ...] | None:
    """Ten buckets of equal width with exact edges, counted by comparing each value with them as
    decimals, never as doubles (DRIVN-73). A tenth of the range takes one place more."""
    if lowest is None or highest is None or minimum is None:
        return None
    if lowest == highest:
        (count,) = connection.execute(f"{cells} SELECT count(typed) FROM typed").fetchone()
        return (Bucket(minimum, minimum, count),)
    with localcontext(prec=2 * _DECIMAL_DIGITS):
        edges = [
            lowest + (highest - lowest) * n / HISTOGRAM_BUCKETS
            for n in range(1, HISTOGRAM_BUCKETS + 1)
        ]
        # A value of the column's places is above an edge just when it's above the edge rounded
        # down to them, which the column's decimal holds.
        floors = [
            format(edge.quantize(Decimal(1).scaleb(-places), ROUND_FLOOR), "f")
            for edge in edges[:-1]
        ]
    exact = f"DECIMAL({_DECIMAL_DIGITS}, {places})"
    above = " + ".join(f"(typed > ?::{exact})::INTEGER" for _ in floors)
    counts = dict(
        connection.execute(
            f"{cells} SELECT {above} AS bucket, count(*) FROM typed WHERE typed IS NOT NULL"
            " GROUP BY bucket",
            floors,
        ).fetchall()
    )
    highs = [_written(edge, places) for edge in edges]
    lows = [minimum, *highs[:-1]]
    return tuple(
        Bucket(low, high, counts.get(n, 0))
        for n, (low, high) in enumerate(zip(lows, highs, strict=True))
    )


def _written(value: Decimal, places: int) -> str:
    """A decimal with the column's places, or one more when it needs it, as DuckDB writes one: with
    no 0 before the point when every digit is a place."""
    with localcontext(prec=2 * _DECIMAL_DIGITS):
        for shown in (places, places + 1):
            rounded = value.quantize(Decimal(1).scaleb(-shown))
            if rounded == value:
                written = format(rounded, "f")
                return written.replace("0.", ".", 1) if places == _DECIMAL_DIGITS else written
    raise ValueError(value)


def _middle(middle: list[Decimal | None], places: int) -> str | None:
    """A median halfway between the two middle values, or the one when they're the same."""
    lower, upper = middle
    if lower is None or upper is None:
        return None
    with localcontext(prec=2 * _DECIMAL_DIGITS):
        return _written((lower + upper) / 2, places)
