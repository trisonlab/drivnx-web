"""Listing, opening, choosing and refreshing a database's tables in a real browser, against the
installed wheel and the fixture.

CFX-APP-postgres AC-08, AC-09, AC-10, AC-11 and AC-13, with an automated accessibility check on
each new state.
"""

import re
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from .test_accessibility import violations
from .test_connect_csv import details_of, open_set_up_app
from .test_connect_postgres import database_form, fill_in

SCHEMA = "drivnx_e2e_objects"
DROP = f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"
# Made as the superuser, one statement at a time, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.customers (id integer CONSTRAINT customers_key PRIMARY KEY, name text)",
    f"INSERT INTO {SCHEMA}.customers VALUES (1, 'Ann'), (2, 'Bob'), (3, 'Zoé')",
    f"ANALYZE {SCHEMA}.customers",
    f"CREATE TABLE {SCHEMA}.orders (id bigint PRIMARY KEY,"
    f" customer_id integer NOT NULL CONSTRAINT orders_customer REFERENCES {SCHEMA}.customers)",
    f"CREATE TABLE {SCHEMA}.secret (id integer)",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO {postgres_fixture.USER}",
    f"GRANT SELECT ON {SCHEMA}.customers, {SCHEMA}.orders TO {postgres_fixture.USER}",
)


@pytest.fixture
def objects(postgres_admin: Callable[[str], None]) -> Iterator[Callable[[str], None]]:
    """This test's schema in the fixture's database, and a way to change it."""
    for statement in (DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield postgres_admin
    finally:
        postgres_admin(DROP)


def save_connection(page: Page):
    fill_in(page)
    database_form(page).get_by_role("button", name="Save").click()
    return details_of(page, postgres_fixture.DATABASE).get_by_role(
        "region", name="Schemas and tables"
    )


def test_a_saved_database_lists_its_tables_and_opens_one(
    start_drivnx: StartDrivnX, page: Page, objects: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-08, CFX-APP-postgres/AC-09 and CFX-APP-postgres/AC-10
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)

    listed = save_connection(page)

    schema = listed.get_by_role("group", name=SCHEMA)
    expect(schema.get_by_role("listitem", name="customers")).to_contain_text(
        "customers · table · about 3 rows (estimate)"
    )
    secret = schema.get_by_role("listitem", name="secret")
    expect(secret).to_contain_text("No read access")
    expect(secret.get_by_role("checkbox", name="Choose secret")).to_be_disabled()
    assert violations(page) == []

    schema.get_by_role("button", name="orders").click()

    opened = page.get_by_role("region", name=f"{SCHEMA}.orders")
    expect(opened.get_by_role("row", name="customer_id integer integer No")).to_be_visible()
    expect(opened).to_contain_text(
        f"Foreign key orders_customer: customer_id → {SCHEMA}.customers (id)"
    )
    assert violations(page) == []


def test_a_selection_stays_after_a_restart_and_a_refresh_shows_a_new_column(
    start_drivnx: StartDrivnX, page: Page, tmp_path: Path, objects: Callable[[str], None]
) -> None:
    # CFX-APP-postgres/AC-11 and CFX-APP-postgres/AC-13
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
    open_set_up_app(page, first.wait_for_launch().url)
    listed = save_connection(page)
    listed.get_by_role("checkbox", name=f"Choose all of {SCHEMA}").click()
    listed.get_by_role("button", name="Save selection").click()
    expect(listed.get_by_role("status")).to_have_text("Selection saved.")
    assert violations(page) == []

    first.process.terminate()
    first.process.wait(timeout=10)
    page.goto(start_drivnx("--no-browser", workspace=workspace).wait_for_launch().url)
    details = details_of(page, postgres_fixture.DATABASE)
    prompt = details.get_by_role("form", name=f"Password for {postgres_fixture.USER}")
    prompt.get_by_label("Password").fill(postgres_fixture.PASSWORD)
    prompt.get_by_role("button", name="Connect").click()

    listed = details.get_by_role("region", name="Schemas and tables")
    expect(listed.get_by_role("checkbox", name="Choose customers")).to_be_checked()
    expect(listed.get_by_role("checkbox", name="Choose orders")).to_be_checked()
    expect(listed.get_by_role("checkbox", name="Choose secret")).not_to_be_checked()

    objects(f"ALTER TABLE {SCHEMA}.customers ADD COLUMN email text")
    when = listed.get_by_text(re.compile(r"^Listed "))
    before = when.inner_text()
    listed.get_by_role("button", name="Refresh").click()
    # The refresh closes any opened object, whose id may have changed, so open it afterwards.
    expect(when).not_to_have_text(before)
    listed.get_by_role("button", name="customers").click()

    opened = page.get_by_role("region", name=f"{SCHEMA}.customers")
    expect(opened.get_by_role("row", name="email text string Yes")).to_be_visible()
    expect(listed.get_by_role("checkbox", name="Choose customers")).to_be_checked()
    assert violations(page) == []
