"""Connecting a CSV, CFX-APP-first-slice AC-20, AC-21, AC-22, AC-23, AC-24, AC-25, AC-26, AC-49."""

import hashlib
import os
import shutil
import sqlite3
import stat
import threading
from pathlib import Path

import duckdb
import pytest
from artifacts.sources import StoredSource, add_csv_source
from artifacts.workspace import open_workspace
from fastapi.testclient import TestClient

from ..conftest import posix_only

CSV_DIR = Path(__file__).parents[2] / "testdata" / "csv"


@pytest.fixture
def customers(tmp_path: Path) -> Path:
    path = tmp_path / "data" / "customers.csv"
    path.parent.mkdir()
    shutil.copy(CSV_DIR / "comma_utf8.csv", path)
    return path


def connect(client: TestClient, path: Path | str) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def database_text(workspace_dir: Path) -> str:
    connection = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        return "\n".join(connection.iterdump())
    finally:
        connection.close()


def test_connecting_a_csv_reports_the_file_and_how_it_is_written(
    client: TestClient, customers: Path
) -> None:
    # CFX-APP-first-slice/AC-22 and CFX-APP-first-slice/AC-24
    source = connect(client, customers)

    assert source["name"] == "customers.csv"
    assert source["path"] == str(customers)
    assert source["delimiter"] == ","
    assert source["encoding"] == "UTF-8"
    assert source["has_header"] is True
    assert source["size_bytes"] == customers.stat().st_size
    assert source["available"] is True
    assert source["modified_at"].startswith("20")
    assert source["columns"] == [
        {"name": "id", "physical_type": "BIGINT", "normalised_type": "integer"},
        {"name": "name", "physical_type": "VARCHAR", "normalised_type": "string"},
        {"name": "city", "physical_type": "VARCHAR", "normalised_type": "string"},
        {"name": "amount", "physical_type": "DOUBLE", "normalised_type": "number"},
        {"name": "joined", "physical_type": "DATE", "normalised_type": "date"},
    ]


def test_no_row_count_is_reported(client: TestClient, customers: Path) -> None:
    # CFX-APP-first-slice/AC-25: counting rows means reading the file, which is profiling (M3).
    source = connect(client, customers)

    assert not {key for key in source if "row" in key or "count" in key}


def test_connected_sources_are_listed_and_kept(
    client: TestClient, customers: Path
) -> None:
    # CFX-APP-first-slice/AC-15
    source = connect(client, customers)

    assert client.get("/api/sources").json() == [source]


