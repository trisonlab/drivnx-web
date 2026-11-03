"""Workspace setup over the API, CFX-APP-first-slice AC-14, AC-15 and AC-16."""

import sqlite3
import sys
from pathlib import Path

import pytest
from artifacts.workspace import open_workspace
from fastapi.testclient import TestClient

PORT = 8123
ORIGIN = f"http://127.0.0.1:{PORT}"
VALID = {"name": "Customer data", "ai_mode": "off"}


def test_a_new_workspace_reports_its_location_and_that_setup_is_needed(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-14
    response = client.get("/api/workspace")

    assert response.status_code == 200
    assert response.json() == {
        "location": str(workspace_dir),
        "name": None,
        "ai_mode": "off",
        "setup_complete": False,
    }


@pytest.mark.parametrize("ai_mode", ["off", "external", "local"])
def test_setup_saves_the_name_and_ai_mode(client: TestClient, ai_mode: str) -> None:
    # CFX-APP-first-slice/AC-14
    saved = client.put(
        "/api/workspace", json={"name": "Customer data", "ai_mode": ai_mode}
    )

    assert saved.status_code == 200
    expected = {"name": "Customer data", "ai_mode": ai_mode, "setup_complete": True}
    assert expected.items() <= saved.json().items()
    assert expected.items() <= client.get("/api/workspace").json().items()


def test_settings_can_be_changed_after_setup(client: TestClient) -> None:
    client.put("/api/workspace", json=VALID)

    client.put("/api/workspace", json={"name": "Renamed", "ai_mode": "local"})

    body = client.get("/api/workspace").json()
    assert (body["name"], body["ai_mode"]) == ("Renamed", "local")


def test_the_name_is_trimmed(client: TestClient) -> None:
    assert (
        client.put("/api/workspace", json={**VALID, "name": "  Sales  "}).json()["name"]
        == "Sales"
    )


@pytest.mark.parametrize(
    "body",
    [
        {"name": "", "ai_mode": "off"},
        {"name": "   ", "ai_mode": "off"},
        {"name": "x" * 101, "ai_mode": "off"},
        {"name": "Fine", "ai_mode": "on"},
        {"name": "Fine"},
        {"ai_mode": "off"},
        {"name": None, "ai_mode": "off"},
    ],
)
def test_invalid_settings_are_refused_and_nothing_is_saved(
    client: TestClient, body: dict[str, object]
) -> None:
    assert client.put("/api/workspace", json=body).status_code == 422
    assert client.get("/api/workspace").json()["setup_complete"] is False


def test_a_name_of_exactly_the_limit_is_allowed(client: TestClient) -> None:
    assert (
        client.put("/api/workspace", json={**VALID, "name": "x" * 100}).status_code
        == 200
    )


def test_a_name_with_html_or_quotes_is_stored_as_it_is(client: TestClient) -> None:
    name = "<script>'; DROP TABLE workspace_settings;--"

    client.put("/api/workspace", json={**VALID, "name": name})

    assert client.get("/api/workspace").json()["name"] == name


def test_the_workspace_api_needs_a_session(client: TestClient) -> None:
    # CFX-APP-first-slice/AC-08
    client.cookies.clear()

    assert client.get("/api/workspace").status_code == 401
    assert client.put("/api/workspace", json=VALID).status_code == 401


def test_settings_are_in_the_workspace_database_after_reopening(
    client: TestClient, workspace_dir: Path, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-15
    client.put("/api/workspace", json={"name": "Kept", "ai_mode": "local"})

    connection = sqlite3.connect(workspace_dir / "workspace.db")
    assert connection.execute(
        "SELECT name, ai_mode FROM workspace_settings"
    ).fetchall() == [("Kept", "local")]
    connection.close()


def test_a_real_older_workspace_is_backed_up_and_upgraded(workspace_dir: Path) -> None:
    # CFX-APP-first-slice/AC-19, with the product's own migrations: 0001 is what T03 shipped.
    workspace_dir.mkdir(mode=0o700)
    database = workspace_dir / "workspace.db"
    connection = sqlite3.connect(database)
    connection.execute(
        "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
    )
    connection.execute("INSERT INTO alembic_version VALUES ('0001')")
    connection.commit()
    connection.close()

    open_workspace(workspace_dir).close()

    connection = sqlite3.connect(database)
    assert connection.execute("SELECT * FROM workspace_settings").fetchall() == []
    assert connection.execute("SELECT version_num FROM alembic_version").fetchone() != (
        "0001",
    )
    connection.close()
    assert (workspace_dir / "workspace.db.bak-0001").is_file()


@pytest.mark.parametrize("ai_mode", ["off", "external", "local"])
def test_no_ai_mode_opens_a_network_connection(
    client: TestClient, ai_mode: str
) -> None:
    # CFX-APP-first-slice/AC-16: Python reports every outgoing connection to an audit hook.
    connections: list[object] = []
    recording = [True]

    def record(event: str, args: tuple[object, ...]) -> None:
        if recording[0] and event == "socket.connect":
            connections.append(args[1])

    sys.addaudithook(record)
    try:
        client.put("/api/workspace", json={"name": "Quiet", "ai_mode": ai_mode})
        client.get("/api/workspace")
    finally:
        recording[0] = False

    assert connections == []


RAW_SURROGATE = b'{"name":"\\ud800","ai_mode":"off"}'


def test_an_unencodable_name_is_refused_without_a_crash(client: TestClient) -> None:
    # Found in the T04 review: building the error response failed on the value it copied in.
    response = client.put(
        "/api/workspace",
        content=RAW_SURROGATE,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert client.get("/api/workspace").json()["setup_complete"] is False


@pytest.mark.parametrize(
    "body",
    [
        {"name": "submitted-value-" * 1000, "ai_mode": "off"},
        {"name": "Fine", "ai_mode": "submitted-value"},
        {"name": 12345, "ai_mode": "off", "extra": "submitted-value"},
    ],
)
def test_a_validation_error_says_what_is_wrong_without_copying_the_input(
    client: TestClient, body: dict[str, object]
) -> None:
    # T05 sends file paths through the same kind of route, and they mustn't come back in errors.
    response = client.put("/api/workspace", json=body)

    assert response.status_code == 422
    assert "submitted-value" not in response.text
    assert "12345" not in response.text
    assert len(response.content) < 1000
    assert {problem["field"] for problem in response.json()["detail"]} <= {
        "body.name",
        "body.ai_mode",
    }


def test_the_session_route_does_not_copy_its_input_either(client: TestClient) -> None:
    response = client.post("/api/session", json={"code": 12345})

    assert response.status_code == 422
    assert "12345" not in response.text


@pytest.mark.parametrize(
    "name",
    [
        "\u200b",  # zero-width space
        "\u200b \u200b",
        "\ufeff",  # byte order mark
        "\u202e",  # right-to-left override
        "a\u0000b",  # NUL
        "a\nb",
        "a\tb",
        "a\u2028b",  # line separator
        "\x7f",
    ],
)
def test_a_name_that_is_invisible_or_has_control_characters_is_refused(
    client: TestClient, name: str
) -> None:
    # Found in the T04 review: an invisible name left a blank heading with nothing to click.
    assert client.put("/api/workspace", json={**VALID, "name": name}).status_code == 422
    assert client.get("/api/workspace").json()["setup_complete"] is False


@pytest.mark.parametrize(
    "name",
    [
        "Données clients",
        "顧客データ",
        "بيانات العملاء",
        "\U0001f468\u200d\U0001f469\u200d\U0001f467 family",  # an emoji joined with zero-width joiners
        "\U0001f600",
        "Q3 sales (draft) #2",
    ],
)
def test_a_real_name_in_any_language_is_kept_exactly(
    client: TestClient, name: str
) -> None:
    assert client.put("/api/workspace", json={**VALID, "name": name}).status_code == 200
    assert client.get("/api/workspace").json()["name"] == name
