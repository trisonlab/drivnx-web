"""Values computed on request from a table's rows, CFX-APP-csv-profile AC-30."""

from pathlib import Path

import pytest
from connectors.csv_files import duckdb_connection, load_rows
from profiling.first_rows import FIRST_ROWS
from profiling.on_request import Bucket, ColumnValues, Frequent, values_for

TABLE = "rows_asked_for"


def values(
    tmp_path: Path, text: str, column_type: str, position: int = 0, every_row: bool = False
) -> ColumnValues:
    path = tmp_path / "values.csv"
    path.write_text(text)
    spill = tmp_path / "duckdb-tmp"
    spill.mkdir(exist_ok=True)
    with duckdb_connection(spill) as connection:
        loaded = load_rows(
            connection,
            path,
            "UTF-8",
            ",",
            True,
            None,
            TABLE,
            None if every_row else FIRST_ROWS + 1,
        )
        return values_for(connection, TABLE, position, column_type, loaded.formats, every_row)


def column(name: str, *cells: str) -> str:
    return name + "\n" + "".join(f"{cell}\n" for cell in cells)


def test_a_text_columns_values_are_its_range_and_most_frequent_values(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30: text takes no histogram, mean, median or deviation.
    cities = ("Oslo", "Bergen", "Oslo", "", "Tromsø", "Oslo")

    found = values(tmp_path, column("city", *cities), "string")

    assert found.minimum == "Bergen"
    assert found.maximum == "Tromsø"
    assert found.frequent == (Frequent("Oslo", 3), Frequent("Bergen", 1), Frequent("Tromsø", 1))
    assert (found.histogram, found.mean, found.median, found.standard_deviation) == (
        None,
        None,
        None,
        None,
    )


def test_only_the_10_most_frequent_values_are_given_ties_in_order(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30
    cells = ["k"] * 3 + ["b"] * 2 + [f"v{number:02d}" for number in range(12)]

    found = values(tmp_path, column("code", *cells), "string")

    assert found.frequent == (
        Frequent("k", 3),
        Frequent("b", 2),
        *(Frequent(f"v{number:02d}", 1) for number in range(8)),
    )


def test_an_integer_columns_range_is_in_number_order_over_values_that_fit(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-30: as text, "10" would come before "9". A value that doesn't fit is
    # still a frequent value, but takes no part in the range or the statistics.
    found = values(tmp_path, column("amount", "9", "10", "100", "n/a", "10", "1.5"), "integer")

    assert (found.minimum, found.maximum) == ("9", "100")
    assert found.frequent[0] == Frequent("10", 2)
    assert {frequent.value for frequent in found.frequent} == {"9", "10", "100", "n/a", "1.5"}
    assert (found.mean, found.median) == ("32.25", "10.0")
    assert found.standard_deviation is not None
    assert float(found.standard_deviation) == pytest.approx(45.17, abs=0.01)


def test_a_number_columns_histogram_has_10_equal_buckets_over_its_range(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30: each bucket runs from where the one before ended, the first
    # from the minimum, and together they hold every value that fits.
    readings = [f"{number}.5" for number in range(100)]

    found = values(tmp_path, column("reading", *readings, "x"), "number")

    assert found.histogram is not None
    assert len(found.histogram) == 10
    assert found.histogram[0].low == "0.5"
    assert found.histogram[-1].high == "99.5"
    for before, after in zip(found.histogram, found.histogram[1:], strict=False):
        assert after.low == before.high
    assert sum(bucket.count for bucket in found.histogram) == 100
    assert (found.mean, found.median) == ("50.0", "50.0")


def test_a_column_of_one_value_has_one_bucket(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30
    found = values(tmp_path, column("level", "7", "7", "7"), "integer")

    assert found.histogram == (Bucket("7", "7", 3),)
    assert found.standard_deviation == "0.0"


def test_a_date_column_written_day_first_takes_its_range_histogram_and_median(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-30: in the format the file writes, and the median is a real date. An
    # average date means little, so there's no mean or deviation.
    dates = ("03/25/2024", "01/02/2024", "02/10/2024", "12/31/2023", "02/10/2024")

    found = values(tmp_path, column("joined", *dates), "date")

    assert (found.minimum, found.maximum) == ("2023-12-31", "2024-03-25")
    assert found.median == "2024-02-10"
    assert (found.mean, found.standard_deviation) == (None, None)
    assert found.histogram is not None
    assert found.histogram[0].low == "2023-12-31"
    assert found.histogram[-1].high == "2024-03-25"
    assert sum(bucket.count for bucket in found.histogram) == 5
    assert found.frequent[0] == Frequent("02/10/2024", 2)


def test_a_column_with_no_values_has_none_to_give(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30: blanks are missing values, as in the profile.
    found = values(tmp_path, "id,note\n1,\n2,  \n", "number", position=1)

    assert found == ColumnValues(2, True, None, None, (), None, None, None, None)


def test_values_after_a_fast_profile_come_from_the_first_rows_only(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30: the part of the file the run read, and said so.
    ids = [str(number) for number in range(FIRST_ROWS + 5)]

    found = values(tmp_path, column("id", *ids), "integer")

    assert (found.rows_read, found.whole_file) == (FIRST_ROWS, False)
    assert found.maximum == str(FIRST_ROWS - 1)


def test_values_after_a_full_profile_come_from_every_row(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30
    found = values(
        tmp_path,
        column("id", *(str(number) for number in range(FIRST_ROWS + 5))),
        "integer",
        every_row=True,
    )

    assert (found.rows_read, found.whole_file) == (FIRST_ROWS + 5, True)
    assert found.maximum == str(FIRST_ROWS + 4)


def test_a_buckets_edges_read_as_the_numbers_they_are(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-30: a width of 0.06 adds up to 0.1599999999999998 in floating point.
    found = values(tmp_path, column("ratio", "0.1", "0.4", "0.7"), "number")

    assert found.histogram is not None
    assert [bucket.high for bucket in found.histogram][:3] == ["0.16", "0.22", "0.28"]


def test_numbers_that_arent_finite_take_no_part_in_the_range_statistics_or_histogram(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-30: a file can write inf or NaN, and they have no place on a range.
    # They're still frequent values.
    found = values(tmp_path, column("reading", "1", "inf", "NaN", "-Infinity", "3"), "number")

    assert (found.minimum, found.maximum) == ("1.0", "3.0")
    assert (found.mean, found.median) == ("2.0", "2.0")
    assert found.histogram is not None
    assert sum(bucket.count for bucket in found.histogram) == 2
    assert {frequent.value for frequent in found.frequent} == {"1", "inf", "NaN", "-Infinity", "3"}


@pytest.mark.parametrize("column_type", ["date", "timestamp"])
def test_dates_that_arent_finite_take_no_part_in_the_range_or_histogram(
    tmp_path: Path, column_type: str
) -> None:
    # CFX-APP-csv-profile/AC-30: DuckDB's `try_strptime` reads `infinity` as 1900-01-01.
    found = values(
        tmp_path, column("joined", "2024-01-02", "Infinity", "-infinity", "2024-03-04"), column_type
    )

    assert found.minimum is not None and found.minimum.startswith("2024-01-02")
    assert found.maximum is not None and found.maximum.startswith("2024-03-04")
    assert found.histogram is not None
    assert sum(bucket.count for bucket in found.histogram) == 2
