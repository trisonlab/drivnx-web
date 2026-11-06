"""Correcting how a file is read, CFX-APP-csv-profile AC-01, AC-02, AC-03, AC-05."""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest
from artifacts.workspace import open_workspace
from connectors import csv_files
from fastapi.testclient import TestClient
from local_api.app import create_app
from local_api.session import LaunchSession

from ..conftest import CLIENT_ORIGIN as ORIGIN
from ..conftest import CLIENT_PORT as PORT

CSV_DIR = Path(__file__).parents[2] / "testdata" / "csv"


def connect(client: TestClient, path: Path) -> dict:
    response = client.post("/api/sources", json={"path": str(path)})
    assert response.status_code == 201, response.text
    return response.json()


def settings_of(source: dict, **changes: object) -> dict:
    return {
        "delimiter": source["delimiter"],
        "encoding": source["encoding"],
        "has_header": source["has_header"],
        "skip_lines": source["skip_lines"],
        **changes,
    }


def save(client: TestClient, source: dict, **changes: object):
    return client.put(
        f"/api/sources/{source['id']}/settings", json=settings_of(source, **changes)
    )


def first_page(client: TestClient, source: dict) -> dict:
    response = client.get(f"/api/sources/{source['id']}/rows")
    assert response.status_code == 200, response.text
    return response.json()


def names(page: dict) -> list[str]:
    return [column["value"] for column in page["columns"]]


def values(page: dict) -> list[list[str | None]]:
    return [[cell["value"] for cell in row] for row in page["rows"]]


class RunningApp(TestClient):
    """A signed-in client that can stop, so the same workspace can be opened again."""

    close_workspace: Callable[[], None]


def restart(tmp_path: Path, workspace_dir: Path) -> RunningApp:
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir(exist_ok=True)
    (ui_dir / "index.html").write_text("<html></html>")
    workspace = open_workspace(workspace_dir)
    session = LaunchSession(PORT)
    app = RunningApp(
        create_app(session, PORT, workspace, ui_dir),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )
    app.close_workspace = workspace.close
    app.post("/api/session", json={"code": session.launch_code})
    return app


def test_a_source_reports_its_settings_and_that_skipping_is_automatic(
    client: TestClient,
) -> None:
    # CFX-APP-csv-profile/AC-01
    source = connect(client, CSV_DIR / "comma_utf8.csv")

    assert source["delimiter"] == ","
    assert source["encoding"] == "UTF-8"
    assert source["has_header"] is True
    assert source["skip_lines"] is None


@pytest.mark.parametrize(
    ("file", "changes", "expected_names", "expected_rows"),
    [
        pytest.param(
            "no_header_first_row_fits_no_type.csv",
            {"delimiter": "\t", "has_header": False},
            ["column0", "column1", "column2"],
            [["Ann", "N/A", "2024-01-15"], ["Bob", "5", "2024-01-16"]],
            id="a first row wrongly used as column names",
        ),
        pytest.param(
            "no_header_commas_in_text.csv",
            {"delimiter": "\t", "has_header": False},
            ["column0", "column1"],
            [["1", "1 Main St, Paris, France"], ["2", "2 High St, Lyon, France"]],
            id="the wrong delimiter",
        ),
        pytest.param(
            "title_line_tab_commas_in_text.csv",
            {"delimiter": "\t", "has_header": True, "skip_lines": 1},
            ["id", "address"],
            [["1", "1 Main St, Paris, France"], ["2", "2 High St, Lyon, France"]],
            id="a title line that wasn't skipped",
        ),
        pytest.param(
            "title_line.csv",
            {"has_header": False, "skip_lines": 0},
            ["column0", "column1", "column2"],
            [["Sales report", None, None], ["id", "name", "amount"]],
            id="lines skipped that the user wants to see",
        ),
        pytest.param(
            "title_line.csv",
            {"has_header": True, "skip_lines": 0},
            ["Sales report", "column1", "column2"],
            [["id", "name", "amount"], ["1", "Ann", "10.50"]],
            id="a skipped line that really is the header",
        ),
    ],
)
def test_saving_the_right_settings_changes_the_columns_and_the_preview(
    client: TestClient,
    file: str,
    changes: dict,
    expected_names: list[str],
    expected_rows: list[list[str | None]],
) -> None:
    # CFX-APP-csv-profile/AC-02
    source = connect(client, CSV_DIR / file)

    response = save(client, source, **changes)

    assert response.status_code == 200, response.text
    saved = response.json()
    assert [column["name"] for column in saved["columns"]] == expected_names
    for key, value in changes.items():
        assert saved[key] == value
    page = first_page(client, source)
    assert names(page) == expected_names
    assert values(page)[: len(expected_rows)] == expected_rows


