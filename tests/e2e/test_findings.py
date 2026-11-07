"""Findings in a real browser, against the installed wheel, CFX-APP-csv-profile AC-21 to AC-23."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import StartConflux
from ..integration.test_full_profile import meeting_every_kind
from .test_connect_csv import connect, details_of, open_set_up_app


@pytest.fixture
def app(start_conflux: StartConflux, page: Page) -> Page:
    open_set_up_app(page, start_conflux("--no-browser").wait_for_launch().url)
    return page


def test_a_full_profiles_findings_show_with_their_evidence(app: Page, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-21, CFX-APP-csv-profile/AC-22 and CFX-APP-csv-profile/AC-23
    path = meeting_every_kind(tmp_path / "every_kind.csv")
    connect(app, path)
    expect(details_of(app, path.name)).to_be_visible()
    profile = app.get_by_role("tabpanel", name="Profile")
    findings = app.get_by_role("complementary", name="Intelligence & Review").get_by_role(
        "region", name="Findings"
    )

    profile.get_by_role("button", name="Full profile").click()

    shown = findings.get_by_role("article")
    expect(shown).to_have_count(5)
    expect(shown.nth(0)).to_contain_text("Observed: amount has 1 value that doesn't fit its type.")
    expect(shown.nth(1)).to_contain_text("Observed: 2 rows are exact copies of another row.")
    expect(shown.nth(2)).to_contain_text("Observed: note is missing 7,500 values (25%).")
    expect(shown.nth(3)).to_contain_text("Observed: id has 30,000 distinct values")
    expect(shown.nth(4)).to_contain_text("Observed: every value in country is the same.")
    expect(shown.nth(0)).to_contain_text("Inferred")
    expect(findings).not_to_contain_text("checked only by a full profile")

    shown.nth(0).get_by_role("button", name="Show the evidence").click()

    amount = profile.get_by_role("row").filter(has_text="amount")
    expect(amount.locator(":focus")).to_have_text("1")

    profile.get_by_role("button", name="Fast profile").click()
    expect(findings).to_contain_text("Duplicate rows are checked only by a full profile.")
