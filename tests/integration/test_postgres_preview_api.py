"""Previewing a chosen database object through the API, CFX-APP-postgres AC-12 (DRIVN-50)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.workspace import Workspace
from fastapi.testclient import TestClient

from ..conftest import StartDrivnX
from .test_credential_store import NO_STORE
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_password_store import (  # noqa: F401 (the fixtures)
    REFUSED,
    StartApp,
    saved_with_a_wrong_stored_password,
    start_app,
    workspace,
)
from .test_session import sign_in

SCHEMA = "drivnx_api_preview"
# A value only the planted table holds, to look for after it's previewed.
PLANTED = "drivnx-preview-planted-7f3a9c"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role. Rows go in from 120 down to 1, so the
# server's own order isn't the key's.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.numbered (id integer PRIMARY KEY, label text)",
    f"INSERT INTO {SCHEMA}.numbered SELECT n, 'row ' || n FROM generate_series(120, 1, -1) AS n",
    f"CREATE TABLE {SCHEMA}.cells (id integer PRIMARY KEY, long text, nothing text,"
    " amount numeric(10, 2), tags text[], markup text)",
    f"INSERT INTO {SCHEMA}.cells VALUES (1, repeat('x', 1001), NULL, 12.5, ARRAY['a', 'b c'],"
    " '<img src=x onerror=alert(1)>')",
    f"CREATE TABLE {SCHEMA}.shrinking (id integer PRIMARY KEY, gone text)",
    # Longer than the 30 s every statement DrivnX sends is allowed.
    f"CREATE VIEW {SCHEMA}.slow AS SELECT 1 AS n FROM pg_sleep(31)",
    f"CREATE TABLE {SCHEMA}.bare ()",
    f"INSERT INTO {SCHEMA}.bare DEFAULT VALUES",
    f"INSERT INTO {SCHEMA}.bare DEFAULT VALUES",
    f"CREATE TABLE {SCHEMA}.planted (id integer PRIMARY KEY, token text)",
    f"INSERT INTO {SCHEMA}.planted VALUES (1, '{PLANTED}')",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    """The objects above, in the fixture's database, for every test here."""
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def chosen(client: TestClient, name: str) -> str:
    """The rows path of an object of this test's schema, saved as the only one chosen."""
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    found = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)[name]
    selection = client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [found["id"]]}
    )
    assert selection.status_code == 200, selection.text
    return f"/api/sources/{source_id}/objects/{found['id']}/rows"


def values(page: dict) -> list[list[str | None]]:
    return [[cell["value"] for cell in row] for row in page["rows"]]


def test_a_chosen_objects_preview_opens_on_its_first_50_rows_in_key_order(
    client: TestClient,
) -> None:
    # CFX-APP-postgres/AC-12: 50 rows at a time, with a next page; a table with a primary key is
    # in the key's order (the order agreed for DRIVN-50), not the order the rows went in.
    response = client.get(chosen(client, "numbered"))

    assert response.status_code == 200, response.text
    page = response.json()
    assert [cell["value"] for cell in page["columns"]] == ["id", "label"]
    assert values(page) == [[str(n), f"row {n}"] for n in range(1, 51)]
    assert (page["offset"], page["has_next"]) == (0, True)


