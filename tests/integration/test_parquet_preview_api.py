"""Paging through a Parquet file's rows, DRIVN-60 AC-10 (DRIVN-62)."""

import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import duckdb
import pytest
from connectors import parquet_files
from connectors.csv_files import duckdb_connection
from fastapi.testclient import TestClient

from ..conftest import posix_only, windows_forbids
from .test_preview_api import everything_stored, names, values


def written(path: Path, select: str) -> Path:
    """A Parquet file DuckDB writes from `select`: in one thread, so its rows keep their order, and
    uncompressed, so a test can find a value in its bytes."""
    with duckdb.connect() as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            f"COPY ({select}) TO '{path}' (FORMAT parquet, COMPRESSION uncompressed)"
        )
    return path


def numbered(path: Path, rows: int) -> Path:
    return written(
        path, f"SELECT i::BIGINT AS id, 'event ' || i AS name FROM range({rows}) t(i)"
    )


def connect(client: TestClient, path: Path) -> str:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "parquet"
    return response.json()["id"]


def test_the_preview_opens_on_the_first_50_rows(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10
    source = connect(client, numbered(tmp_path / "events.parquet", 120))

    response = client.get(f"/api/sources/{source}/rows")

    assert response.status_code == 200, response.text
    page = response.json()
    assert names(page) == ["id", "name"]
    assert len(page["rows"]) == 50
    assert values(page)[0] == ["0", "event 0"]
    assert values(page)[49] == ["49", "event 49"]
    assert page["has_next"] is True
    assert page["skipped_lines"] == 0


@pytest.fixture
def in_new_york(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """This process's time zone is New York's until the test ends."""
    monkeypatch.setenv("TZ", "America/New_York")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


@posix_only
@pytest.mark.usefixtures("in_new_york")
def test_a_moment_shows_in_utc_as_the_file_stores_it(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10, decided with the user on 2026-11-26: not this computer's time zone.
    path = written(
        tmp_path / "moments.parquet",
        "SELECT TIMESTAMPTZ '2024-01-15 10:00:00.5+00' AS at",
    )

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert values(page) == [["2024-01-15 10:00:00.5+00"]]


def test_binary_shows_every_byte_in_hex_as_a_database_preview_does(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10, decided with the user on 2026-11-26: `A` (0x41) isn't shown as a letter.
    path = written(
        tmp_path / "bytes.parquet",
        "SELECT * FROM (VALUES (1, from_hex('DEAD41')), (2, NULL::BLOB)) t(id, payload)",
    )

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert values(page) == [["1", "\\xdead41"], ["2", None]]


@pytest.mark.parametrize(
    ("params", "status", "rows", "has_next"),
    [
        ({"limit": 200}, 200, 200, True),
        ({"limit": 201}, 422, None, None),
        ({"offset": 250}, 200, 50, False),
        ({"offset": 300}, 200, 0, False),
    ],
)
def test_up_to_200_rows_a_page_when_asked(
    client: TestClient,
    tmp_path: Path,
    params: dict,
    status: int,
    rows: int | None,
    has_next: bool | None,
) -> None:
    # DRIVN-60/AC-10: the CSV preview's limits, reused as they are.
    source = connect(client, numbered(tmp_path / "events.parquet", 300))

    response = client.get(f"/api/sources/{source}/rows", params=params)

    assert response.status_code == status
    if rows is not None:
        assert len(response.json()["rows"]) == rows
        assert response.json()["has_next"] is has_next


@pytest.mark.parametrize(
    ("length", "kept", "cut"),
    [(1000, 1000, False), (1001, 1000, True), (50_000, 1000, True)],
)
def test_a_cell_over_1000_characters_is_cut_and_marked(
    client: TestClient, tmp_path: Path, length: int, kept: int, cut: bool
) -> None:
    # DRIVN-60/AC-10
    path = written(
        tmp_path / "notes.parquet",
        f"SELECT * FROM (VALUES (1, repeat('é', {length})), (2, 'short')) t(id, note)",
    )

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert page["rows"][0][1] == {"value": "é" * kept, "cut": cut}
    assert page["rows"][1][1] == {"value": "short", "cut": False}


def refused(client: TestClient, source: str) -> str:
    response = client.get(f"/api/sources/{source}/rows")
    assert response.status_code == 409, response.text
    return response.json()["detail"]


def test_a_file_that_has_gone_says_so(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-10: the CSV preview's message.
    path = numbered(tmp_path / "events.parquet", 10)
    source = connect(client, path)
    path.unlink()

    assert refused(client, source) == (
        "DrivnX can't find this file. It may have moved or been deleted."
    )


@pytest.mark.parametrize(
    "replacement",
    [b"id,name\n1,event 1\n", b"PAR1 cut short"],
    ids=["text", "cut-short"],
)
def test_a_file_replaced_by_something_else_says_so_without_its_path(
    client: TestClient, tmp_path: Path, replacement: bytes
) -> None:
    # DRIVN-60/AC-10: DuckDB's own message names the path.
    path = numbered(tmp_path / "events.parquet", 10)
    source = connect(client, path)
    path.write_bytes(replacement)

    detail = refused(client, source)

    assert detail == "This file isn't a Parquet file, so it can't be read as one."
    assert "events" not in detail


@posix_only
def test_a_file_that_cant_be_read_gives_a_clear_message(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10: the CSV preview's message.
    path = numbered(tmp_path / "events.parquet", 10)
    source = connect(client, path)
    path.chmod(0)

    try:
        detail = refused(client, source)
    finally:
        path.chmod(0o600)

    assert detail == "DrivnX doesn't have permission to read this file."


def test_a_preview_is_never_stored_by_drivnx_or_the_browser(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # DRIVN-60/AC-10: `Cache-Control: no-store`, and no file in the workspace, its WAL included,
    # holds a previewed value. Nor does DuckDB's spill folder or the folder DrivnX runs in.
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    path = written(
        tmp_path / "planted.parquet",
        "SELECT * FROM (VALUES (1, 'Zanzibar-7731'), (2, 'Quillfeather')) t(id, secret)",
    )
    source = connect(client, path)

    response = client.get(f"/api/sources/{source}/rows")
    client.get(f"/api/sources/{source}/rows", params={"offset": 1, "limit": 200})

    assert response.status_code == 200, response.text
    assert "Zanzibar-7731" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert (workspace_dir / "workspace.db").is_file()
    stored = everything_stored(workspace_dir)
    for value in ("Zanzibar-7731", "Quillfeather"):
        assert value.encode() not in stored
    assert list((workspace_dir / "duckdb-tmp").iterdir()) == []
    assert list(cwd.iterdir()) == []


def test_every_type_reads_as_text(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-10, with AC-09's types: nested values keep their shape, and nothing is rounded.
    path = written(
        tmp_path / "types.parquet",
        """
        SELECT
            18446744073709551615::UBIGINT AS big,
            1234.50::DECIMAL(38,2) AS money,
            TIMESTAMP_NS '2024-01-15 10:00:00.123456789' AS nanos,
            'a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11'::UUID AS id,
            [1, NULL, 3]::INTEGER[] AS tags,
            {'a': 1}::STRUCT(a INTEGER) AS device,
            MAP {'k': 1} AS attributes,
            NULL::VARCHAR AS nothing
        """,
    )

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert values(page) == [
        [
            "18446744073709551615",
            "1234.50",
            "2024-01-15 10:00:00.123456789",
            "a0eebc99-9c0b-4ef8-bb6d-6bb9bd380a11",
            "[1, NULL, 3]",
            "{'a': 1}",
            "{k=1}",
            None,
        ]
    ]


def test_column_names_are_shown_as_written_and_never_read_as_sql(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10
    path = written(
        tmp_path / "names.parquet",
        'SELECT 1 AS "it\'s ""quoted""", 2 AS "select", 3 AS "a); DROP TABLE x; --"',
    )

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert names(page) == ['it\'s "quoted"', "select", "a); DROP TABLE x; --"]
    assert values(page) == [["1", "2", "3"]]


def test_a_file_with_no_rows_shows_its_columns_and_no_next_page(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10
    source = connect(client, numbered(tmp_path / "empty.parquet", 0))

    page = client.get(f"/api/sources/{source}/rows").json()

    assert names(page) == ["id", "name"]
    assert page["rows"] == []
    assert page["has_next"] is False


@windows_forbids
def test_a_name_with_wildcards_previews_only_the_file_it_names(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10: DuckDB reads `*`, `?` and `[` as a pattern, which here matches both files.
    numbered(tmp_path / "wi.parquet", 3)
    source = connect(client, numbered(tmp_path / "w[ir]*.parquet", 1))

    page = client.get(f"/api/sources/{source}/rows").json()

    assert values(page) == [["0", "event 0"]]


def test_a_folder_named_like_a_partition_adds_nothing_to_the_file(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10: one file is read on its own, as partitioned datasets are out of scope.
    # DuckDB would otherwise read `id=x` as a column, and show it in place of the file's own.
    folder = tmp_path / "year=2024" / "id=x"
    folder.mkdir(parents=True)
    path = written(folder / "events.parquet", "SELECT 1::INTEGER AS id, 'a' AS name")

    page = client.get(f"/api/sources/{connect(client, path)}/rows").json()

    assert names(page) == ["id", "name"]
    assert values(page) == [["1", "a"]]


def test_a_damaged_value_says_a_row_cant_be_read_without_the_value_or_path(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-10: the file is still Parquet, and its metadata still reads, so saying it isn't
    # would be wrong. DuckDB's message names the path and quotes the value.
    path = written(
        tmp_path / "events.parquet",
        "SELECT i AS id, 'value-' || lpad(i::VARCHAR, 4, '0') AS v FROM range(1000) t(i)",
    )
    source = connect(client, path)
    damaged = bytearray(path.read_bytes())
    at = damaged.find(b"value-0500")
    assert at > 0, "the value isn't stored as plain text, so this test proves nothing"
    damaged[at] = 0xFF
    path.write_bytes(bytes(damaged))

    detail = refused(client, source)

    assert detail == "A row in this file can't be read, so this page can't be shown."


def test_running_out_of_memory_says_so(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-60/AC-10: not that a row can't be read, nor that the file isn't Parquet. DuckDB is
    # given 8 MB here, so a page of 3 MB values can't be built.
    path = written(
        tmp_path / "big.parquet",
        "SELECT i AS id, repeat('x', 3000000) AS v FROM range(60) t(i)",
    )
    source = connect(client, path)

    @contextmanager
    def small(temp_directory: Path) -> Iterator[duckdb.DuckDBPyConnection]:
        with duckdb_connection(temp_directory) as connection:
            connection.execute("SET memory_limit = '8MB'; SET threads = 1")
            yield connection

    monkeypatch.setattr(parquet_files, "duckdb_connection", small)

    assert refused(client, source) == "DrivnX ran out of memory showing this page."
