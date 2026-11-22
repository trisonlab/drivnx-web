"""Adding a PostgreSQL connection in a real browser, against the installed wheel and the fixture.

DRIVN-45 AC-01, AC-02, AC-03, AC-05 and AC-30, with an automated accessibility check on
each new state.
"""

import json
import re
import secrets
from pathlib import Path

import pytest
from artifacts.sources import StoredPostgresSource, list_sources
from local_api.credentials import entry
from playwright.sync_api import Page, expect
from sqlalchemy import create_engine

from .. import postgres_fixture
from ..conftest import StartDrivnX
from ..integration.test_credential_store import (  # noqa: F401 (the fixture)
    NO_STORE,
    RealStore,
    this_computers_store,
)
from ..integration.test_postgres_password_store import stored
from ..integration.test_postgres_sources_api import parts_of
from .test_accessibility import violations
from .test_connect_csv import details_of, open_set_up_app

CONNECTED = re.compile(r"Connected to PostgreSQL \d+")


def database_form(page: Page):
    return page.get_by_role("form", name="PostgreSQL database")


def fill_in(
    page: Page, password: str = postgres_fixture.PASSWORD, user: str = postgres_fixture.USER
) -> None:
    page.get_by_role("button", name="Add a PostgreSQL database").click()
    form = database_form(page)
    form.get_by_role("textbox", name="Host").fill(postgres_fixture.HOST)
    form.get_by_role("spinbutton", name="Port").fill(str(postgres_fixture.PORT))
    form.get_by_role("textbox", name="Database").fill(postgres_fixture.DATABASE)
    form.get_by_role("textbox", name="User").fill(user)
    form.get_by_label("Password").fill(password)


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page, postgres_server: str) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    return page


def test_testing_a_connection_shows_the_server_and_that_it_is_encrypted(app: Page) -> None:
    # DRIVN-45/AC-01 and DRIVN-45/AC-02
    fill_in(app)
    form = database_form(app)
    expect(form.get_by_role("spinbutton", name="Port")).to_have_value(str(postgres_fixture.PORT))
    expect(form.get_by_role("combobox", name="TLS mode")).to_have_value("prefer")

    form.get_by_role("button", name="Test connection").click()

    status = form.get_by_role("status")
    expect(status).to_have_text(CONNECTED)
    expect(status).to_contain_text("The connection is encrypted.")
    assert violations(app) == []


def test_a_wrong_password_says_so_and_saves_nothing(app: Page) -> None:
    # DRIVN-45/AC-03
    fill_in(app, password="not-the-password")  # pragma: allowlist secret, ai-skills: allow

    database_form(app).get_by_role("button", name="Save").click()

    expect(database_form(app).get_by_role("alert")).to_have_text(
        "The server refused this user name and password."
    )
    explorer = app.get_by_role("navigation", name="Data Explorer")
    expect(explorer.get_by_text("No sources yet")).to_be_visible()
    assert violations(app) == []


def test_a_saved_connection_asks_for_its_password_after_a_restart(
    page: Page, start_drivnx: StartDrivnX, tmp_path: Path, postgres_server: str
) -> None:
    # DRIVN-45/AC-02 and DRIVN-45/AC-05
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", env=NO_STORE, workspace=workspace)
    open_set_up_app(page, first.wait_for_launch().url)
    fill_in(page)
    database_form(page).get_by_role("button", name="Save").click()

    details = details_of(page, postgres_fixture.DATABASE)
    expect(details).to_contain_text(CONNECTED)
    expect(details).to_contain_text("DrivnX keeps this password only until it stops")
    explorer = page.get_by_role("navigation", name="Data Explorer")
    expect(explorer.get_by_role("button", name=postgres_fixture.DATABASE)).to_be_visible()
    assert violations(page) == []

    first.process.terminate()
    first.process.wait(timeout=10)
    page.goto(start_drivnx("--no-browser", env=NO_STORE, workspace=workspace).wait_for_launch().url)

    details = details_of(page, postgres_fixture.DATABASE)
    prompt = details.get_by_role("form", name=f"Password for {postgres_fixture.USER}")
    expect(prompt).to_be_visible()
    expect(details).not_to_contain_text(CONNECTED)
    assert violations(page) == []

    prompt.get_by_label("Password").fill(postgres_fixture.PASSWORD)
    prompt.get_by_role("button", name="Connect").click()

    expect(details).to_contain_text(CONNECTED)
    expect(prompt).to_have_count(0)