def test_next_and_previous_page_through_the_rows_to_the_last(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12: next and previous are pages from an offset; the last page of 120
    # rows has 20 and no next page.
    path = chosen(client, "numbered")

    last = client.get(path, params={"offset": 100}).json()
    previous = client.get(path, params={"offset": 50}).json()

    assert values(last) == [[str(n), f"row {n}"] for n in range(101, 121)]
    assert (last["offset"], last["has_next"]) == (100, False)
    assert values(previous) == [[str(n), f"row {n}"] for n in range(51, 101)]
    assert (previous["offset"], previous["has_next"]) == (50, True)


def test_more_than_200_rows_a_page_is_refused(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12 with CFX-APP-first-slice/AC-28: at most 200 rows a page.
    path = chosen(client, "numbered")

    assert client.get(path, params={"limit": 201}).status_code == 422
    assert len(client.get(path, params={"limit": 200}).json()["rows"]) == 120


def test_cells_are_postgresqls_text_and_a_long_one_is_cut_and_marked(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12 with CFX-APP-first-slice/AC-29: a cell over 1,000 characters is cut
    # and marked; NULL stays null; every type comes as PostgreSQL writes it as text, and markup is
    # only text, which the screen shows as such (Preview.test.tsx).
    (row,) = client.get(chosen(client, "cells")).json()["rows"]

    assert row == [
        {"value": "1", "cut": False},
        {"value": "x" * 1000, "cut": True},
        {"value": None, "cut": False},
        {"value": "12.50", "cut": False},
        {"value": '{a,"b c"}', "cut": False},
        {"value": "<img src=x onerror=alert(1)>", "cut": False},
    ]


def test_an_object_that_isnt_chosen_has_no_preview(client: TestClient) -> None:
    # CFX-APP-postgres/AC-11: only chosen objects can be previewed. `cells` is chosen, so the
    # source's selection is saved, and `numbered` isn't.
    source_id = chosen(client, "cells").split("/")[3]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)

    response = client.get(f"/api/sources/{source_id}/objects/{listed['numbered']['id']}/rows")

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Choose this object to preview its rows."}


def test_an_object_no_longer_listed_has_no_preview(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12: gone in a refresh, or never this source's, as when it's opened.
    source_id = chosen(client, "numbered").split("/")[3]

    response = client.get(f"/api/sources/{source_id}/objects/{'0' * 32}/rows")

    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "There's no such object. Refresh the list."}


def test_after_a_restart_the_preview_waits_for_the_password(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-12 with AC-05: the rows are read from the database, so after a restart
    # without a credential store the preview waits for the password, as the source asks for it.
    workspace = tmp_path / "workspace"
    before = start_drivnx("--no-browser", env=NO_STORE, workspace=workspace)
    path = chosen(sign_in(before.wait_for_launch()), "numbered")
    before.process.terminate()
    before.process.wait(timeout=30)
    after = sign_in(
        start_drivnx("--no-browser", env=NO_STORE, workspace=workspace).wait_for_launch()
    )

    response = after.get(path)

    assert response.status_code == 409, response.text
    assert response.json() == {"detail": "Enter the password for this database first."}


def test_a_password_the_server_refuses_when_previewing_asks_for_it_again(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-12 with AC-06: the preview is the first read after a restart, with a
    # stored password the server now refuses. The server's reason, and the source asks again.
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)
    found = objects_in(after.get(f"/api/sources/{source.id}/objects").json(), SCHEMA)["numbered"]
    after.put(f"/api/sources/{source.id}/selection", json={"object_ids": [found["id"]]})

    response = after.get(f"/api/sources/{source.id}/objects/{found['id']}/rows")

    assert (response.status_code, response.json()) == (422, REFUSED)
    (listed,) = after.get("/api/sources").json()
    assert listed["password_needed"] is True


def test_an_object_changed_since_it_was_listed_points_to_a_refresh(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-12 with AC-13: the preview reads the columns the last discovery found,
    # and one of them is gone. The connector's message, never DuckDB's text.
    path = chosen(client, "shrinking")
    postgres_admin(f"ALTER TABLE {SCHEMA}.shrinking DROP COLUMN gone")

    response = client.get(path)

    assert response.status_code == 409, response.text
    assert response.json() == {
        "detail": "DrivnX couldn't read this object. It may have changed since it was listed:"
        " refresh the list."
    }


def test_a_preview_is_never_stored_by_drivnx_or_the_browser(
    client: TestClient, workspace_dir: Path
) -> None:
    # CFX-APP-postgres/AC-12: the response is `Cache-Control: no-store`, and no file in the
    # workspace holds a previewed value.
    response = client.get(chosen(client, "planted"))

    assert response.status_code == 200, response.text
    assert PLANTED in response.text
    assert response.headers["cache-control"] == "no-store"
    written = [path for path in workspace_dir.rglob("*") if path.is_file()]
    assert any(path.name == "workspace.db" for path in written)
    for path in written:
        assert PLANTED.encode() not in path.read_bytes(), path.name


def test_a_table_without_columns_shows_its_rows_with_no_cells(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12: PostgreSQL allows a table with no columns; its rows are still rows.
    response = client.get(chosen(client, "bare"))

    assert response.status_code == 200, response.text
    page = response.json()
    assert (page["columns"], page["rows"], page["has_next"]) == ([], [[], []], False)


def test_a_page_the_database_takes_too_long_to_read_says_so(client: TestClient) -> None:
    # CFX-APP-postgres/AC-12 with the spec's 30 s statement_timeout for preview: the object hasn't
    # changed, so the message mustn't point to a refresh.
    response = client.get(chosen(client, "slow"))

    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": "The database took longer than 30 seconds to read this page. Try again."
    }
