"""Reading and measuring a file's first rows, CFX-APP-csv-profile AC-07, AC-16, AC-17."""

from pathlib import Path

import pytest
from connectors.csv_files import CsvError, duckdb_connection, load_rows
from profiling.first_rows import FIRST_ROWS, FirstRows, measure_first_rows


def measure(
    path: Path,
    tmp_path: Path,
    delimiter: str = ",",
    encoding: str = "UTF-8",
    has_header: bool = True,
    chosen_skip: int | None = None,
) -> tuple[list[str], FirstRows]:
    spill = tmp_path / "duckdb-tmp"
    spill.mkdir(exist_ok=True)
    with duckdb_connection(spill) as connection:
        names = load_rows(
            connection,
            path,
            encoding,
            delimiter,
            has_header,
            chosen_skip,
            "first_rows",
            FIRST_ROWS + 1,
        ).names
        return names, measure_first_rows(connection, "first_rows", ["string"] * len(names), {})


def missing_of(measured: FirstRows) -> tuple[int, ...]:
    return tuple(column.missing for column in measured.columns)


def summary(measured: FirstRows) -> tuple[int, bool, tuple[int, ...]]:
    return measured.rows_read, measured.whole_file, missing_of(measured)


def test_a_small_file_is_read_as_the_whole_file(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-17
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n3,Cid\n")

    columns, measured = measure(path, tmp_path)

    assert columns == ["id", "name"]
    assert measured.rows_read == 3
    assert measured.whole_file is True


def numbered_csv(path: Path, rows: int) -> Path:
    path.write_text("id\n" + "".join(f"{number}\n" for number in range(rows)))
    return path


@pytest.mark.parametrize(
    ("rows", "rows_read", "whole_file"),
    [
        (99_999, 99_999, True),
        (100_000, 100_000, True),
        (100_001, 100_000, False),
        (100_002, 100_000, False),
    ],
)
def test_the_first_100000_rows_are_all_that_is_measured(
    tmp_path: Path, rows: int, rows_read: int, whole_file: bool
) -> None:
    # CFX-APP-csv-profile/AC-07, AC-16 and AC-17: exactly 100,000 rows is still the whole file.
    path = numbered_csv(tmp_path / "numbers.csv", rows)

    _, measured = measure(path, tmp_path)

    assert (measured.rows_read, measured.whole_file) == (rows_read, whole_file)


def test_empty_and_whitespace_only_cells_are_missing_in_each_column(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-07: "missing" is no value, or a value holding only whitespace.
    path = tmp_path / "people.csv"
    path.write_text(
        "id,name,city\n"
        "1,,Paris\n"
        "2,   ,Lyon\n"
        "3,\t,Nice\n"
        "4, ,Metz\n"
        "5,　 ,\n"
        "6,x y,Caen\n",
        encoding="utf-8",
    )

    _, measured = measure(path, tmp_path)

    assert missing_of(measured) == (0, 5, 1)


def test_a_missing_value_after_the_first_100000_rows_is_not_counted(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-07: the row read to tell whether there are more is never measured.
    path = tmp_path / "numbers.csv"
    rows = "".join(f"{number},x\n" for number in range(100_000))
    path.write_text(f"id,name\n{rows}100000,\n100001,\n")

    _, measured = measure(path, tmp_path)

    assert missing_of(measured) == (0, 0)


def test_column_names_can_hold_anything(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-07: a name comes from the file, so it must never reach the SQL as SQL.
    path = tmp_path / "hostile.csv"
    path.write_text('"a""b","x"") FROM first_rows; DROP TABLE first_rows; --"\n1,\n2,y\n')

    columns, measured = measure(path, tmp_path)

    assert columns == ['a"b', 'x") FROM first_rows; DROP TABLE first_rows; --']
    assert missing_of(measured) == (0, 1)


def test_duplicate_names_are_measured_as_separate_columns(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-07: the names DuckDB gives them are the ones the preview shows.
    path = tmp_path / "duplicates.csv"
    path.write_text("a,a,A\n1,,3\n,,6\n")

    columns, measured = measure(path, tmp_path)

    assert len(set(columns)) == 3
    assert missing_of(measured) == (1, 2, 0)


def test_a_header_with_no_rows_has_nothing_missing(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-17
    path = tmp_path / "empty_table.csv"
    path.write_text("id,name\n")

    _, measured = measure(path, tmp_path)

    assert summary(measured) == (0, True, (0, 0))


def test_lines_the_user_chose_to_skip_are_not_measured(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-07: read as the preview reads it, with the settings the user saved.
    path = tmp_path / "report.csv"
    path.write_text("Quarterly,report\nid,name\n1,Ann\n2,\n")

    columns, measured = measure(path, tmp_path, chosen_skip=1)

    assert columns == ["id", "name"]
    assert summary(measured) == (2, True, (0, 1))


def test_a_broken_row_after_the_first_rows_does_not_stop_the_read(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-07: a row past the limit isn't read at all.
    path = tmp_path / "numbers.csv"
    rows = "".join(f"{number},x\n" for number in range(120_000))
    path.write_text(f"id,name\n{rows}1,2,3,4,5,6\n")

    columns, measured = measure(path, tmp_path)

    assert columns == ["id", "name"]
    assert summary(measured) == (100_000, False, (0, 0))


@pytest.mark.parametrize("name", ["rowid", "ROWID", "RowId"])
def test_a_column_can_share_a_name_with_duckdbs_own_row_number(
    tmp_path: Path, name: str
) -> None:
    # CFX-APP-csv-profile/AC-07: exports from Oracle and SQLite often have this column.
    path = tmp_path / "export.csv"
    path.write_text(f"{name},city\n50000,Paris\n150000,\n3,Lyon\n4,Nice\n")

    columns, measured = measure(path, tmp_path)

    assert columns == [name, "city"]
    assert summary(measured) == (4, True, (0, 1))


def test_a_broken_row_just_after_the_first_rows_fails_the_read(tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-07: DuckDB parses in chunks, so this row is reached. A failed read is
    # safe, and leaving out a row that was reached wouldn't be. A DuckDB upgrade may change this.
    path = tmp_path / "numbers.csv"
    rows = "".join(f"{number},x\n" for number in range(100_049))
    path.write_text(f"id,name\n{rows}1,2,3,4,5,6\n")

    with pytest.raises(CsvError):
        measure(path, tmp_path)


def test_a_surplus_column_can_share_a_name_with_duckdbs_own_row_number(
    tmp_path: Path,
) -> None:
    # CFX-APP-csv-profile/AC-14: only the first column is measured, and the name after it is harmless.
    path = tmp_path / "export.csv"
    path.write_text("id,rowid\n1,x\n,y\n3,z\n4,w\n")
    spill = tmp_path / "duckdb-tmp"
    spill.mkdir()

    with duckdb_connection(spill) as connection:
        load_rows(connection, path, "UTF-8", ",", True, None, "first_rows", FIRST_ROWS + 1)
        measured = measure_first_rows(connection, "first_rows", ["string"], {})

    assert missing_of(measured) == (1,)
