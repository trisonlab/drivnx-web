"""A fast and a full profile of a database's chosen objects in the browser, a full one cancelled,
and earlier runs, CFX-APP-postgres AC-17 to AC-21, AC-24, AC-31 and AC-32 (DRIVN-51, DRIVN-53,
DRIVN-57), against the installed wheel and the PostgreSQL fixture."""

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
    expect(profile.get_by_role("button", name="Full profile")).to_be_enabled()
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


def chosen_schema(page: Page, schema: str):
    """The app with every object of `schema` chosen, and its Profile tab."""
    listed = save_connection(page)
    listed.get_by_role("checkbox", name=f"Choose all of {schema}").click()
    listed.get_by_role("button", name="Save selection").click()
    expect(listed.get_by_role("status")).to_have_text("Selection saved.")
    return page.get_by_role("tabpanel", name="Profile")


def test_a_full_profile_reads_every_row_within_a_time_limit_the_user_sets(
    start_drivnx: StartDrivnX, page: Page, objects: None
) -> None:
    # CFX-APP-postgres/AC-19 and AC-20: every row of each chosen object, with each object's exact
    # row count, under a per-object time limit of 1 to 120 minutes; the screens, the limit's error
    # among them, pass the accessibility check.
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    profile = chosen_schema(page, SCHEMA)
    minutes = profile.get_by_role("spinbutton", name="Time limit per object, in minutes")
    expect(minutes).to_have_value("10")

    minutes.fill("0")
    profile.get_by_role("button", name="Full profile").click()
    expect(minutes).to_have_accessible_description("Enter a whole number of minutes from 1 to 120.")
    assert violations(page) == []

    minutes.fill("5")
    profile.get_by_role("button", name="Full profile").click()

    whole = profile.get_by_role("table", name=f"{SCHEMA}.customers: every row, 3 rows")
    expect(whole.get_by_role("row", name=re.compile("^name"))).to_contain_text("1 (33.33%)")
    view = profile.get_by_role("table", name=f"{SCHEMA}.customer_names: every row, 3 rows")
    # Its masked patterns are its fast reading's, the view's first rows (the spec's Assumptions).
    expect(view.get_by_role("row", name=re.compile("^name"))).to_contain_text(
        "from the first 3 rows the view returned, not random"
    )
    expect(page.get_by_text("Profile status: Completed")).to_be_visible()
    assert violations(page) == []



def test_an_earlier_run_opens_and_says_which_objects_are_out_of_date_since(
    start_drivnx: StartDrivnX,
    page: Page,
    objects: None,
    postgres_admin: Callable[[str], None],
) -> None:
    # CFX-APP-postgres/AC-31 and AC-32: the runs list names each run's objects, any completed one
    # opens, and once a refresh finds an object's columns changed, its results say they're out of
    # date; the screens pass the accessibility check.
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    listed = save_connection(page)
    listed.get_by_role("checkbox", name=f"Choose all of {SCHEMA}").click()
    listed.get_by_role("button", name="Save selection").click()
    expect(listed.get_by_role("status")).to_have_text("Selection saved.")
    profile = page.get_by_role("tabpanel", name="Profile")
    profile.get_by_role("button", name="Fast profile").click()
    expect(profile.get_by_text(re.compile("^These results describe each object as it was"))).to_be_visible()

    postgres_admin(f"ALTER TABLE {SCHEMA}.customers ADD COLUMN email text")
    listed.get_by_role("button", name="Refresh").click()

    changed = f"{SCHEMA}.customers has changed or is gone since this run"
    expect(profile.get_by_text(re.compile(f"^{re.escape(changed)}"))).to_be_visible()
    expect(
        profile.get_by_role("table", name=f"{SCHEMA}.customers: the whole object, 3 rows (out of date)")
    ).to_be_visible()
    assert violations(page) == []

    profile.get_by_role("button", name="Fast profile").click()
    runs = profile.get_by_role("list", name="Runs of this database").get_by_role("listitem")
    expect(runs).to_have_count(2)
    expect(profile.get_by_text(re.compile(f"^{re.escape(changed)}"))).to_have_count(0)
    expect(runs.nth(1)).to_contain_text(f"of {SCHEMA}.customer_names, {SCHEMA}.customers")
    expect(runs.nth(1)).to_contain_text("Out of date")

    runs.nth(1).get_by_role("button", name=re.compile("^Open the fast profile")).click()

    expect(profile.get_by_text(re.compile(f"^{re.escape(changed)}"))).to_be_visible()
    expect(runs.nth(1)).to_contain_text("Shown")
    assert violations(page) == []

SLOW_SCHEMA = "drivnx_e2e_stopping"
SLOW_DROP = f"DROP SCHEMA IF EXISTS {SLOW_SCHEMA} CASCADE"
SLOW_OBJECTS = (
    f"CREATE SCHEMA {SLOW_SCHEMA}",
    # Read longer than a cancel may take: DuckDB's interrupt can't stop it (ADR 0009).
    f"CREATE VIEW {SLOW_SCHEMA}.sleepy AS SELECT 1 AS n FROM pg_sleep(20)",
    f"GRANT USAGE ON SCHEMA {SLOW_SCHEMA} TO {postgres_fixture.USER}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SLOW_SCHEMA} TO {postgres_fixture.USER}",
)


@pytest.fixture
def slow_objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (SLOW_DROP, *SLOW_OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        postgres_admin(SLOW_DROP)


def test_a_full_profile_cancelled_while_it_reads_ends_cancelled_within_5_seconds(
    start_drivnx: StartDrivnX, page: Page, slow_objects: None
) -> None:
    # CFX-APP-postgres/AC-21: "Given a run in progress, when the user cancels it, then it stops
    # within the target under Non-functional (5 s) ... its status is `cancelled`"; the running
    # and the cancelled screens pass the accessibility check.
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    profile = chosen_schema(page, SLOW_SCHEMA)
    profile.get_by_role("button", name="Full profile").click()
    running = f"Profile status: Running on {postgres_fixture.DATABASE}: {SLOW_SCHEMA}.sleepy"
    expect(page.get_by_text(running)).to_be_visible()
    assert violations(page) == []

    profile.get_by_role("button", name="Cancel profile").click()

    expect(profile.get_by_text("This profile was cancelled.")).to_be_visible(timeout=5_000)
    assert violations(page) == []
