"""Paging through a CSV's rows, CFX-APP-first-slice AC-27, AC-28, AC-29, AC-30, AC-31."""

import os
import shutil
import sqlite3
from pathlib import Path

import fsspec
import pytest
from fastapi.testclient import TestClient
from local_api import sources_api

from ..conftest import posix_only

CSV_DIR = Path(__file__).parents[2] / "testdata" / "csv"


def connect(client: TestClient, path: Path) -> str:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def numbered_csv(path: Path, rows: int) -> Path:
    lines = [f"{number},name{number}\n" for number in range(1, rows + 1)]
    path.write_text("id,name\n" + "".join(lines))
    return path


def names(page: dict) -> list[str]:
    return [column["value"] for column in page["columns"]]


def values(page: dict) -> list[list[str | None]]:
    return [[cell["value"] for cell in row] for row in page["rows"]]


def test_the_preview_opens_on_the_first_50_rows(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27
    source = connect(client, numbered_csv(tmp_path / "people.csv", 120))

    response = client.get(f"/api/sources/{source}/rows")

    assert response.status_code == 200
    page = response.json()
    assert names(page) == ["id", "name"]
    assert len(page["rows"]) == 50
    assert values(page)[0] == ["1", "name1"]
    assert values(page)[49] == ["50", "name50"]


def test_next_and_previous_show_the_adjacent_pages_without_a_total(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27
    source = connect(client, numbered_csv(tmp_path / "people.csv", 120))

    second = client.get(f"/api/sources/{source}/rows", params={"offset": 50}).json()
    first = client.get(f"/api/sources/{source}/rows", params={"offset": 0}).json()

    assert values(second)[0] == ["51", "name51"]
    assert values(second)[49] == ["100", "name100"]
    assert second["has_next"] is True
    assert values(first)[0] == ["1", "name1"]
    assert not {key for key in second if "total" in key or "count" in key}


@pytest.mark.parametrize(("rows", "offset"), [(120, 100), (100, 50), (50, 0), (0, 0)])
def test_there_is_no_next_page_on_the_last_page(
    client: TestClient, tmp_path: Path, rows: int, offset: int
) -> None:
    # CFX-APP-first-slice/AC-27: a last page that is exactly full is the case that goes wrong.
    source = connect(client, numbered_csv(tmp_path / "people.csv", rows))

    page = client.get(f"/api/sources/{source}/rows", params={"offset": offset}).json()

    assert page["has_next"] is False
    assert len(page["rows"]) == min(rows - offset, 50)


def test_an_unknown_source_is_not_found(client: TestClient) -> None:
    assert client.get("/api/sources/nothing/rows").status_code == 404


@pytest.mark.parametrize(
    ("params", "status", "rows"),
    [
        ({"limit": 200}, 200, 200),
        ({"limit": 1}, 200, 1),
        ({"limit": 201}, 422, None),
        ({"limit": 0}, 422, None),
        ({"limit": -1}, 422, None),
        ({"limit": "many"}, 422, None),
        ({"offset": -1}, 422, None),
        ({"offset": 10**30}, 422, None),
        ({"offset": 2**63 - 1}, 200, 0),
    ],
)
def test_page_sizes_above_200_and_bad_positions_are_refused(
    client: TestClient, tmp_path: Path, params: dict, status: int, rows: int | None
) -> None:
    # CFX-APP-first-slice/AC-28
    source = connect(client, numbered_csv(tmp_path / "people.csv", 300))

    response = client.get(f"/api/sources/{source}/rows", params=params)

    assert response.status_code == status
    if rows is not None:
        assert len(response.json()["rows"]) == rows


@pytest.mark.parametrize(
    "file",
    [
        "comma_utf8.csv",
        "comma_utf8_bom.csv",
        "comma_utf16_le.csv",
        "comma_utf16_be.csv",
        "comma_latin1.csv",
    ],
)
def test_every_encoding_gives_the_same_rows(client: TestClient, file: str) -> None:
    # CFX-APP-first-slice/AC-27
    page = client.get(f"/api/sources/{connect(client, CSV_DIR / file)}/rows").json()

    assert names(page) == ["id", "name", "city", "amount", "joined"]
    assert values(page)[2] == ["3", "Zoé", "Orléans", "3.00", "2024-03-05"]


@pytest.mark.parametrize(
    ("file", "columns", "row"),
    [
        (
            "prices_windows1252.csv",
            ["item", "price", "note"],
            ["Café", "€5", "“fresh”"],
        ),
        ("emoji_utf16_le.csv", ["id", "note"], ["1", "😀 great"]),
        ("emoji_utf16_be.csv", ["id", "note"], ["1", "😀 great"]),
        ("title_line.csv", ["id", "name", "amount"], ["1", "Ann", "10.50"]),
        (
            "no_header_typed.csv",
            ["column0", "column1", "column2", "column3", "column4"],
            ["1", "Ann", "Paris", "10.50", "2024-01-15"],
        ),
        (
            "tab_commas_in_text.csv",
            ["id", "address"],
            ["1", "1 Main St, Paris, France"],
        ),
        ("quoted_delimiter.csv", ["id", "note"], ["1", "a, b"]),
    ],
)
def test_the_preview_finds_the_same_columns_and_rows_the_file_holds(
    client: TestClient, file: str, columns: list[str], row: list[str]
) -> None:
    # CFX-APP-first-slice/AC-27, and a title line above the header must not become a row.
    page = client.get(f"/api/sources/{connect(client, CSV_DIR / file)}/rows").json()

    assert names(page) == columns
    assert values(page)[0] == row


@pytest.mark.parametrize("name", ["report[1].csv", "star*name.csv", "plain.csv.gz"])
def test_awkward_file_names_preview_the_file_they_name(
    client: TestClient, tmp_path: Path, name: str
) -> None:
    # Found in T05: * ? [ are wildcards to DuckDB, and it picks compression from the name.
    shutil.copy(CSV_DIR / "comma_utf8.csv", tmp_path / name)
    (tmp_path / "report1.csv").write_text("wrong,file\n1,2\n")

    page = client.get(f"/api/sources/{connect(client, tmp_path / name)}/rows").json()

    assert names(page)[0] == "id"


def test_a_short_row_gets_blanks(client: TestClient) -> None:
    # CFX-APP-first-slice/AC-31: from T05, detection accepts one uneven row.
    page = client.get(
        f"/api/sources/{connect(client, CSV_DIR / 'short_row.csv')}/rows"
    ).json()

    assert names(page) == ["id", "name", "amount"]
    assert values(page)[4] == ["5", None, None]
    assert values(page)[3] == ["4", "Dee", "12.75"]


def test_an_extra_field_gets_its_own_column_so_nothing_is_hidden(
    client: TestClient,
) -> None:
    # CFX-APP-first-slice/AC-31: from T05, detection accepts one uneven row.
    page = client.get(
        f"/api/sources/{connect(client, CSV_DIR / 'extra_field_in_a_row.csv')}/rows"
    ).json()

    assert len(names(page)) == 4
    assert names(page)[:3] == ["id", "name", "amount"]
    assert values(page)[4] == ["5", "Eve", "1.00", "surplus"]
    assert values(page)[3] == ["4", "Dee", "12.75", None]


def far_past_the_sniff_sample(path: Path, odd_row: str) -> Path:
    rows = "".join(f"{number},name{number}\n" for number in range(1, 100_001))
    path.write_text("id,name\n" + rows + odd_row + rows)
    return path


def test_a_short_row_past_what_detection_read_still_pages(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31
    source = connect(client, far_past_the_sniff_sample(tmp_path / "late.csv", "x\n"))

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 99_999}).json()

    assert values(page)[:3] == [["100000", "name100000"], ["x", None], ["1", "name1"]]


def test_a_row_that_cannot_fit_gives_a_clear_error_and_earlier_pages_still_work(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31: skipping the row would show a page that hides data.
    source = connect(
        client, far_past_the_sniff_sample(tmp_path / "late.csv", "x,private-value,y\n")
    )

    bad = client.get(f"/api/sources/{source}/rows", params={"offset": 99_990})
    good = client.get(f"/api/sources/{source}/rows", params={"offset": 50})

    assert bad.status_code == 409
    assert "this page can't be shown" in bad.json()["detail"]
    # Nothing here changed, so the message mustn't suggest it did.
    assert "changed" not in bad.json()["detail"]
    assert "private-value" not in bad.text
    assert good.status_code == 200


@posix_only
@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("deleted", "moved or been deleted"),
        ("replaced by a folder", "moved or been deleted"),
        ("replaced by a fifo", "moved or been deleted"),
        ("link to a device", "moved or been deleted"),
        ("unreadable", "permission"),
        ("emptied", "empty"),
        ("replaced by binary", "isn't text"),
    ],
)
def test_a_file_that_changes_between_pages_gives_a_clear_error(
    client: TestClient, tmp_path: Path, change: str, message: str
) -> None:
    # CFX-APP-first-slice/AC-31
    path = numbered_csv(tmp_path / "people.csv", 120)
    source = connect(client, path)
    assert client.get(f"/api/sources/{source}/rows").status_code == 200

    if change == "deleted":
        path.unlink()
    elif change == "replaced by a folder":
        path.unlink()
        path.mkdir()
    elif change == "replaced by a fifo":
        path.unlink()
        os.mkfifo(path)
    elif change == "link to a device":
        path.unlink()
        path.symlink_to("/dev/zero")
    elif change == "unreadable":
        path.chmod(0)
    elif change == "emptied":
        path.write_bytes(b"")
    elif change == "replaced by binary":
        path.write_bytes(b"\xff\xfe\x00\x00" + bytes(range(256)))

    response = client.get(f"/api/sources/{source}/rows", params={"offset": 50})

    assert response.status_code == 409
    assert message in response.json()["detail"]
    assert str(path) not in response.text
    assert client.get("/api/sources").status_code == 200