def test_connecting_leaves_the_file_alone_and_copies_nothing(
    client: TestClient, customers: Path, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-20
    before = hashlib.sha256(customers.read_bytes()).hexdigest()
    modified = customers.stat().st_mtime_ns

    connect(client, customers)

    assert hashlib.sha256(customers.read_bytes()).hexdigest() == before
    assert customers.stat().st_mtime_ns == modified
    stored = {entry.name for entry in workspace_dir.rglob("*") if entry.is_file()}
    assert stored <= {
        ".drivnx.lock",
        "workspace.db",
        "workspace.db-wal",
        "workspace.db-shm",
    }


def test_no_row_values_reach_the_database(
    client: TestClient, customers: Path, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-26
    connect(client, customers)

    text = database_text(workspace_dir)
    assert "customers.csv" in text
    assert '"id"' in text or "'id'" in text
    for value in ("Ann", "Paris", "Orléans", "10.5", "2024-01-15", "Málaga"):
        assert value not in text


def test_a_stray_delimiter_puts_no_row_values_in_the_database(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-26: found in T05, a tab file with commas in its text stored a row.
    connect(client, CSV_DIR / "tab_commas_in_text.csv")

    text = database_text(workspace_dir)
    for value in ("Main St", "Paris", "France", "Lyon"):
        assert value not in text


@pytest.mark.parametrize(
    ("file", "kept", "not_kept"),
    [
        ("no_header_commas_in_text.csv", "Main St", ("High St", "Lyon", "Nice")),
        ("one_column_commas_in_text.csv", "Main St", ("High St", "Lyon", "Nice")),
        ("title_line_tab_commas_in_text.csv", "Main St", ("High St", "Lyon", "Nice")),
        ("header_one_field_short.csv", "Ann", ("Bob", "Lyon", "Cid", "Nice")),
        ("no_header_first_row_fits_no_type.csv", "Ann", ("Bob", "Cid", "2024-01-16")),
    ],
)
def test_only_the_row_used_as_column_names_reaches_the_database(
    client: TestClient,
    workspace_dir: Path,
    file: str,
    kept: str,
    not_kept: tuple[str, ...],
) -> None:
    # CFX-APP-first-slice/AC-26
    connect(client, CSV_DIR / file)

    text = database_text(workspace_dir)
    assert kept in text
    for value in not_kept:
        assert value not in text


def test_the_same_path_opens_the_existing_source(
    client: TestClient, customers: Path
) -> None:
    # The spec's assumption: connecting a path twice opens the existing source.
    first = connect(client, customers)

    second = client.post("/api/sources", json={"path": str(customers)})

    assert second.status_code == 200
    assert second.json() == first
    assert len(client.get("/api/sources").json()) == 1


@posix_only
def test_another_route_to_the_same_file_opens_the_existing_source(
    client: TestClient, customers: Path
) -> None:
    first = connect(client, customers)
    link = customers.with_name("link.csv")
    link.symlink_to(customers)

    second = client.post("/api/sources", json={"path": str(link)})
    third = client.post(
        "/api/sources", json={"path": f"{customers.parent}/../data/customers.csv"}
    )

    assert second.json()["id"] == first["id"]
    assert third.json()["id"] == first["id"]


def test_connecting_the_same_path_at_once_makes_one_source(
    client: TestClient, customers: Path
) -> None:
    results: list[int] = []

    def post() -> None:
        results.append(
            client.post("/api/sources", json={"path": str(customers)}).status_code
        )

    threads = [threading.Thread(target=post) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 8, "a request failed inside its thread"
    assert sorted(set(results)) in ([200, 201], [201])
    assert len(client.get("/api/sources").json()) == 1


def test_saving_the_same_path_twice_keeps_the_first(
    client: TestClient, customers: Path, workspace_dir: Path
) -> None:
    # Two requests can both finish scanning before either saves.
    first = connect(client, customers)
    workspace = client.app.state.workspace
    duplicate = StoredSource(
        id="another-id",
        path=customers,
        delimiter=";",
        encoding="Latin-1",
        has_header=False,
        size_bytes=1,
        modified_at="2020-01-01T00:00:00+00:00",
        fields=(),
    )

    assert add_csv_source(workspace.engine, duplicate) is False
    assert client.get("/api/sources").json() == [first]


def test_connecting_a_known_path_again_opens_it_without_rescanning(
    client: TestClient, customers: Path
) -> None:
    # A rescan could fail, or change what was reviewed. Re-profiling is a separate action (M3).
    first = connect(client, customers)
    customers.write_bytes(b"")

    second = client.post("/api/sources", json={"path": str(customers)})

    assert second.status_code == 200
    assert second.json()["columns"] == first["columns"]


def test_a_home_relative_path_is_expanded(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    shutil.copy(CSV_DIR / "comma_utf8.csv", tmp_path / "home.csv")

    assert connect(client, "~/home.csv")["name"] == "home.csv"


@posix_only
@pytest.mark.parametrize(
    ("kind", "message"),
    [
        ("missing", "no file"),
        ("folder", "folder"),
        ("relative", "full path"),
        ("device", "not a regular file"),
        ("link to device", "not a regular file"),
        ("fifo", "not a regular file"),
        ("unreadable", "permission"),
        ("empty", "empty"),
        ("binary", "UTF-16"),
        ("utf-16 no bom", "UTF-16"),
        ("utf-16 be cut short", "can't be read as a CSV"),
        ("utf-16 be bad surrogate", "can't be read as a CSV"),
        ("symlink loop", "couldn't read"),
    ],
)
def test_a_bad_path_gets_a_specific_error_and_adds_no_source(
    client: TestClient, tmp_path: Path, kind: str, message: str
) -> None:
    # CFX-APP-first-slice/AC-21
    target = tmp_path / "target.csv"
    paths: dict[str, str] = {
        "missing": str(tmp_path / "nothing.csv"),
        "folder": str(tmp_path),
        "relative": "data/file.csv",
        "device": "/dev/zero",
    }
    if kind == "link to device":
        target.symlink_to("/dev/zero")
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "unreadable":
        shutil.copy(CSV_DIR / "comma_utf8.csv", target)
        target.chmod(0)
    elif kind == "empty":
        shutil.copy(CSV_DIR / "empty.csv", target)
    elif kind == "binary":
        shutil.copy(CSV_DIR / "binary.csv", target)
    elif kind == "utf-16 no bom":
        shutil.copy(CSV_DIR / "utf16_no_bom.csv", target)
    elif kind == "utf-16 be cut short":
        target.write_bytes(b"\xfe\xff\x00i\x00d\x00\n\x00")
    elif kind == "utf-16 be bad surrogate":
        target.write_bytes(b"\xfe\xff\x00a\x00,\x00b\x00\n\xd8\x00\x00,\x00c\x00\n")
    elif kind == "symlink loop":
        target.symlink_to(target)

    response = client.post("/api/sources", json={"path": paths.get(kind, str(target))})

    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert client.get("/api/sources").json() == []


@posix_only
def test_a_link_to_a_name_that_is_not_valid_text_gets_a_specific_error(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-21: a request can't carry this name, but a link can lead to it.
    name = os.fsencode(tmp_path) + b"/caf\xe9.csv"
    try:
        Path(os.fsdecode(name)).write_bytes((CSV_DIR / "comma_utf8.csv").read_bytes())
    except OSError:
        pytest.skip("this filesystem only allows names that are valid text")
    link = tmp_path / "link.csv"
    link.symlink_to(os.fsdecode(name))

    response = client.post("/api/sources", json={"path": str(link)})

    assert response.status_code == 422
    assert "aren't valid text" in response.json()["detail"]
    assert client.get("/api/sources").json() == []


@pytest.mark.parametrize(
    "body",
    [
        b'{"path":""}',
        b'{"path":"/tmp/a\\u0000b.csv"}',
        b'{"path":"\\ud800"}',
        b'{"path":123}',
        b"{}",
    ],
)
def test_an_invalid_path_is_refused_without_a_crash(
    client: TestClient, body: bytes
) -> None:
    # CFX-APP-first-slice/AC-21
    response = client.post(
        "/api/sources", content=body, headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert client.get("/api/sources").json() == []


def test_errors_do_not_repeat_the_path(client: TestClient, tmp_path: Path) -> None:
    response = client.post(
        "/api/sources", json={"path": str(tmp_path / "secret-name.csv")}
    )

    assert "secret-name" not in response.text


def test_a_moved_file_is_listed_as_unavailable_and_the_app_keeps_working(
    client: TestClient, customers: Path
) -> None:
    # CFX-APP-first-slice/AC-23
    connect(client, customers)

    customers.rename(customers.with_name("moved.csv"))

    sources = client.get("/api/sources").json()
    assert sources[0]["available"] is False
    assert sources[0]["columns"]
    assert client.get("/api/workspace").status_code == 200


def test_a_file_that_comes_back_is_available_again(
    client: TestClient, customers: Path
) -> None:
    # CFX-APP-first-slice/AC-23
    connect(client, customers)
    moved = customers.with_name("moved.csv")
    customers.rename(moved)
    assert client.get("/api/sources").json()[0]["available"] is False

    moved.rename(customers)

    assert client.get("/api/sources").json()[0]["available"] is True


@posix_only
def test_a_source_in_a_folder_that_cannot_be_read_is_unavailable_and_the_rest_still_list(
    client: TestClient, customers: Path, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-23: one unreachable source must not hide the others.
    connect(client, customers)
    other = tmp_path / "other.csv"
    shutil.copy(CSV_DIR / "comma_utf8.csv", other)
    connect(client, other)

    customers.parent.chmod(0)
    try:
        response = client.get("/api/sources")
    finally:
        customers.parent.chmod(0o700)

    assert response.status_code == 200
    assert [source["available"] for source in response.json()] == [False, True]


def test_the_sources_api_needs_a_session(client: TestClient, customers: Path) -> None:
    # CFX-APP-first-slice/AC-08
    client.cookies.clear()

    assert client.get("/api/sources").status_code == 401
    assert client.post("/api/sources", json={"path": str(customers)}).status_code == 401


def test_connecting_gives_duckdb_the_workspace_temp_directory(
    client: TestClient,
    customers: Path,
    workspace_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-first-slice/AC-49
    configs: list[dict] = []
    real_connect = duckdb.connect

    def recording_connect(*args, **kwargs):
        configs.append(kwargs["config"])
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", recording_connect)

    connect(client, customers)

    assert [config["temp_directory"] for config in configs] == [
        str(workspace_dir / "duckdb-tmp")
    ]


@posix_only
def test_duckdb_spills_inside_the_workspace_and_leftovers_are_removed_at_startup(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-49
    directory = tmp_path / "workspace"
    workspace = open_workspace(directory)
    temp = workspace.temp_directory
    assert temp == directory / "duckdb-tmp"
    assert stat.S_IMODE(temp.stat().st_mode) == 0o700
    (temp / "leftover.tmp").write_text("spilled source data")
    workspace.close()

    reopened = open_workspace(directory)
    reopened.close()

    assert list(temp.iterdir()) == []
