"""A fast profile of a database's chosen objects in the browser, CFX-APP-postgres AC-17, AC-18 and
AC-24 (DRIVN-51), against the installed wheel and the PostgreSQL fixture."""

import re
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from ..integration.test_credential_store import NO_STORE
from .test_accessibility import violations
from .test_choose_postgres_objects import save_connection
from .test_connect_csv import open_set_up_app

SCHEMA = "drivnx_e2e_profile"
DROP = f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"
# Made as the superuser, one statement at a time, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.customers (id integer PRIMARY KEY, name text)",
    f"INSERT INTO {SCHEMA}.customers VALUES (1, 'Ann'), (2, ''), (3, NULL)",
    f"ANALYZE {SCHEMA}.customers",
    f"CREATE VIEW {SCHEMA}.customer_names AS SELECT name FROM {SCHEMA}.customers",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO {postgres_fixture.USER}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {postgres_fixture.USER}",
)


@pytest.fixture
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        postgres_admin(DROP)


def test_a_fast_profile_of_the_chosen_objects_shows_how_each_was_read(
    start_drivnx: StartDrivnX, page: Page, objects: None
) -> None:
    # CFX-APP-postgres/AC-18 and AC-24: one run over the chosen objects, each labelled with how its
    # rows were read, with the run's ID and time; the screens pass the accessibility check.
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    listed = save_connection(page)
    listed.get_by_role("checkbox", name=f"Choose all of {SCHEMA}").click()
    listed.get_by_role("button", name="Save selection").click()
    expect(listed.get_by_role("status")).to_have_text("Selection saved.")
    profile = page.get_by_role("tabpanel", name="Profile")
    expect(profile.get_by_role("button", name=re.compile("Full profile"))).to_be_disabled()
    assert violations(page) == []

    profile.get_by_role("button", name="Fast profile").click()

    whole = profile.get_by_role("table", name=f"{SCHEMA}.customers: the whole object, 3 rows")
    expect(whole.get_by_role("row", name=re.compile("^name"))).to_contain_text("1 (33.33%)")
    expect(
        profile.get_by_role(
            "table", name=f"{SCHEMA}.customer_names: the first 3 rows the view returned, not random"
        )
    ).to_be_visible()
    expect(profile.get_by_text(re.compile(r"^Run [0-9a-f]{32}, finished "))).to_be_visible()
    expect(page.get_by_text("Profile status: Completed")).to_be_visible()
    assert violations(page) == []
