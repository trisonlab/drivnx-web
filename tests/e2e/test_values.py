"""Values on request in a real browser, against the installed wheel, CFX-APP-csv-profile AC-29 and
AC-30."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import StartConflux
from .test_accessibility import violations
from .test_connect_csv import connect, details_of, open_set_up_app


@pytest.fixture
def app(start_conflux: StartConflux, page: Page) -> Page:
    open_set_up_app(page, start_conflux("--no-browser").wait_for_launch().url)
    return page


def test_a_flagged_columns_values_show_after_the_extra_confirmation(
    app: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-29 and CFX-APP-csv-profile/AC-30, with an automated accessibility
    # check in each state.
    path = tmp_path / "people.csv"
    path.write_text(
        "email,amount\n"
        + "".join(f"user{row}@example.com,{row % 10}\n" for row in range(50))
    )
    connect(app, path)
    expect(details_of(app, path.name)).to_be_visible()
    profile = app.get_by_role("tabpanel", name="Profile")
    profile.get_by_role("button", name="Fast profile").click()
    expect(profile.get_by_role("row").filter(has_text="amount")).to_be_visible()

    profile.get_by_role("button", name="Show values of email").click()

    asking = profile.get_by_role(
        "alertdialog", name="This column may hold personal data. Show its values?"
    )
    expect(asking).to_be_visible()
    expect(profile.get_by_role("region", name="Values in email")).to_have_count(0)
    assert violations(app) == []

    asking.get_by_role("button", name="Show values").click()

    shown = profile.get_by_role("region", name="Values in email")
    expect(shown).to_contain_text("from the whole file, and not saved.")
    expect(shown.get_by_role("table", name="Most frequent values")).to_contain_text(
        "user0@example.com"
    )
    assert violations(app) == []

    profile.get_by_role("button", name="Show values of amount").click()

    numbers = profile.get_by_role("region", name="Values in amount")
    expect(numbers.get_by_role("table", name="Histogram")).to_be_visible()
    expect(numbers).to_contain_text("Median")
    expect(numbers.get_by_role("heading", name="Values in amount")).to_be_focused()
    assert violations(app) == []
