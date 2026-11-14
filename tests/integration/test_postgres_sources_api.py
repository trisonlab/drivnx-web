"""Adding a PostgreSQL source through the API, CFX-APP-postgres AC-01, AC-02, AC-03, AC-05, AC-07
(DRIVN-47)."""

import json
from pathlib import Path

import pytest
from connectors import postgres
from local_api.postgres_api import UNTIL_STOP
from fastapi.testclient import TestClient

from .. import postgres_fixture
from ..conftest import StartDrivnX
from .test_session import sign_in

DETAILS = {
    "host": postgres_fixture.HOST,
    "port": postgres_fixture.PORT,
    "database": postgres_fixture.DATABASE,
    "user": postgres_fixture.USER,
    "password": postgres_fixture.PASSWORD,
    "tls_mode": "prefer",
}


def test_testing_details_that_work_shows_the_server_and_saves_nothing(
    client: TestClient, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-02: "when the user tests ... the connection".
    response = client.post("/api/sources/postgres/test", json=DETAILS)

    assert response.status_code == 200, response.text
    assert response.json()["encrypted"] is True
    assert response.json()["server_version"].split(".")[0].isdigit()
    assert client.get("/api/sources").json() == []


def test_a_saved_connection_is_listed_under_its_database_with_how_its_password_is_kept(
    client: TestClient, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-02 and AC-05: there's no credential store yet (DRIVN-48), so the password
    # is kept until DrivnX stops, and the screen says so.
    response = client.post("/api/sources/postgres", json=DETAILS)

    assert response.status_code == 201, response.text
    saved = response.json()
    assert {key: saved[key] for key in ("kind", "name", "host", "port", "database", "user")} == {
        "kind": "postgres",
        "name": "drivnx_fixture",
        "host": "127.0.0.1",
        "port": 54329,
        "database": "drivnx_fixture",
        "user": "drivnx_fixture",
    }
    assert saved["tls_mode"] == "prefer" and saved["ca_path"] is None
    assert saved["password_kept"] == UNTIL_STOP
    assert saved["password_needed"] is False
    assert saved["encrypted"] is True and saved["server_version"]
    assert "password" not in saved
    assert client.get("/api/sources").json() == [saved]


def test_saving_the_same_connection_again_gives_the_source_it_already_is(
    client: TestClient, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-02, as connecting a CSV path again does.
    first = client.post("/api/sources/postgres", json=DETAILS).json()

    again = client.post("/api/sources/postgres", json=DETAILS)

    assert again.status_code == 200
    assert again.json()["id"] == first["id"]
    assert len(client.get("/api/sources").json()) == 1


CA = {"tls_mode": "verify-full", "ca_path": str(postgres_fixture.CA_CERTIFICATE)}


@pytest.mark.parametrize(
    ("saved", "again", "says"),
    [
        ({}, CA, "TLS mode prefer"),
        (CA, {}, "TLS mode verify-full"),
        # A file that isn't there: a connection tried with it would answer 422 instead.
        (
            CA,
            {**CA, "ca_path": str(Path("/elsewhere/ca.crt").absolute())},
            "another CA certificate",
        ),
    ],
)
def test_saving_a_saved_connection_with_other_tls_settings_is_refused_and_changes_nothing(
    client: TestClient, postgres_server: str, saved: dict, again: dict, says: str
) -> None:
    # Found in review: the new settings were dropped, and the save looked as if it had worked.
    first = client.post("/api/sources/postgres", json={**DETAILS, **saved}).json()

    response = client.post("/api/sources/postgres", json={**DETAILS, **again})

    assert response.status_code == 409
    assert says in response.json()["detail"]
    assert client.get("/api/sources").json() == [first]


@pytest.mark.parametrize("path", ["/api/sources/postgres/test", "/api/sources/postgres"])
@pytest.mark.parametrize(
    ("change", "says"),
    [
        (
            {"password": "not the password"},  # pragma: allowlist secret, ai-skills: allow
            "The server refused this user name and password.",
        ),
        ({"database": "no_such_database"}, "The server has no database with this name."),
        ({"host": "no-such-host.invalid"}, "DrivnX couldn't find a server with this host name."),
        (
            {"user": postgres_fixture.PLAIN_USER, "password": postgres_fixture.PLAIN_PASSWORD,
             "tls_mode": "require"},
            "The server won't encrypt a connection for this user, and the TLS mode requires it.",
        ),
    ],
)
def test_a_connection_that_fails_says_which_way_and_nothing_is_saved(
    client: TestClient, postgres_server: str, path: str, change: dict, says: str
) -> None:
    # CFX-APP-postgres/AC-03: the connector proves every case; this proves the API passes the
    # message on, as written for the user, and saves nothing.
    response = client.post(path, json={**DETAILS, **change})

    assert response.status_code == 422
    assert response.json() == {"detail": says}
    assert client.get("/api/sources").json() == []


@pytest.mark.parametrize(
    "change",
    [
        # CFX-APP-postgres/AC-01: no field for a connection string or URL.
        {"url": "postgresql://drivnx_fixture@127.0.0.1:54329/drivnx_fixture"},
        {"dsn": "host=127.0.0.1"},
        {"host": "postgresql://127.0.0.1:54329"},
        {"host": "127.0.0.1 sslmode=disable"},
        {"host": "one.example,two.example"},
        {"host": "/var/run/postgresql"},
        {"host": ""},
        {"port": 0},
        {"port": 65536},
        {"database": ""},
        {"database": "x" * 64},
        {"user": "line\nbreak"},
        {"password": ""},
        {"password": "Pl4nted\x00rest"},  # pragma: allowlist secret, ai-skills: allow
        {"password": "Pl4nted\ud800rest"},  # pragma: allowlist secret, ai-skills: allow
        {"tls_mode": "disable"},
        {"tls_mode": "verify-full"},
        {"tls_mode": "prefer", "ca_path": "/certs/ca.crt"},
        {"tls_mode": "verify-full", "ca_path": "certs/ca.crt"},
    ],
)
def test_details_that_cant_be_right_are_refused_without_repeating_them(
    client: TestClient, change: dict
) -> None:
    # CFX-APP-postgres/AC-01: the fields and TLS modes the form offers, and nothing else. Refused
    # as input, a list of problems, before any connection is tried with them.
    # Escaped as JSON allows, as a lone surrogate can't be sent as UTF-8.
    response = client.post(
        "/api/sources/postgres/test",
        content=json.dumps({**DETAILS, **change}),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert "Pl4nted" not in response.text and "sslmode" not in response.text


def test_every_field_but_the_ca_certificate_is_needed(client: TestClient) -> None:
    # CFX-APP-postgres/AC-01: the form's defaults are the form's (port 5432, prefer).
    for field in ("host", "port", "database", "user", "password", "tls_mode"):
        body = {key: value for key, value in DETAILS.items() if key != field}
        assert client.post("/api/sources/postgres/test", json=body).status_code == 422, field


def test_a_platform_with_no_bundled_extension_is_told_so_when_it_tests(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-postgres/AC-07: the message the screen shows.
    monkeypatch.setattr(postgres, "EXTENSIONS", tmp_path / "none")

    response = client.post("/api/sources/postgres/test", json=DETAILS)

    assert response.status_code == 422
    assert response.json()["detail"].startswith("PostgreSQL isn't available on this platform (")


QUOTES = {
    **DETAILS,
    "user": postgres_fixture.QUOTES_USER,
    "password": postgres_fixture.QUOTES_PASSWORD,
}


def test_after_a_restart_a_source_asks_for_its_password_and_keeps_the_one_that_works(
    start_drivnx: StartDrivnX, tmp_path: Path, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-02 and AC-05: the source stays after a restart, and its password doesn't.
    directory = tmp_path / "workspace"
    before = start_drivnx("--no-browser", workspace=directory)
    saved = sign_in(before.wait_for_launch()).post("/api/sources/postgres", json=QUOTES).json()
    before.process.terminate()
    before.process.wait(timeout=30)

    after = sign_in(start_drivnx("--no-browser", workspace=directory).wait_for_launch())
    unknown = {"password_needed": True, "server_version": None, "encrypted": None}
    assert after.get("/api/sources").json() == [{**saved, **unknown}]
    wrong = after.post(f"/api/sources/{saved['id']}/password", json={"password": "not it"})
    assert wrong.status_code == 422
    assert wrong.json() == {"detail": "The server refused this user name and password."}
    assert after.get("/api/sources").json() == [{**saved, **unknown}]
    right = after.post(
        f"/api/sources/{saved['id']}/password", json={"password": postgres_fixture.QUOTES_PASSWORD}
    )

    assert right.status_code == 200, right.text
    assert right.json() == saved
    assert after.get("/api/sources").json() == [saved]


SETTINGS = {"delimiter": ",", "encoding": "UTF-8", "has_header": True, "skip_lines": None}


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/sources/{id}/rows", None),
        ("PUT", "/api/sources/{id}/settings", SETTINGS),
        ("POST", "/api/profile-runs", {"source_id": "{id}", "mode": "fast"}),
    ],
)
def test_what_only_a_file_can_do_is_refused_for_a_database_with_a_reason(
    client: TestClient, postgres_server: str, method: str, path: str, body: dict | None
) -> None:
    # CFX-APP-postgres/AC-02: a saved database is a source, and previewing or profiling it comes
    # with choosing its tables (DRIVN-49 to 51), so these say so rather than fail.
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    filled = json.loads(json.dumps(body).replace("{id}", source_id)) if body else None

    response = client.request(method, path.format(id=source_id), json=filled)

    assert response.status_code == 409
    assert isinstance(response.json()["detail"], str)


def test_a_password_is_only_taken_for_a_database_that_exists(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-05
    csv = tmp_path / "people.csv"
    csv.write_text("id\n1\n", encoding="utf-8")
    file_id = client.post("/api/sources", json={"path": str(csv)}).json()["id"]
    password = {"password": "anything"}  # pragma: allowlist secret, ai-skills: allow

    assert client.post(f"/api/sources/{file_id}/password", json=password).status_code == 409
    assert client.post(f"/api/sources/{'0' * 32}/password", json=password).status_code == 404


def forms_of(password: str) -> list[bytes]:
    """The password as it could be written down: as typed, and as JSON escapes it."""
    return [
        password.encode("utf-8"),
        json.dumps(password).strip('"').encode("ascii"),
        json.dumps(password, ensure_ascii=False).strip('"').encode("utf-8"),
    ]


def test_no_password_reaches_the_workspace_or_the_most_detailed_logs(
    start_drivnx: StartDrivnX, tmp_path: Path, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05, and AC-30 starts here: the right password and a wrong one, through a
    # save, a restart and the password asked again.
    wrong = "Wr0ng-Pl4nted-pass"  # pragma: allowlist secret, ai-skills: allow
    directory = tmp_path / "workspace"
    before = start_drivnx("--no-browser", "--log-level", "trace", workspace=directory)
    first = sign_in(before.wait_for_launch())
    refused = first.post("/api/sources/postgres", json={**QUOTES, "password": wrong})
    assert refused.status_code == 422
    saved = first.post("/api/sources/postgres", json=QUOTES).json()
    before.process.terminate()
    before.process.wait(timeout=30)
    after = start_drivnx("--no-browser", "--log-level", "trace", workspace=directory)
    second = sign_in(after.wait_for_launch())
    second.post(f"/api/sources/{saved['id']}/password", json={"password": wrong})
    second.post(
        f"/api/sources/{saved['id']}/password", json={"password": postgres_fixture.QUOTES_PASSWORD}
    )
    after.process.terminate()
    after.process.wait(timeout=30)

    output = (before.all_output() + after.all_output()).encode("utf-8")
    # uvicorn's trace lines, so the logs were at their most detailed.
    assert "ASGI [" in output.decode()
    written = [path for path in directory.rglob("*") if path.is_file()]
    assert any(path.name == "workspace.db" for path in written)
    for password in (postgres_fixture.QUOTES_PASSWORD, wrong):
        for form in forms_of(password):
            assert form not in output
            for path in written:
                assert form not in path.read_bytes(), path.name
