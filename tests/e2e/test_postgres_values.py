"""A database column's values on request in the browser, DRIVN-45 AC-27 and AC-28 (DRIVN-56),
against the installed wheel and the PostgreSQL fixture."""

import re
from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from ..integration.test_credential_store import NO_STORE
from .test_accessibility import violations
from .test_connect_csv import open_set_up_app
from .test_profile_postgres import chosen_schema

SCHEMA = "drivnx_e2e_values"
DROP = f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"
# 10 rows, read whole. `email` is possibly sensitive by its name and its values (DRIVN-55).
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.contacts (id integer PRIMARY KEY, email text, city text)",
    f"INSERT INTO {SCHEMA}.contacts SELECT n, 'a' || n % 2 || '@example.com',"
    " CASE WHEN n <= 7 THEN 'Oslo' ELSE 'Bergen' END FROM generate_series(1, 10) AS n",
    f"ANALYZE {SCHEMA}.contacts",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO {postgres_fixture.USER}",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {postgres_fixture.USER}",
)
CONTACTS = f"{SCHEMA}.contacts"


@pytest.fixture
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        postgres_admin(DROP)


def test_a_database_columns_values_show_on_request_and_a_flagged_ones_after_the_confirmation(
    start_drivnx: StartDrivnX, page: Page, objects: None
) -> None:
    # DRIVN-45/AC-28: "computed by querying the database with that run's method and seed,
    # labelled with that scope, the time computed, and that they're the same rows only if the
    # object hasn't changed since the run"; AC-27: a possibly-sensitive column's values "need the
    # extra confirmation". Each state passes the accessibility check.
    console_errors: list[str] = []
    page.on(
        "console",
        lambda message: message.type == "error" and console_errors.append(message.text),
    )
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    profile = chosen_schema(page, SCHEMA)
    profile.get_by_role("button", name="Fast profile").click()
    contacts = profile.get_by_role("table", name=f"{CONTACTS}: the whole object, 10 rows")
    expect(contacts).to_be_visible()

    profile.get_by_role("button", name=f"Show values of city in {CONTACTS}").click()

    city = profile.get_by_role("region", name=f"Values in city of {CONTACTS}")
    expect(city).to_contain_text(
        re.compile(
            r"Computed .+ from the whole object, 10 rows, and not saved\."
            f" These are the same rows as this run's only if {re.escape(CONTACTS)} hasn't changed"
            " since this run\\."
        )
    )
    frequent = city.get_by_role("table", name="Most frequent values")
    expect(frequent.get_by_role("row")).to_have_text(["ValueCount", "Oslo7", "Bergen3"])
    expect(city.get_by_role("heading", name=f"Values in city of {CONTACTS}")).to_be_focused()
    assert violations(page) == []

    email_button = profile.get_by_role("button", name=f"Show values of email in {CONTACTS}")
    email_button.click()

    asking = profile.get_by_role(
        "alertdialog", name="This column may hold personal data. Show its values?"
    )
    expect(asking).to_be_visible()
    expect(profile.get_by_role("region", name=re.compile("^Values in "))).to_have_count(0)
    assert violations(page) == []

    asking.get_by_role("button", name="Show values").click()

    email = profile.get_by_role("region", name=f"Values in email of {CONTACTS}")
    frequent = email.get_by_role("table", name="Most frequent values")
    expect(frequent.get_by_role("row")).to_have_text(
        ["ValueCount", "a0@example.com5", "a1@example.com5"]
    )
    assert violations(page) == []

    email.get_by_role("button", name="Close values").click()

    expect(email_button).to_be_focused()
    assert console_errors == []
