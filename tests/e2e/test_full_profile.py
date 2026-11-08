"""A full profile and cancelling, in a real browser against the installed wheel, CFX-APP-csv-profile
AC-08, AC-10 and AC-18."""

import time
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import StartDrivnX
from ..integration.test_full_profile import (
    CANCEL_WITHIN_SECONDS,
    large_csv,
    planted_missing,
)
from .test_connect_csv import connect, details_of, open_set_up_app


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


def test_a_full_profile_counts_every_row_and_labels_its_patterns(
    app: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-08 and CFX-APP-csv-profile/AC-18
    path = planted_missing(tmp_path / "notes.csv")
    connect(app, path)
    expect(details_of(app, path.name)).to_be_visible()
    panel = app.get_by_role("tabpanel", name="Profile")

    panel.get_by_role("button", name="Full profile").click()

    expect(panel.get_by_text("This file has 150,001 rows.")).to_be_visible()
    note = panel.get_by_role("row").filter(has_text="note")
    expect(note).to_contain_text("whole file")
    expect(note).to_contain_text("10 (")
    expect(note.get_by_text("from the first 100,000 rows, not random")).to_be_visible()


def test_a_full_profile_can_be_cancelled(app: Page, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-10
    path = large_csv(tmp_path / "large.csv", rows=20_000_000)
    connect(app, path)
    expect(details_of(app, path.name)).to_be_visible()
    panel = app.get_by_role("tabpanel", name="Profile")
    panel.get_by_role("button", name="Full profile").click()
    cancel = panel.get_by_role("button", name="Cancel profile")
    expect(cancel).to_be_visible()

    cancelled_at = time.monotonic()
    cancel.click()

    expect(panel.get_by_text("This profile was cancelled.")).to_be_visible(
        timeout=CANCEL_WITHIN_SECONDS * 1000
    )
    assert time.monotonic() - cancelled_at < CANCEL_WITHIN_SECONDS
    expect(app.get_by_text("Profile status: Cancelled")).to_be_visible()
    expect(panel.get_by_role("table")).not_to_be_visible()
