"""A fast profile in a real browser, against the installed wheel, CFX-APP-csv-profile AC-07, AC-15, AC-16, AC-17."""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import REPO_ROOT, StartConflux
from .test_connect_csv import connect, details_of, open_set_up_app

CSV_DIR = REPO_ROOT / "testdata" / "csv"


@pytest.fixture
def app(start_conflux: StartConflux, page: Page) -> Page:
    open_set_up_app(page, start_conflux("--no-browser").wait_for_launch().url)
    return page


def run_a_fast_profile(page: Page, path: Path):
    connect(page, path)
    expect(details_of(page, path.name)).to_be_visible()
    panel = page.get_by_role("tabpanel", name="Profile")
    panel.get_by_role("button", name="Fast profile").click()
    expect(panel.get_by_role("table")).to_be_visible()
    return panel


def test_a_file_under_100000_rows_is_labelled_as_the_whole_file(app: Page) -> None:
    # CFX-APP-csv-profile/AC-07, CFX-APP-csv-profile/AC-15 and CFX-APP-csv-profile/AC-17
    panel = run_a_fast_profile(app, CSV_DIR / "comma_utf8.csv")

    rows = panel.get_by_role("row")
    expect(rows.filter(has_text="Rows read")).to_contain_text("whole file")
    expect(rows.filter(has_text="Rows read")).to_contain_text("Exact")
    expect(panel.get_by_role("table")).to_contain_text("Missing values")
    expect(panel.get_by_role("table")).not_to_contain_text("first 100,000")
    expect(panel.get_by_role("table")).to_contain_text("Run ")
    expect(app.get_by_text("Profile status: Completed")).to_be_visible()


def test_a_file_over_100000_rows_is_labelled_as_its_first_rows(
    app: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-07 and CFX-APP-csv-profile/AC-16
    path = tmp_path / "numbers.csv"
    path.write_text(
        "id,name\n" + "".join(f"{number},name{number}\n" for number in range(150_000))
    )

    panel = run_a_fast_profile(app, path)

    rows_read = panel.get_by_role("row").filter(has_text="Rows read")
    expect(rows_read).to_contain_text("100,000")
    expect(rows_read).to_contain_text("first 100,000 rows, not random")
    expect(panel).not_to_contain_text("whole file")
    expect(panel).not_to_contain_text("150,000")
    expect(app.get_by_text("Profile status: Completed")).to_be_visible()


def test_a_correction_stops_showing_results_read_the_earlier_way(app: Page) -> None:
    # Until T11 labels them. CFX-APP-csv-profile/AC-36 finishes this.
    panel = run_a_fast_profile(app, CSV_DIR / "comma_utf8.csv")
    details = details_of(app, "comma_utf8.csv")

    details.get_by_text("How this file is read").click()
    details.get_by_role("combobox", name="Delimiter").select_option(label="Tab")
    details.get_by_role("button", name="Save settings").click()

    expect(panel.get_by_role("table")).to_have_count(0)
    expect(
        panel.get_by_text("No profile has been run with these settings yet.")
    ).to_be_visible()
