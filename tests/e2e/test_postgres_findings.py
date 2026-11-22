"""A database's findings in the browser, CFX-APP-postgres AC-26, AC-27 and AC-35 (DRIVN-55), against
the installed wheel and the PostgreSQL fixture."""

from collections.abc import Callable, Iterator

import pytest
from playwright.sync_api import Page, expect

from .. import postgres_fixture
from ..conftest import StartDrivnX
from ..integration.test_credential_store import NO_STORE
from .test_accessibility import violations
from .test_connect_csv import open_set_up_app
from .test_profile_postgres import chosen_schema

SCHEMA = "drivnx_e2e_findings"
DROP = f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE"
# 10 rows, read whole. `id` and `code` are both distinct in every row, but only `id` is declared a
# key; `status` has one value.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.orders (id integer PRIMARY KEY, code text, status text)",
    f"INSERT INTO {SCHEMA}.orders SELECT n, 'C-' || n, 'open' FROM generate_series(1, 10) AS n",
    f"ANALYZE {SCHEMA}.orders",
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


def test_a_fast_profiles_findings_say_their_object_and_link_to_its_evidence(
    start_drivnx: StartDrivnX, page: Page, objects: None
) -> None:
    # CFX-APP-postgres/AC-26: the CSV profile's findings "with their evidence links", and none of a
    # possible identifier for the declared key; AC-27: the no-flag note; AC-35: "opening a finding's
    # evidence", with the accessibility check.
    open_set_up_app(page, start_drivnx("--no-browser", env=NO_STORE).wait_for_launch().url)
    profile = chosen_schema(page, SCHEMA)
    findings = page.get_by_role("complementary", name="Intelligence & Review").get_by_role(
        "region", name="Findings"
    )
    expect(findings).to_contain_text("Profile this database to see what it finds.")

    profile.get_by_role("button", name="Fast profile").click()

    shown = findings.get_by_role("article")
    expect(shown).to_have_count(2)
    expect(shown.nth(0)).to_contain_text(
        f"Observed in {SCHEMA}.orders: code has 10 distinct values (100%)."
    )
    expect(shown.nth(1)).to_contain_text(
        f"Observed in {SCHEMA}.orders: every value in status is the same."
    )
    expect(findings).to_contain_text(
        "Having no possibly-sensitive flag doesn't mean a column has no personal data."
    )
    expect(findings).to_contain_text("Duplicate rows are checked only by a full profile.")
    assert violations(page) == []

    shown.nth(0).get_by_role("button", name="Show the evidence").click()

    orders = profile.get_by_role("table", name=f"{SCHEMA}.orders: the whole object, 10 rows")
    code = orders.get_by_role("row").filter(
        has=page.get_by_role("rowheader", name="code", exact=True)
    )
    expect(code.locator(":focus")).to_have_text("10 (100%)")
    assert violations(page) == []
