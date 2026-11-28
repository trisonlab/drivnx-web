"""Values on request keep a wide number's and a precise time's range as written, DRIVN-60 AC-14
(DRIVN-73)."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from .test_fast_profile import finished, start
from .test_parquet_profile import connect, written
from .test_values_api import cell, shown
from .test_values_on_request import column, values

WIDE_LOW = "12345678901234567890123.45"
WIDE_HIGH = "12345678901234567890123.46"
WIDEST = "123456789012345678901234567890123456."
EARLY = "2026-01-01 00:00:00.123456789"
LATE = "2026-01-01 00:00:00.123456999"


def buckets(found: dict) -> list[tuple[dict | None, dict | None, int]]:
    return [(bucket["low"], bucket["high"], bucket["count"]) for bucket in found["histogram"]]


def first_and_last(edges: list[str]) -> list[tuple[dict | None, dict | None, int]]:
    """Buckets between these edges, with a value in the first and one in the last."""
    return [
        (cell(low), cell(high), 1 if n in (0, len(edges) - 2) else 0)
        for n, (low, high) in enumerate(zip(edges, edges[1:], strict=False))
    ]


@pytest.fixture
def wide(tmp_path: Path) -> Path:
    # Two values 0.01 apart with 25 significant digits, which a double can't tell apart.
    return written(
        tmp_path / "wide.parquet",
        f"""
        SELECT * FROM (VALUES
            ({WIDE_LOW}::DECIMAL(38, 2)),
            ({WIDE_HIGH}::DECIMAL(38, 2))
        ) t(amount)
        """,
    )


@pytest.fixture
def precise(tmp_path: Path) -> Path:
    # Two times 210 nanoseconds apart, in the same microsecond.
    return written(
        tmp_path / "precise.parquet",
        f"""
        SELECT * FROM (VALUES
            (TIMESTAMP_NS '{EARLY}'),
            (TIMESTAMP_NS '{LATE}')
        ) t(seen_at)
        """,
    )


def test_a_wide_decimals_minimum_and_maximum_are_as_written(
    client: TestClient, wide: Path
) -> None:
    # DRIVN-60/AC-14: the values part, with a DECIMAL(38,2) column's range as the file has it.
    run = finished(client, start(client, connect(client, wide), "full"))

    amount = shown(client, run, "amount")
    assert (amount["minimum"], amount["maximum"]) == (cell(WIDE_LOW), cell(WIDE_HIGH))


def test_a_wide_decimals_histogram_buckets_hold_values_from_the_file(
    client: TestClient, wide: Path
) -> None:
    # DRIVN-60/AC-14: ten buckets 0.001 wide from the minimum to the maximum, worked out by hand,
    # with the minimum in the first and the maximum in the last.
    run = finished(client, start(client, connect(client, wide), "full"))

    amount = shown(client, run, "amount")
    whole = "12345678901234567890123."
    edges = [f"{whole}45", *(f"{whole}45{n}" for n in range(1, 10)), f"{whole}46"]
    assert buckets(amount) == first_and_last(edges)


def test_a_wide_decimals_median_is_exact(client: TestClient, wide: Path) -> None:
    # DRIVN-60/AC-14: the median of two values is halfway between them, worked out by hand.
    run = finished(client, start(client, connect(client, wide), "full"))

    assert shown(client, run, "amount")["median"] == cell("12345678901234567890123.455")


@pytest.fixture
def widest(tmp_path: Path) -> Path:
    # DECIMAL(38,2) holds 36 digits before the point, more than Python's decimals work to by
    # default (28).
    return written(
        tmp_path / "widest.parquet",
        f"""
        SELECT * FROM (VALUES
            ({WIDEST}45::DECIMAL(38, 2)),
            ({WIDEST}46::DECIMAL(38, 2))
        ) t(amount)
        """,
    )


def test_the_widest_decimal_38_2_keeps_its_range(client: TestClient, widest: Path) -> None:
    # DRIVN-60/AC-14
    run = finished(client, start(client, connect(client, widest), "full"))

    amount = shown(client, run, "amount")
    assert (amount["minimum"], amount["maximum"]) == (cell(f"{WIDEST}45"), cell(f"{WIDEST}46"))


def test_the_widest_decimal_38_2_keeps_its_histogram_and_median(
    client: TestClient, widest: Path
) -> None:
    # DRIVN-60/AC-14: edges and a median of 39 digits, one more than a decimal holds, worked out by
    # hand.
    run = finished(client, start(client, connect(client, widest), "full"))

    amount = shown(client, run, "amount")
    edges = [f"{WIDEST}45", *(f"{WIDEST}45{n}" for n in range(1, 10)), f"{WIDEST}46"]
    assert buckets(amount) == first_and_last(edges)
    assert amount["median"] == cell(f"{WIDEST}455")


def test_a_nanosecond_times_minimum_and_maximum_are_as_written(
    client: TestClient, precise: Path
) -> None:
    # DRIVN-60/AC-14: a TIMESTAMP_NS column's range as the file has it, not cut to microseconds.
    run = finished(client, start(client, connect(client, precise), "full"))

    seen_at = shown(client, run, "seen_at")
    assert (seen_at["minimum"], seen_at["maximum"]) == (cell(EARLY), cell(LATE))


def test_times_beyond_what_nanoseconds_reach_keep_their_range(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14: a time in nanoseconds only reaches from 1677 to 2262, so a column with a time
    # outside them keeps microseconds, as before.
    path = written(
        tmp_path / "far.parquet",
        """
        SELECT * FROM (VALUES
            (TIMESTAMP '1500-01-01 00:00:00'),
            (TIMESTAMP '9999-12-31 23:59:59.999999')
        ) t(seen_at)
        """,
    )
    run = finished(client, start(client, connect(client, path), "full"))

    seen_at = shown(client, run, "seen_at")
    assert (seen_at["minimum"], seen_at["maximum"]) == (
        cell("1500-01-01 00:00:00"),
        cell("9999-12-31 23:59:59.999999"),
    )
    assert seen_at["median"] == cell("1500-01-01 00:00:00")


def test_a_nanosecond_times_median_is_a_time_from_the_file(
    client: TestClient, precise: Path
) -> None:
    # DRIVN-60/AC-14: a time's median is the lower middle one, as a date's (DRIVN-9/AC-30).
    run = finished(client, start(client, connect(client, precise), "full"))

    assert shown(client, run, "seen_at")["median"] == cell(EARLY)


def test_a_nanosecond_times_histogram_buckets_hold_times_from_the_file(
    client: TestClient, precise: Path
) -> None:
    # DRIVN-60/AC-14: ten buckets 21 nanoseconds wide, worked out by hand, written as DuckDB writes
    # a time, which leaves off a fraction's last zeros.
    run = finished(client, start(client, connect(client, precise), "full"))

    seen_at = shown(client, run, "seen_at")
    second = "2026-01-01 00:00:00."
    inner = ["12345681", *(f"123456{789 + 21 * n}" for n in range(2, 10))]
    edges = [EARLY, *(second + fraction for fraction in inner), LATE]
    assert buckets(seen_at) == first_and_last(edges)


def test_a_csv_number_of_more_digits_than_a_double_holds_keeps_them(tmp_path: Path) -> None:
    # DRIVN-73: a CSV number is read the same way, so it's checked the same way. 20 significant
    # digits, the last one apart, and a number written with fewer places.
    found = values(
        tmp_path,
        column("amount", "12345678901234567.891", "12345678901234567.892", "7"),
        "number",
        every_row=True,
    )

    assert (found.minimum, found.maximum) == ("7.000", "12345678901234567.892")
    assert found.median == "12345678901234567.891"
    assert found.histogram is not None
    assert found.histogram[-1].high == "12345678901234567.892"
    assert [bucket.count for bucket in found.histogram] == [1, *[0] * 8, 2]


def test_a_csv_time_finer_than_a_microsecond_keeps_it(tmp_path: Path) -> None:
    # DRIVN-73: as a CSV writes a time, which DuckDB detects without a format.
    found = values(tmp_path, column("seen_at", EARLY, LATE), "timestamp", every_row=True)

    assert (found.minimum, found.maximum, found.median) == (EARLY, LATE, EARLY)


def test_a_median_between_two_distant_middle_values_is_exact(tmp_path: Path) -> None:
    # DRIVN-73: halfway between the two, worked out by hand, which DuckDB's quantile_cont works out
    # through a double as 671472079393161357.8.
    found = values(
        tmp_path,
        column("amount", "556269653869031489", "786674504917291237"),
        "number",
        every_row=True,
    )

    assert found.median == "671472079393161363"


def test_a_value_on_a_buckets_edge_counts_in_the_bucket_it_ends(tmp_path: Path) -> None:
    # DRIVN-9/AC-30, DRIVN-73: 0.2 is the fifth bucket's high, between edges of a place more than
    # the column writes.
    found = values(tmp_path, column("ratio", "0.1", "0.2", "0.3"), "number", every_row=True)

    assert found.histogram is not None
    assert [bucket.high for bucket in found.histogram][3:5] == ["0.18", "0.2"]
    assert [bucket.count for bucket in found.histogram] == [1, 0, 0, 0, 1, 0, 0, 0, 0, 1]


def test_a_decimal_of_38_places_writes_its_edges_and_median_as_its_range(tmp_path: Path) -> None:
    # DRIVN-73: DuckDB leaves off the 0 before the point of a DECIMAL(38,38).
    low, high = "." + "1" * 38, "." + "9" * 38
    found = values(tmp_path, column("share", low, high), "number", every_row=True)

    assert (found.minimum, found.maximum, found.median) == (low, high, "." + "5" * 38)
    assert found.histogram is not None
    assert found.histogram[-1].high == high