def test_verify_full_connects_with_the_servers_ca(app: Page) -> None:
    # DRIVN-45/AC-01 and DRIVN-45/AC-02
    fill_in(app)
    form = database_form(app)
    form.get_by_role("combobox", name="TLS mode").select_option("verify-full")
    form.get_by_role("textbox", name="CA certificate file").fill(
        str(postgres_fixture.CA_CERTIFICATE)
    )
    assert violations(app) == []

    form.get_by_role("button", name="Save").click()

    details = details_of(app, postgres_fixture.DATABASE)
    expect(details).to_contain_text("The connection is encrypted.")
    expect(details.get_by_text(str(postgres_fixture.CA_CERTIFICATE))).to_be_visible()


REFUSED = "The server refused this user name and password."


def forget_saved(store: RealStore, workspace: Path) -> None:
    """Has the store's fixture delete the entry of each database the workspace holds, also when
    the test stopped before it knew the source. Read-only, as DrivnX may still hold the
    workspace's lock."""
    database = workspace / "workspace.db"
    if not database.is_file():
        return
    engine = create_engine(f"sqlite:///file:{database.as_posix()}?mode=ro&uri=true")
    try:
        for source in list_sources(engine):
            if isinstance(source, StoredPostgresSource) and entry(source) not in store.written:
                store.written.append(entry(source))
    finally:
        engine.dispose()


def test_no_part_of_a_password_reaches_the_workspace_logs_browser_storage_or_a_url(
    page: Page,
    start_drivnx: StartDrivnX,
    tmp_path: Path,
    this_computers_store: RealStore | None,
    postgres_server: str,
    request: pytest.FixtureRequest,
) -> None:
    # DRIVN-45/AC-30 in the browser: a refused password, the right one saved, a restart,
    # a stored password the server now refuses (AC-06), a wrong one and the right one again.
    # With this computer's store where it has one, else with the password in memory (AC-05).
    store = this_computers_store
    planted = secrets.token_urlsafe(18)  # 24 random characters, refused by the server
    refused_later = secrets.token_urlsafe(18)  # stored, as if the password changed since
    right = postgres_fixture.PASSWORD
    run = ("--no-browser", "--log-level", "trace")
    env = store.env if store else NO_STORE
    workspace = tmp_path / "workspace"
    if store:
        # Before anything is saved, so an entry written by a run that fails is deleted too.
        request.addfinalizer(lambda: forget_saved(store, workspace))
    urls: list[str] = []
    page.context.on("request", lambda request: urls.append(request.url))

    before = start_drivnx(*run, env=env, workspace=workspace)
    open_set_up_app(page, before.wait_for_launch().url)
    fill_in(page, password=planted)
    database_form(page).get_by_role("button", name="Save").click()
    expect(database_form(page).get_by_role("alert")).to_have_text(REFUSED)
    database_form(page).get_by_label("Password").fill(right)
    database_form(page).get_by_role("button", name="Save").click()
    expect(details_of(page, postgres_fixture.DATABASE)).to_contain_text(CONNECTED)
    (saved,) = page.evaluate("fetch('/api/sources').then((response) => response.json())")
    source = stored(saved)
    before.process.terminate()
    before.process.wait(timeout=30)
    if store:
        store.set(*entry(source), refused_later)

    after = start_drivnx(*run, env=env, workspace=workspace)
    page.goto(after.wait_for_launch().url)
    details = details_of(page, postgres_fixture.DATABASE)
    prompt = details.get_by_role("form", name=f"Password for {postgres_fixture.USER}")
    expect(prompt).to_contain_text(REFUSED if store else "DrivnX has restarted.")
    assert violations(page) == []
    prompt.get_by_label("Password").fill(planted)
    prompt.get_by_role("button", name="Connect").click()
    expect(prompt.get_by_role("alert")).to_have_text(REFUSED)
    prompt.get_by_label("Password").fill(right)
    prompt.get_by_role("button", name="Connect").click()
    expect(details).to_contain_text(CONNECTED)
    if store:
        # AC-06: "saving a new one replaces the stored one".
        assert store.store.get_password(*entry(source)) == right
    browser_storage = (
        json.dumps(page.context.storage_state(indexed_db=True))
        + page.evaluate("JSON.stringify(sessionStorage)")
    ).encode("utf-8")
    after.process.terminate()
    after.process.wait(timeout=30)

    output = (before.all_output() + after.all_output()).encode("utf-8")
    # uvicorn's trace lines, so the logs were at their most detailed.
    assert b"ASGI [" in output
    assert any("/connect" in url for url in urls) is bool(store)
    contents = {path.name: path.read_bytes() for path in workspace.rglob("*") if path.is_file()}
    assert "workspace.db" in contents
    for secret in (right, planted, refused_later):
        for part in parts_of(secret):
            assert part not in output
            assert part not in browser_storage
            for url in urls:
                assert part not in url.encode("utf-8"), url
            for name, content in contents.items():
                assert part not in content, name