def test_a_corrected_encoding_changes_the_text(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-02
    path = tmp_path / "people.csv"
    path.write_bytes("id,name\n1,Zoé\n".encode())
    source = connect(client, path)
    assert source["encoding"] == "UTF-8"

    response = save(client, source, encoding="Latin-1")

    assert response.status_code == 200, response.text
    assert values(first_page(client, source))[0] == ["1", "ZoÃ©"]


def test_lines_skipped_by_the_user_are_reported_by_the_preview(
    client: TestClient,
) -> None:
    # CFX-APP-csv-profile/AC-02
    source = connect(client, CSV_DIR / "title_line.csv")

    save(client, source, has_header=False, skip_lines=2)

    page = first_page(client, source)
    assert page["skipped_lines"] == 2
    assert values(page)[0] == ["1", "Ann", "10.50"]


def test_a_corrected_encoding_stays_when_the_file_changes(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-02: detecting again would quietly undo the user's choice.
    path = tmp_path / "people.csv"
    path.write_bytes("id,name\n1,Zoé\n".encode())
    source = connect(client, path)
    save(client, source, encoding="Latin-1")

    path.write_bytes("id,name\n1,Zoé\n2,José\n".encode())

    assert values(first_page(client, source))[1] == ["2", "JosÃ©"]


def test_corrected_settings_survive_a_restart(
    tmp_path: Path, workspace_dir: Path
) -> None:
    # CFX-APP-csv-profile/AC-02
    first_run = restart(tmp_path, workspace_dir)
    source = connect(first_run, CSV_DIR / "no_header_commas_in_text.csv")
    save(first_run, source, delimiter="\t", has_header=False, skip_lines=1)
    first_run.close_workspace()

    restarted = restart(tmp_path, workspace_dir)
    try:
        listed = restarted.get("/api/sources").json()[0]
        assert listed["delimiter"] == "\t"
        assert listed["has_header"] is False
        assert listed["skip_lines"] == 1
        assert [column["name"] for column in listed["columns"]] == [
            "column0",
            "column1",
        ]
        assert values(first_page(restarted, listed))[0] == [
            "2",
            "2 High St, Lyon, France",
        ]
    finally:
        restarted.close_workspace()


def test_the_saved_columns_come_from_one_current_dataset(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-csv-profile/AC-02: earlier observations must still point at what they measured.
    source = connect(client, CSV_DIR / "no_header_commas_in_text.csv")

    save(client, source, delimiter="\t", has_header=False)

    database = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        datasets = database.execute(
            "SELECT count(*), count(*) FILTER (WHERE replaced_at IS NULL) FROM dataset"
        ).fetchone()
    finally:
        database.close()
    assert datasets == (2, 1)


def test_saving_the_same_settings_changes_nothing(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-csv-profile/AC-02
    source = connect(client, CSV_DIR / "comma_utf8.csv")

    response = save(client, source)

    assert response.status_code == 200, response.text
    assert response.json()["columns"] == source["columns"]
    database = sqlite3.connect(workspace_dir / "workspace.db")
    try:
        assert database.execute("SELECT count(*) FROM dataset").fetchone() == (1,)
    finally:
        database.close()


@pytest.mark.parametrize(
    "changes",
    [
        pytest.param({"skip_lines": 99}, id="every line skipped"),
        pytest.param(
            {"skip_lines": 99, "has_header": False},
            id="every line skipped, with no header",
        ),
        pytest.param({"encoding": "UTF-8"}, id="an encoding the bytes don't fit"),
    ],
)
def test_settings_that_cannot_read_the_file_are_refused_and_the_old_ones_stay(
    client: TestClient, changes: dict
) -> None:
    # CFX-APP-csv-profile/AC-03
    source = connect(client, CSV_DIR / "comma_latin1.csv")
    before = first_page(client, source)

    response = save(client, source, **changes)

    assert response.status_code == 422
    assert "previous settings" in response.json()["detail"]
    assert client.get("/api/sources").json() == [source]
    assert first_page(client, source) == before


def test_mixed_line_endings_with_a_chosen_number_of_lines_are_refused(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-03: a lax read is the only one that manages these, and given a number
    # of lines to skip it can answer with no rows and the wrong columns.
    path = tmp_path / "mixed.csv"
    path.write_bytes(b"junk\nid,name\r\n1,Ann\n2,Bob\r\n3,Cid\n")
    source = connect(client, path)

    response = save(client, source, skip_lines=1)

    assert response.status_code == 422
    assert "previous settings" in response.json()["detail"]
    assert client.get("/api/sources").json() == [source]


def test_a_row_wider_than_the_table_is_an_error_and_never_cut(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-03: dropping the extra field would hide data without saying so.
    path = tmp_path / "wide.csv"
    rows = "".join(f"{number},Ann\n" for number in range(30_000))
    path.write_text(f"title\nid,name\n{rows}1,Ann,extra\n")
    source = connect(client, path)

    response = save(client, source, has_header=False, skip_lines=0)

    assert response.status_code == 200, response.text
    assert first_page(client, source)["rows"][0][0]["value"] == "title"
    last = client.get(f"/api/sources/{source['id']}/rows", params={"offset": 30_000})
    assert last.status_code == 409


def test_a_bad_byte_far_past_the_sample_is_refused(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-03: otherwise a later page would fail after the save had worked.
    path = tmp_path / "late.csv"
    good = "".join(f"{number},Ann\n" for number in range(500_000)).encode()
    path.write_bytes(b"id,name\n" + good + b"1,Zo\xe9\n")
    source = connect(client, path)
    assert source["encoding"] == "Latin-1"

    response = save(client, source, encoding="UTF-8")

    assert response.status_code == 422
    assert "previous settings" in response.json()["detail"]


def test_an_encoding_over_the_limit_is_refused_and_the_old_one_stays(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-03
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n")
    source = connect(client, path)
    monkeypatch.setattr(csv_files, "DECODED_IN_MEMORY_LIMIT_BYTES", 4)

    response = save(client, source, encoding="Windows-1252")

    assert response.status_code == 422
    assert "over 100 MB" in response.json()["detail"]
    assert client.get("/api/sources").json() == [source]


def test_a_file_that_has_gone_keeps_the_old_settings(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-03
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    path.unlink()

    response = save(client, source, has_header=False)

    assert response.status_code == 409
    assert client.get("/api/sources").json()[0]["has_header"] is True


@pytest.mark.parametrize(
    "changes",
    [
        {"delimiter": ":"},
        {"encoding": "ASCII"},
        {"skip_lines": -1},
        {"skip_lines": 1001},
    ],
)
def test_values_outside_the_choices_are_refused(
    client: TestClient, changes: dict
) -> None:
    # CFX-APP-csv-profile/AC-01
    source = connect(client, CSV_DIR / "comma_utf8.csv")

    assert save(client, source, **changes).status_code == 422
    assert client.get("/api/sources").json() == [source]


def test_settings_of_a_source_that_does_not_exist(client: TestClient) -> None:
    # CFX-APP-csv-profile/AC-01
    response = client.put(
        "/api/sources/nope/settings",
        json={
            "delimiter": ",",
            "encoding": "UTF-8",
            "has_header": True,
            "skip_lines": None,
        },
    )

    assert response.status_code == 404


def test_connecting_the_path_again_opens_the_corrected_source(
    client: TestClient,
) -> None:
    # CFX-APP-csv-profile/AC-05
    source = connect(client, CSV_DIR / "no_header_commas_in_text.csv")
    corrected = save(client, source, delimiter="\t", has_header=False).json()

    again = client.post("/api/sources", json={"path": source["path"]})

    assert again.status_code == 200
    assert again.json() == corrected


def test_saving_settings_needs_a_session_and_the_right_origin(
    client: TestClient,
) -> None:
    # CFX-APP-first-slice/AC-08 and CFX-APP-first-slice/AC-09, for this new route.
    source = connect(client, CSV_DIR / "comma_utf8.csv")
    url = f"/api/sources/{source['id']}/settings"
    body = settings_of(source)

    assert (
        client.put(url, json=body, headers={"Origin": "http://evil.test"}).status_code
        == 403
    )
    client.cookies.clear()
    assert client.put(url, json=body).status_code == 401
