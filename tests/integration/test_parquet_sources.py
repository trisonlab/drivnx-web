"""Connecting a Parquet file and reading its metadata, DRIVN-60 AC-07, AC-08, AC-09, AC-29 (DRIVN-61)."""

import sqlite3
import time
from pathlib import Path

import duckdb
import pytest
from alembic import command
from alembic.config import Config
from artifacts.schema import read_revision
from artifacts.workspace import MIGRATIONS_DIR, open_workspace
from fastapi.testclient import TestClient
from local_api import sources_api
from sqlalchemy import URL, create_engine
from sqlalchemy.pool import NullPool

from ..conftest import posix_only

ROWS = 10_240
ROW_GROUP_ROWS = 2_048


def duckdb_writer() -> str:
    """How DuckDB signs the files it writes, from DuckDB itself rather than from DrivnX."""
    version, build = duckdb.execute(
        "SELECT library_version, source_id FROM pragma_version()"
    ).fetchone()
    return f"DuckDB version {version} (build {build})"


@pytest.fixture
def events(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "events.parquet"
    path.parent.mkdir()
    with duckdb.connect() as connection:
        # One thread, so each row group holds exactly ROW_GROUP_ROWS rows.
        connection.execute("SET threads = 1")
        connection.execute(
            f"""
            COPY (
                SELECT i::BIGINT AS id, 'event ' || i AS name, DATE '2026-01-01' + (i % 365)::INTEGER AS day
                FROM range({ROWS}) t(i)
            ) TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE {ROW_GROUP_ROWS})
            """
        )
    return path


def connect(client: TestClient, path: Path | str) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def test_connecting_a_parquet_file_shows_its_metadata(
    client: TestClient, events: Path
) -> None:
    # DRIVN-60/AC-07
    source = connect(client, events)

    assert source["kind"] == "parquet"
    assert source["name"] == "events.parquet"
    assert source["path"] == str(events)
    assert source["size_bytes"] == events.stat().st_size
    assert source["available"] is True
    assert source["row_count"] == ROWS
    assert source["row_group_count"] == ROWS // ROW_GROUP_ROWS
    assert source["created_by"] == duckdb_writer()
    assert source["columns"] == [
        {"name": "id", "physical_type": "BIGINT", "normalised_type": "integer"},
        {"name": "name", "physical_type": "VARCHAR", "normalised_type": "string"},
        {"name": "day", "physical_type": "DATE", "normalised_type": "date"},
    ]
    assert client.get("/api/sources").json() == [source]


# DRIVN-60/AC-09: each Parquet type, its native type as DuckDB reads it and its type in ADR
# 0003's vocabulary, as the criterion lists them.
TYPES = [
    ("UBIGINT", "18446744073709551615::UBIGINT", "integer"),
    ("DECIMAL(38,2)", "12.50::DECIMAL(38,2)", "number"),
    ("TIMESTAMP_NS", "TIMESTAMP_NS '2026-01-02 03:04:05.123456789'", "timestamp"),
    ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ '2026-01-02 03:04:05+00'", "timestamp"),
    ("UUID", "'0b6a9c1e-0000-4000-8000-000000000001'::UUID", "string"),
    ("BLOB", "'\\x00\\xFF'::BLOB", "string"),
    ("INTEGER[]", "[1, 2]::INTEGER[]", "string"),
    ("STRUCT(a INTEGER)", "{'a': 1}::STRUCT(a INTEGER)", "string"),
    ("MAP(VARCHAR, INTEGER)", "MAP {'k': 1}::MAP(VARCHAR, INTEGER)", "string"),
]


def test_each_parquet_type_shows_its_native_and_normalised_type(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-09
    path = tmp_path / "types.parquet"
    columns = ", ".join(f"{value} AS c{index}" for index, (_, value, _) in enumerate(TYPES))
    with duckdb.connect() as connection:
        connection.execute(f"COPY (SELECT {columns}) TO '{path}' (FORMAT parquet)")

    source = connect(client, path)

    assert source["columns"] == [
        {"name": f"c{index}", "physical_type": native, "normalised_type": normalised}
        for index, (native, _, normalised) in enumerate(TYPES)
    ]


ENCRYPTED = "This Parquet file is encrypted, so DrivnX can't read it."
FOLDER = (
    "This path is a folder. Enter the path to a CSV, XLSX or Parquet file; "
    "a folder of Parquet files isn't supported yet."
)
REMOTE = "DrivnX reads files on this computer only. Enter the full path to a file."
NOT_PARQUET = "This file isn't a Parquet file, so it can't be read as one."


def refused(client: TestClient, path: Path | str) -> str:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 422, response.text
    assert client.get("/api/sources").json() == []
    return response.json()["detail"]


def test_an_encrypted_parquet_file_is_refused(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-08
    path = tmp_path / "secret.parquet"
    with duckdb.connect() as connection:
        connection.execute("PRAGMA add_parquet_key('key', '0123456789112345')")
        connection.execute(
            f"COPY (SELECT 1 AS a) TO '{path}' (FORMAT parquet, ENCRYPTION_CONFIG {{footer_key: 'key'}})"
        )

    assert refused(client, path) == ENCRYPTED


def test_a_folder_is_refused(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-08: a folder of Parquet files, as Spark writes them, comes later.
    folder = tmp_path / "events"
    folder.mkdir()
    (folder / "part-0.parquet").write_bytes(b"PAR1")

    assert refused(client, folder) == FOLDER


@pytest.mark.parametrize(
    "remote",
    [
        "s3://bucket/events.parquet",
        "https://example.com/events.parquet",
        # Local, but the user still types a plain path (agreed after the review of DRIVN-61).
        "file:///data/events.parquet",
    ],
)
def test_a_remote_path_is_refused(client: TestClient, remote: str) -> None:
    # DRIVN-60/AC-08
    assert refused(client, remote) == REMOTE


def test_a_drive_letter_with_two_slashes_isnt_a_remote_path(client: TestClient) -> None:
    # Windows reads C://data as C:\\data (review of DRIVN-61, finding 6). Elsewhere it's relative.
    assert refused(client, "C://data/events.parquet") != REMOTE


@posix_only
def test_a_device_is_refused_naming_no_kind_of_file(client: TestClient) -> None:
    # The message said "as a CSV" (DRIVN-60, Conflicts; review of DRIVN-61, finding 5).
    assert refused(client, "/dev/null") == "This path is not a regular file, so DrivnX can't read it."


def test_a_parquet_file_cut_short_is_refused(
    client: TestClient, events: Path, tmp_path: Path
) -> None:
    # DRIVN-60/AC-08: it starts like a Parquet file, and its footer is missing.
    path = tmp_path / "cut.parquet"
    whole = events.read_bytes()
    path.write_bytes(whole[: len(whole) // 2])

    detail = refused(client, path)

    assert detail == NOT_PARQUET
    assert str(tmp_path) not in detail


def test_a_file_named_parquet_that_isnt_one_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-08: neither a Parquet file nor text.
    path = tmp_path / "events.parquet"
    path.write_bytes(bytes(range(256)) * 4)

    assert refused(client, path) == NOT_PARQUET


def test_a_parquet_file_named_csv_is_read_as_parquet(
    client: TestClient, events: Path
) -> None:
    # DRIVN-60/AC-29: what the file is, not what it's called. The source says which it was.
    named_csv = events.rename(events.with_name("orders.csv"))

    source = connect(client, named_csv)

    assert source["kind"] == "parquet"
    assert source["row_count"] == ROWS


def test_csv_text_named_parquet_is_read_as_a_csv(client: TestClient, tmp_path: Path) -> None:
    # DRIVN-60/AC-29
    path = tmp_path / "sales.parquet"
    path.write_text("id,amount\n1,10.5\n2,7\n", encoding="utf-8", newline="")

    source = connect(client, path)

    assert source["kind"] == "csv"
    assert source["columns"] == [
        {"name": "id", "physical_type": "BIGINT", "normalised_type": "integer"},
        {"name": "amount", "physical_type": "DOUBLE", "normalised_type": "number"},
    ]


@pytest.mark.parametrize("header", ["PARENT_ID,NAME", "PAR1_CODE,QTY"])
def test_csv_text_that_starts_like_parquet_is_read_as_a_csv(
    client: TestClient, tmp_path: Path, header: str
) -> None:
    # DRIVN-60/AC-29: Parquet's magic bytes are four capital letters, which a CSV header can start
    # with; a Parquet file also ends with them (review of DRIVN-61, finding 1).
    path = tmp_path / "accounts.csv"
    path.write_text(f"{header}\n1,a\n2,b\n", encoding="utf-8", newline="")

    source = connect(client, path)

    assert source["kind"] == "csv"
    assert [column["name"] for column in source["columns"]] == header.split(",")


def test_a_file_gone_before_its_content_is_read_is_refused_without_its_path(
    client: TestClient, events: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Removed after the path was checked and before its first bytes are read (review of DRIVN-61,
    # finding 2). An unhandled error would answer 500 and log the path.
    checked = sources_api._readable_file

    def checked_then_removed(text: str) -> Path:
        path = checked(text)
        path.unlink()
        return path

    monkeypatch.setattr(sources_api, "_readable_file", checked_then_removed)

    detail = refused(client, events)

    assert detail == "DrivnX couldn't read this file."
    assert "events" not in detail


@pytest.mark.parametrize(
    ("method", "route", "body", "message"),
    [
        (
            "PUT",
            "/api/sources/{id}/settings",
            {"delimiter": ",", "encoding": "UTF-8", "has_header": True, "skip_lines": None},
            "A Parquet file has no file settings.",
        ),
        (
            "POST",
            "/api/profile-runs",
            {"source_id": "{id}", "mode": "fast"},
            "This Parquet file can't be profiled yet.",
        ),
    ],
)
def test_a_parquet_source_isnt_read_as_a_csv(
    client: TestClient,
    events: Path,
    method: str,
    route: str,
    body: dict | None,
    message: str,
) -> None:
    # A Parquet file has no CSV settings, and until profiling reads Parquet (DRIVN-63), it refuses it.
    source_id = connect(client, events)["id"]
    if body and body.get("source_id"):
        body = {**body, "source_id": source_id}

    response = client.request(method, route.format(id=source_id), json=body)

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == message
    assert client.get("/api/profile-runs").json() == []


@pytest.fixture(scope="module")
def forty_million_rows(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """AC-07's 40,000,000 rows in 326 row groups, in two columns to keep it to about 160 MB."""
    path = tmp_path_factory.mktemp("large") / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            f"""
            COPY (SELECT i AS id, i % 1000 AS bucket FROM range(40000000) t(i))
            TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE 122880)
            """
        )
    return path


def test_connecting_40_million_rows_takes_at_most_1_second(
    client: TestClient, forty_million_rows: Path
) -> None:
    # DRIVN-60/AC-07: within 1 s, as it reads the footer and no row.
    started = time.perf_counter()
    source = connect(client, forty_million_rows)
    took = time.perf_counter() - started
    print(f"\nConnect a 40,000,000-row Parquet file: {took:.3f} s")

    assert source["row_count"] == 40_000_000
    assert source["row_group_count"] == 326
    assert took <= 1


# Beyond the criterion's list (review of DRIVN-61, findings 3 and 4): a list of a type with
# parameters is still a list, and DRIVN-60's Conflicts section names the unsigned integers.
MORE_TYPES = [
    ("DECIMAL(10,2)[]", "[1.50]::DECIMAL(10,2)[]", "string"),
    ("UTINYINT", "1::UTINYINT", "integer"),
    ("USMALLINT", "1::USMALLINT", "integer"),
    ("UINTEGER", "1::UINTEGER", "integer"),
    # As TIMESTAMP WITH TIME ZONE is a timestamp (review of DRIVN-61, finding 7).
    ("TIME WITH TIME ZONE", "TIMETZ '01:02:03+00'", "time"),
]


def test_a_list_of_decimals_is_a_string_and_unsigned_integers_are_integers(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-09
    path = tmp_path / "more_types.parquet"
    columns = ", ".join(f"{value} AS c{index}" for index, (_, value, _) in enumerate(MORE_TYPES))
    with duckdb.connect() as connection:
        connection.execute(f"COPY (SELECT {columns}) TO '{path}' (FORMAT parquet)")

    source = connect(client, path)

    assert source["columns"] == [
        {"name": f"c{index}", "physical_type": native, "normalised_type": normalised}
        for index, (native, _, normalised) in enumerate(MORE_TYPES)
    ]


def workspace_text(workspace_dir: Path) -> str:
    connection = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def test_no_statistic_of_a_parquet_file_is_sent_or_saved(
    client: TestClient, tmp_path: Path, workspace_dir: Path
) -> None:
    # DRIVN-60/AC-07: a statistic's minimum and maximum are values from the data (ADR 0004).
    path = tmp_path / "planted.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            f"""
            COPY (
                SELECT * FROM (VALUES ('aardvark-planted-min', -31415.92653::DOUBLE),
                    ('zebra-planted-max', 27182.81828::DOUBLE))
                    t(name, amount)
            ) TO '{path}' (FORMAT parquet)
            """
        )
        statistics = connection.execute(
            "SELECT stats_min, stats_max FROM parquet_metadata(?)", [str(path)]
        ).fetchall()
    # The file holds them, so their absence below means DrivnX left them out.
    assert ("aardvark-planted-min", "zebra-planted-max") in statistics
    assert ("-31415.92653", "27182.81828") in statistics

    responses = [
        client.post("/api/sources", json={"path": str(path)}).text,
        client.get("/api/sources").text,
    ]

    for text in [*responses, workspace_text(workspace_dir)]:
        for planted in ("aardvark-planted-min", "zebra-planted-max", "31415.92653", "27182.81828"):
            assert planted not in text


def downgrade_to_0009(database: Path) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    engine = create_engine(URL.create("sqlite", database=str(database)), poolclass=NullPool)
    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.downgrade(config, "0009")
    finally:
        engine.dispose()


def source_rows(database: Path) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(
            "SELECT id, kind, path, delimiter, encoding, has_header, size_bytes, modified_at,"
            " skip_lines, corrected, host, port, database_name, user_name, tls_mode, ca_path,"
            " discovered_at, write_confirmed FROM source ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()


def test_a_workspace_goes_back_to_0009_with_its_csv_and_database_sources(
    tmp_path: Path,
) -> None:
    # The migration adding the xlsx and parquet kinds, up and down (DRIVN-61, Expected evidence).
    directory = tmp_path / "workspace"
    open_workspace(directory).close()
    database = directory / "workspace.db"
    connection = sqlite3.connect(database)
    with connection:
        connection.execute(
            "INSERT INTO source (id, kind, path, delimiter, encoding, has_header, size_bytes,"
            " modified_at, skip_lines, corrected) VALUES"
            " ('s1', 'csv', '/data/a.csv', ',', 'UTF-8', 1, 10, 't', 2, 1)"
        )
        connection.execute(
            "INSERT INTO source (id, kind, host, port, database_name, user_name, tls_mode,"
            " ca_path, write_confirmed) VALUES"
            " ('p1', 'postgres', 'h', 5432, 'db', 'u', 'verify-full', '/ca.pem', '{}')"
        )
    connection.close()
    expected = source_rows(database)

    downgrade_to_0009(database)

    assert read_revision(database) == "0009"
    assert source_rows(database) == expected
    connection = sqlite3.connect(database)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(source)")}
        assert not {"row_count", "row_group_count", "created_by"} & columns
    finally:
        connection.close()

    open_workspace(directory).close()
    assert read_revision(database) == "0010"
    assert source_rows(database) == expected


def test_a_workspace_with_a_parquet_source_stays_at_0010(
    client: TestClient, events: Path, workspace_dir: Path
) -> None:
    # A Parquet source has nowhere to go in the 0009 table, so it is refused, not dropped.
    connect(client, events)
    database = workspace_dir / "workspace.db"
    expected = source_rows(database)

    with pytest.raises(RuntimeError, match="stays at 0010"):
        downgrade_to_0009(database)

    assert read_revision(database) == "0010"
    assert source_rows(database) == expected


def test_a_folder_named_like_a_partition_adds_no_column(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-07: one file is read on its own, as partitioned datasets are out of scope.
    # DuckDB would otherwise list `year` and `id` from the folders, and `id` as text.
    folder = tmp_path / "year=2024" / "id=x"
    folder.mkdir(parents=True)
    path = folder / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute(f"COPY (SELECT 1::INTEGER AS id) TO '{path}' (FORMAT parquet)")

    source = connect(client, path)

    assert source["columns"] == [
        {"name": "id", "physical_type": "INTEGER", "normalised_type": "integer"}
    ]