def test_a_file_rewritten_between_pages_shows_its_current_data(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31
    path = numbered_csv(tmp_path / "people.csv", 120)
    source = connect(client, path)

    path.write_text("code,city,country\nA,Paris,France\nB,Lyon,France\n")
    page = client.get(f"/api/sources/{source}/rows").json()

    assert names(page) == ["code", "city", "country"]
    assert values(page) == [["A", "Paris", "France"], ["B", "Lyon", "France"]]


def test_a_file_that_shrinks_below_the_page_shows_an_empty_last_page(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31
    path = numbered_csv(tmp_path / "people.csv", 120)
    source = connect(client, path)

    numbered_csv(path, 10)
    page = client.get(f"/api/sources/{source}/rows", params={"offset": 50}).json()

    assert page["rows"] == []
    assert page["has_next"] is False
    assert names(page) == ["id", "name"]


def test_a_windows_1252_file_that_grows_past_the_limit_says_to_save_it_as_utf8(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-21 and CFX-APP-first-slice/AC-31
    path = tmp_path / "prices.csv"
    shutil.copy(CSV_DIR / "prices_windows1252.csv", path)
    source = connect(client, path)

    path.write_bytes(path.read_bytes() + b"Tea,\x803,hot\n" * (10 * 1024 * 1024))
    response = client.get(f"/api/sources/{source}/rows")

    assert response.status_code == 409
    assert "Save it as UTF-8" in response.json()["detail"]


SCRIPT_TEXT = (
    '<script>window.ran = true</script><img src=x onerror="window.ran = true">'
)


def test_html_in_a_cell_comes_back_unchanged_as_text(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-29: the screen shows it as text, so the API must not alter it.
    path = tmp_path / "html.csv"
    path.write_text('id,note\n1,"' + SCRIPT_TEXT.replace('"', '""') + '"\n')

    response = client.get(f"/api/sources/{connect(client, path)}/rows")

    assert values(response.json()) == [["1", SCRIPT_TEXT]]
    assert response.headers["content-type"] == "application/json"


@pytest.mark.parametrize(
    ("length", "kept", "cut"),
    [(999, 999, False), (1000, 1000, False), (1001, 1000, True), (50_000, 1000, True)],
)
def test_a_cell_over_1000_characters_is_cut_and_marked(
    client: TestClient, tmp_path: Path, length: int, kept: int, cut: bool
) -> None:
    # CFX-APP-first-slice/AC-29
    path = tmp_path / "long.csv"
    path.write_text(f"id,note\n1,{'é' * length}\n2,short\n")

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert page["rows"][0][1] == {"value": "é" * kept, "cut": cut}
    assert page["rows"][1][1] == {"value": "short", "cut": False}
    assert page["rows"][0][0]["cut"] is False


def test_an_empty_cell_is_not_marked_as_cut(client: TestClient) -> None:
    # CFX-APP-first-slice/AC-29: a short row's missing values have no length to cut.
    page = client.get(
        f"/api/sources/{connect(client, CSV_DIR / 'short_row.csv')}/rows"
    ).json()

    assert page["rows"][4][1] == {"value": None, "cut": False}


def everything_stored(workspace_dir: Path) -> bytes:
    """Raw bytes as well as a dump, because deleted rows stay in SQLite's free pages."""
    connection = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()
    return b"".join(
        entry.read_bytes() for entry in workspace_dir.rglob("*") if entry.is_file()
    )


@pytest.mark.parametrize("file", ["comma_utf8.csv", "prices_windows1252.csv"])
def test_viewing_pages_stores_no_row_values_and_leaves_no_files(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    file: str,
) -> None:
    # CFX-APP-first-slice/AC-30: Windows-1252 is decoded in memory, so it takes a different route.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    source = connect(client, CSV_DIR / file)

    page = client.get(f"/api/sources/{source}/rows").json()
    client.get(f"/api/sources/{source}/rows", params={"offset": 1})

    assert page["rows"], "no rows were read, so this test proves nothing"
    stored = everything_stored(workspace_dir)
    for value in ("Paris", "Orléans", "2024-01-15", "Café", "fresh"):
        assert value.encode() not in stored
    assert list((workspace_dir / "duckdb-tmp").iterdir()) == []
    assert list(cwd.iterdir()) == []


def test_a_decoded_file_leaves_nothing_in_memory_after_a_page(
    client: TestClient,
) -> None:
    # CFX-APP-first-slice/AC-30: the decoded copy holds every row of the file.
    source = connect(client, CSV_DIR / "prices_windows1252.csv")
    memory = fsspec.filesystem("memory")

    client.get(f"/api/sources/{source}/rows")

    assert memory.ls("/", detail=False) == []


@pytest.mark.parametrize(
    ("contents", "skipped", "first_row"),
    [
        ("id,name\n1,Ann\n2,Bob\n", 0, ["1", "Ann"]),
        ("Sales report\nid,name\n1,Ann\n2,Bob\n", 1, ["1", "Ann"]),
        ("# exported\n# 2024\nid,name\n1,Ann\n2,Bob\n", 2, ["1", "Ann"]),
        (
            "".join(f"{number},x{number}\n" for number in range(500))
            + "7,y,z\n8,y,z\n9,y,z\n",
            500,
            ["7", "y", "z"],
        ),
    ],
    ids=["none", "title line", "comments", "real rows with fewer fields"],
)
def test_the_preview_reports_how_many_lines_it_skipped_above_the_table(
    client: TestClient,
    tmp_path: Path,
    contents: str,
    skipped: int,
    first_row: list[str],
) -> None:
    # CFX-APP-first-slice/AC-27: DuckDB can't tell a title line from real rows, so it must be said.
    path = tmp_path / "skips.csv"
    path.write_text(contents)

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert page["skipped_lines"] == skipped
    assert values(page)[0] == first_row


def test_skipped_lines_are_reported_on_later_pages_too(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27
    path = numbered_csv(tmp_path / "people.csv", 120)
    path.write_text("Sales report\n" + path.read_text())
    source = connect(client, path)

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 50}).json()

    assert page["skipped_lines"] == 1
    assert values(page)[0] == ["51", "name51"]


@pytest.mark.parametrize("file", ["short_row.csv", "extra_field_in_a_row.csv"])
def test_a_padded_read_skips_nothing(client: TestClient, file: str) -> None:
    # CFX-APP-first-slice/AC-27
    page = client.get(f"/api/sources/{connect(client, CSV_DIR / file)}/rows").json()

    assert page["skipped_lines"] == 0


def test_a_title_line_and_a_late_short_row_keep_the_same_columns_on_every_page(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27 and CFX-APP-first-slice/AC-31: the page past the short row is read
    # with padding, which must not bring the title line back as the column name.
    path = far_past_the_sniff_sample(tmp_path / "late.csv", "x\n")
    path.write_text("Sales report\n" + path.read_text())
    source = connect(client, path)

    first = client.get(f"/api/sources/{source}/rows").json()
    later = client.get(f"/api/sources/{source}/rows", params={"offset": 99_999}).json()

    assert names(first) == names(later) == ["id", "name"]
    assert later["skipped_lines"] == 1
    assert values(later)[:2] == [["100000", "name100000"], ["x", None]]


@pytest.mark.parametrize(
    ("before", "after", "row"),
    [
        pytest.param(
            b"\xff\xfe" + "id,city\n1,Paris\n".encode("utf-16-le"),
            "id,city\n1,Orléans\n".encode(),
            ["1", "Orléans"],
            id="utf-16 rewritten as utf-8",
        ),
        pytest.param(
            b"\xfe\xff" + "id,city\n1,Paris\n".encode("utf-16-be"),
            "id,city\n1,Orléans\n".encode(),
            ["1", "Orléans"],
            id="utf-16 be rewritten as utf-8",
        ),
        pytest.param(
            "id,city\n1,Orléans\n".encode("latin-1"),
            "id,city\n1,Orléans and more\n".encode(),
            ["1", "Orléans and more"],
            id="latin-1 rewritten as utf-8",
        ),
        pytest.param(
            b"id,city\n1,Paris\n",
            "id,city\n1,Orléans\n".encode("latin-1"),
            ["1", "Orléans"],
            id="utf-8 gains a latin-1 byte",
        ),
        pytest.param(
            b"id,city\n1,Paris\n",
            b"\xff\xfe" + "id,city\n1,Orléans\n".encode("utf-16-le"),
            ["1", "Orléans"],
            id="utf-8 rewritten as utf-16",
        ),
    ],
)
def test_a_file_rewritten_in_another_encoding_shows_its_current_text(
    client: TestClient, tmp_path: Path, before: bytes, after: bytes, row: list[str]
) -> None:
    # CFX-APP-first-slice/AC-31: read with the old encoding, this comes out garbled and looks valid.
    path = tmp_path / "rewritten.csv"
    path.write_bytes(before)
    source = connect(client, path)

    path.write_bytes(after)
    page = client.get(f"/api/sources/{source}/rows").json()

    assert names(page) == ["id", "city"]
    assert values(page) == [row]


def test_a_file_that_has_not_changed_keeps_its_saved_encoding(
    client: TestClient, tmp_path: Path
) -> None:
    # Detecting reads the whole file, so an unchanged file must not pay for that on every page.
    path = tmp_path / "same.csv"
    path.write_bytes("id,city\n1,Orl\xe9ans\n".encode("latin-1"))
    source = connect(client, path)
    saved = path.stat()

    path.write_bytes(b"id,city\n1,Orl\xc3\xa9ns\n")
    os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    assert path.stat().st_size == saved.st_size
    page = client.get(f"/api/sources/{source}/rows").json()

    assert values(page) == [["1", "OrlÃ©ns"]]


def test_api_responses_tell_the_browser_not_to_store_them(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-30: without this, row values reach the browser's disk cache.
    source = connect(client, numbered_csv(tmp_path / "people.csv", 3))

    for path in (
        f"/api/sources/{source}/rows",
        "/api/sources",
        "/api/workspace",
        "/api/session",
        "/api/sources/nothing/rows",
    ):
        assert client.get(path).headers["cache-control"] == "no-store", path
    client.cookies.clear()
    assert client.get("/api/sources").headers["cache-control"] == "no-store"


def test_a_page_says_where_it_starts(client: TestClient, tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-27: the screen labels rows it is still showing while the next page loads.
    source = connect(client, numbered_csv(tmp_path / "people.csv", 120))

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 50}).json()

    assert page["offset"] == 50


def every_row(client: TestClient, source: str) -> list[list[str | None]]:
    rows: list[list[str | None]] = []
    while True:
        page = client.get(
            f"/api/sources/{source}/rows", params={"offset": len(rows), "limit": 200}
        ).json()
        rows += values(page)
        if not page["has_next"]:
            return rows


@pytest.mark.parametrize(
    ("contents", "expected"),
    [
        (
            "issue,title\n#101,Fix login\n#102,Fix logout\n103,a\n104,b\n105,c\n",
            [
                ["#101", "Fix login"],
                ["#102", "Fix logout"],
                ["103", "a"],
                ["104", "b"],
                ["105", "c"],
            ],
        ),
        (
            "id,name\n1,a\n# a comment line\n2,b\n3,c\n4,d\n",
            [
                ["1", "a"],
                ["# a comment line", None],
                ["2", "b"],
                ["3", "c"],
                ["4", "d"],
            ],
        ),
    ],
    ids=["identifiers", "a comment line"],
)
def test_rows_starting_with_a_hash_are_shown(
    client: TestClient, tmp_path: Path, contents: str, expected: list
) -> None:
    # CFX-APP-first-slice/AC-27: found reviewing T06, DuckDB can decide # marks comments.
    path = tmp_path / "hash.csv"
    path.write_text(contents)

    assert every_row(client, connect(client, path)) == expected


def test_a_hash_in_every_second_row_keeps_every_row(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27
    path = tmp_path / "hash.csv"
    rows = [[f"#{n}" if n % 2 else str(n), f"x{n}"] for n in range(60)]
    path.write_text("id,name\n" + "".join(f"{a},{b}\n" for a, b in rows))

    assert every_row(client, connect(client, path)) == rows


def test_a_large_file_with_line_breaks_in_quotes_reads_true_to_the_end(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: found reviewing T06, DuckDB's parallel reader invented a row where
    # a read buffer ended inside a quoted value, and shifted every row after it.
    path = tmp_path / "large.csv"
    with path.open("w") as file:
        file.write("id,note,z\n")
        for number in range(1_500_000):
            file.write(f'{number},"line one\nline two {number}, x",z{number}\n')
    source = connect(client, path)

    for offset in (718_500, 750_000, 1_125_000, 1_499_950):
        page = client.get(
            f"/api/sources/{source}/rows", params={"offset": offset}
        ).json()
        expected = [str(number) for number in range(offset, offset + 50)]
        assert [row[0] for row in values(page)] == expected
    last = client.get(
        f"/api/sources/{source}/rows", params={"offset": 1_499_950}
    ).json()
    assert last["has_next"] is False


def test_a_line_break_in_quotes_and_an_uneven_row_can_both_be_shown(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31: found reviewing T06, every page of this file gave an error.
    path = tmp_path / "both.csv"
    path.write_text('id,note,z\n1,"l1\nl2",z\n2\n3,c,z\n')

    assert every_row(client, connect(client, path)) == [
        ["1", "l1\nl2", "z"],
        ["2", None, None],
        ["3", "c", "z"],
    ]


PLAIN_ROWS = "".join(f"{number},plain{number}\n" for number in range(30_000))


@pytest.mark.parametrize(
    ("late_rows", "expected"),
    [
        ('30001,"a\nb"\n', [["30001", "a\nb"]]),
        ('30001,"a""b"\n', [["30001", 'a"b']]),
        ('30001,"a,b"\n', [["30001", "a,b"]]),
        ('30001,5" pipe\n30002,ok\n', [["30001", '5" pipe'], ["30002", "ok"]]),
        ("30001,O'Brien\n", [["30001", "O'Brien"]]),
        ('30001,"a"b\n', [["30001", '"a"b']]),
    ],
    ids=[
        "line break",
        "doubled quote",
        "comma",
        "inch mark",
        "apostrophe",
        "text after quote",
    ],
)
def test_a_first_quote_after_the_sample_is_still_read_as_a_quote(
    client: TestClient, tmp_path: Path, late_rows: str, expected: list
) -> None:
    # CFX-APP-first-slice/AC-27: DuckDB decides how a file is quoted from a sample of its start.
    path = tmp_path / "late.csv"
    path.write_text("id,note\n" + PLAIN_ROWS + late_rows)
    source = connect(client, path)

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 30_000})

    assert page.status_code == 200
    assert values(page.json()) == expected


def test_a_late_quote_that_never_closes_may_lose_the_rows_after_it(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: the accepted limit, for malformed files only. If this starts
    # failing, DuckDB has improved, so the limit can come out of AC-27.
    path = tmp_path / "late.csv"
    path.write_text("id,note\n" + PLAIN_ROWS + '30001,"oops\n30002,ok\n')
    source = connect(client, path)

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 30_000}).json()

    assert values(page) == []


@pytest.mark.parametrize(
    ("contents", "row"),
    [
        (
            "id,note,n\n" + "".join(f"{n},'a,b',{n}\n" for n in range(60)),
            ["0", "a,b", "0"],
        ),
        (
            "id,note,n\n" + "".join(f'{n},"a\\"b",{n}\n' for n in range(40)),
            ["0", 'a"b', "0"],
        ),
        (
            "id,note,n\n" + "".join(f"{n},'a b',{n}\n" for n in range(60)),
            ["0", "a b", "0"],
        ),
    ],
    ids=["single quotes", "backslash escapes", "single quotes around a plain value"],
)
def test_quoting_seen_at_the_start_of_a_file_is_kept(
    client: TestClient, tmp_path: Path, contents: str, row: list[str]
) -> None:
    # CFX-APP-first-slice/AC-27: the standard quote is only for files whose sample shows none.
    path = tmp_path / "quoted.csv"
    path.write_text(contents)

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert names(page) == ["id", "note", "n"]
    assert values(page)[0] == row


FILLER = "".join(f"{number},name{number}\n" for number in range(5000))


@pytest.mark.parametrize(
    ("contents", "rows", "last"),
    [
        ("id,name\n" + FILLER + "7,x\r\n" + FILLER, 10_001, ["4999", "name4999"]),
        (
            "id,name\n"
            + "".join(f"{n},x" + ("\r\n" if n % 2 else "\n") for n in range(40)),
            40,
            ["39", "x"],
        ),
        ("id,name\n" + FILLER + "X\n", 5001, ["X", None]),
    ],
    ids=["one CRLF row", "CRLF on every other row", "a short row at row 5,000"],
)
def test_files_detection_used_to_refuse_connect_and_show_every_row(
    client: TestClient, tmp_path: Path, contents: str, rows: int, last: list
) -> None:
    # CFX-APP-first-slice/AC-20 and CFX-APP-first-slice/AC-27: found reviewing T06.
    path = tmp_path / "awkward.csv"
    path.write_bytes(contents.encode())

    response = client.post("/api/sources", json={"path": str(path)})

    assert response.status_code == 201, response.text
    assert response.json()["delimiter"] == ","
    shown = every_row(client, response.json()["id"])
    assert len(shown) == rows
    assert shown[-1] == last


def test_a_long_column_name_is_cut_and_marked(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-29: found reviewing T06.
    path = tmp_path / "long.csv"
    path.write_text(f"id,{'h' * 5000}\n1,a\n2,b\n")

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert page["columns"] == [
        {"value": "id", "cut": False},
        {"value": "h" * 1000, "cut": True},
    ]


@pytest.mark.parametrize("change", ["size", "modified time"])
def test_either_kind_of_change_detects_the_encoding_again(
    client: TestClient, tmp_path: Path, change: str
) -> None:
    # CFX-APP-first-slice/AC-31: a change can leave one of the two as it was.
    path = tmp_path / "changed.csv"
    path.write_bytes("id,city\n1,Orl\xe9ans\n".encode("latin-1"))
    source = connect(client, path)
    saved = path.stat()

    if change == "size":
        path.write_bytes(b"id,city\n1,Orl\xc3\xa9ans\n")
        os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns))
    else:
        path.write_bytes(b"id,city\n1,Orl\xc3\xa9ns\n")
        os.utime(path, ns=(saved.st_atime_ns, saved.st_mtime_ns + 10**10))
    page = client.get(f"/api/sources/{source}/rows").json()

    assert values(page)[0][1] == ("Orléans" if change == "size" else "Orléns")


def test_a_failing_drive_gives_a_clear_error(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-first-slice/AC-31: a dead network drive can't be made here, so it is simulated.
    source = connect(client, numbered_csv(tmp_path / "people.csv", 3))

    def failing_read(*args: object) -> None:
        raise OSError(5, "Input/output error")

    monkeypatch.setattr(sources_api, "read_page", failing_read)
    response = client.get(f"/api/sources/{source}/rows")

    assert response.status_code == 409
    assert "couldn't read" in response.json()["detail"]


def test_a_title_line_is_found_in_a_plain_file_named_like_a_compressed_one(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: the skip count comes from its own sniff, which must not decompress.
    path = tmp_path / "titled.csv.gz"
    path.write_text("Sales report\nid,name\n1,Ann\n2,Bob\n")

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert page["skipped_lines"] == 1
    assert names(page) == ["id", "name"]


def test_a_quoted_file_keeps_the_columns_it_was_connected_with(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: reading padded here would bring the real header back as a row.
    path = tmp_path / "short_header.csv"
    path.write_text(
        'name,city\n"Ann","Paris","France"\n"Bob","Lyon","France"\n"Cid","Nice","France"\n'
    )
    connected = client.post("/api/sources", json={"path": str(path)}).json()

    page = client.get(f"/api/sources/{connected['id']}/rows").json()

    assert names(page) == [column["name"] for column in connected["columns"]]
    assert values(page) == [["Bob", "Lyon", "France"], ["Cid", "Nice", "France"]]


def test_comment_lines_above_and_inside_a_table_hide_nothing(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: comments are off for the sniff too, so it agrees with the read.
    path = tmp_path / "comments.csv"
    path.write_text("# a\nid,name\n1,a\n2,b\n# b\n3,c\n4,d\n")

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert names(page)[0] == "# a"
    assert page["skipped_lines"] == 0
    assert [row[0] for row in values(page)] == ["id", "1", "2", "# b", "3", "4"]


def test_a_late_extra_field_is_an_error_even_if_an_early_row_is_short(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-31: found fixing T06, the fallback for mixed line endings dropped the
    # extra field in silence when it was also allowed to pad.
    path = tmp_path / "uneven.csv"
    path.write_text(
        "id,name\n1,a\n2\n" + PLAIN_ROWS + "LATE,private,extra\n" + PLAIN_ROWS
    )
    source = connect(client, path)

    response = client.get(f"/api/sources/{source}/rows", params={"offset": 29_999})

    assert response.status_code == 409
    assert "private" not in response.text


def test_mixed_line_endings_and_a_late_extra_field_lose_that_field(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-27: the accepted limit. Only DuckDB's lax mode reads mixed line endings,
    # and it drops an extra field past the sample. If this starts failing, AC-27 can be narrowed.
    path = tmp_path / "mixed.csv"
    path.write_bytes(
        b"id,name\n1,a\r\n2,b\n"
        + PLAIN_ROWS.encode()
        + b"LATE,private,extra\n"
        + PLAIN_ROWS.encode()
    )
    source = connect(client, path)

    page = client.get(f"/api/sources/{source}/rows", params={"offset": 30_002}).json()

    assert values(page)[0] == ["LATE", "private"]
