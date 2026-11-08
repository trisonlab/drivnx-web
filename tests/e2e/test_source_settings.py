"""Correcting how a file is read in a real browser, CFX-APP-csv-profile AC-01, AC-02, AC-03, AC-05."""

import pytest
from playwright.sync_api import Page, expect

from ..conftest import REPO_ROOT, StartDrivnX
from .test_connect_csv import connect, details_of, open_set_up_app

CSV_DIR = REPO_ROOT / "testdata" / "csv"


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


def test_correcting_a_file_changes_its_columns_and_preview(app: Page) -> None:
    # CFX-APP-csv-profile/AC-01 and CFX-APP-csv-profile/AC-02
    connect(app, CSV_DIR / "title_line_tab_commas_in_text.csv")
    details = details_of(app, "title_line_tab_commas_in_text.csv")
    expect(details).to_be_visible()

    details.get_by_text("How this file is read").click()
    expect(details.get_by_role("combobox", name="Delimiter")).to_have_value(",")
    details.get_by_role("combobox", name="Delimiter").select_option(label="Tab")
    details.get_by_role("spinbutton", name="Lines to skip above the table").fill("1")
    details.get_by_role("button", name="Save settings").click()

    expect(details.get_by_text("Settings saved")).to_be_visible()
    expect(details.get_by_role("cell", name="address", exact=True)).to_be_visible()
    preview = app.get_by_role("region", name="Preview")
    expect(
        preview.get_by_role("cell", name="1 Main St, Paris, France", exact=True).first
    ).to_be_visible()


def test_settings_that_cannot_read_the_file_keep_the_old_ones(app: Page) -> None:
    # CFX-APP-csv-profile/AC-03
    connect(app, CSV_DIR / "comma_latin1.csv")
    details = details_of(app, "comma_latin1.csv")
    expect(details).to_be_visible()

    details.get_by_text("How this file is read").click()
    details.get_by_role("combobox", name="Encoding").select_option("UTF-8")
    details.get_by_role("button", name="Save settings").click()

    expect(details.get_by_role("alert")).to_contain_text(
        "previous settings are still in use"
    )
    expect(details.get_by_text("Latin-1", exact=True).first).to_be_visible()
