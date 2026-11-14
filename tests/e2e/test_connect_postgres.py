"""Adding a PostgreSQL connection in a real browser, against the installed wheel and the fixture.

CFX-APP-postgres AC-01, AC-02, AC-03 and AC-05, with an automated accessibility check on each
new state.
"""

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from .test_accessibility import violations
from .test_connect_csv import details_of, open_set_up_app

CONNECTED = re.compile(r"Connected to PostgreSQL \d+")


def database_form(page: Page):
    return page.get_by_role("form", name="PostgreSQL database")


def fill_in(page: Page, password: str = postgres_fixture.PASSWORD) -> None:
    page.get_by_role("button", name="Add a PostgreSQL database").click()
    form = database_form(page)
    form.get_by_role("textbox", name="Host").fill(postgres_fixture.HOST)
    form.get_by_role("spinbutton", name="Port").fill(str(postgres_fixture.PORT))
    form.get_by_role("textbox", name="Database").fill(postgres_fixture.DATABASE)
    form.get_by_role("textbox", name="User").fill(postgres_fixture.USER)
    form.get_by_label("Password").fill(password)


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page, postgres_server: str) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


def test_testing_a_connection_shows_the_server_and_that_it_is_encrypted(app: Page) -> None:
    # CFX-APP-postgres/AC-01 and CFX-APP-postgres/AC-02
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
    # CFX-APP-postgres/AC-03
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
    # CFX-APP-postgres/AC-02 and CFX-APP-postgres/AC-05
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
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
    page.goto(start_drivnx("--no-browser", workspace=workspace).wait_for_launch().url)

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
    # CFX-APP-postgres/AC-01 and CFX-APP-postgres/AC-02
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
