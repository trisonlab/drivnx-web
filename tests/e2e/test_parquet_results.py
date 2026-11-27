"""A completed Parquet run in a real browser, on the installed wheel, DRIVN-60 AC-14
(DRIVN-64)."""

import re
from pathlib import Path

import duckdb
import pytest
from playwright.sync_api import Page, expect

from ..conftest import StartDrivnX
from .test_accessibility import violations
from .test_connect_csv import connect, details_of, open_set_up_app


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


@pytest.fixture
def events(tmp_path: Path) -> Path:
    # Larger than the sample, so a fast profile reads 8 seeded slices of 12,500 rows. Each slice
    # is a run of rows, so a quarter of `note` is missing in it whichever rows it starts at.
    path = tmp_path / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            f"""
            COPY (
                SELECT
                    i::BIGINT AS id,
                    CASE WHEN i % 4 = 0 THEN NULL ELSE 'n' || (i % 7) END AS note,
                    'FR' AS country
                FROM range(200000) t(i)
            ) TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE 50000)
            """
        )
    return path


def test_a_completed_runs_findings_values_and_earlier_runs(app: Page, events: Path) -> None:
    # DRIVN-60/AC-14, with an automated accessibility check in each state.
    connect(app, events)
    expect(details_of(app, events.name)).to_be_visible()
    profile = app.get_by_role("tabpanel", name="Profile")
    findings = app.get_by_role("complementary", name="Intelligence & Review").get_by_role(
        "region", name="Findings"
    )

    profile.get_by_role("button", name="Fast profile").click()

    # `id` is a number, so its row has no patterns, whose label names the sample on any run.
    id_row = profile.get_by_role("row", name=re.compile(r"^id "))
    expect(id_row).to_contain_text("about 100,000 rows, sampled by row group")
    shown = findings.get_by_role("article")
    expect(shown).to_have_count(3)
    expect(shown.nth(0)).to_contain_text(
        "Observed: note is missing 25,000 values (25%) in about 100,000 rows, sampled by row group."
    )
    expect(shown.nth(1)).to_contain_text("Observed: id has 100,000 distinct values")
    expect(shown.nth(2)).to_contain_text(
        "Observed: every value in country is the same in about 100,000 rows, sampled by row group."
    )
    assert violations(app) == []

    profile.get_by_role("button", name="Show values of id").click()

    values = profile.get_by_role("region", name="Values in id")
    expect(values).to_contain_text(
        "from about 100,000 rows, sampled by row group, and not saved."
    )
    expect(values.get_by_role("table", name="Histogram")).to_be_visible()
    expect(values.get_by_role("heading", name="Values in id")).to_be_focused()
    assert violations(app) == []

    profile.get_by_role("button", name="Full profile").click()

    expect(id_row).to_contain_text("every row, 200,000 rows")
    runs = profile.get_by_role("list", name="Runs of this file").get_by_role("listitem")
    expect(runs).to_have_count(2)
    assert violations(app) == []

    runs.nth(1).get_by_role("button", name=re.compile(r"^Open the fast profile started")).click()

    expect(id_row).to_contain_text("about 100,000 rows, sampled by row group")
    expect(id_row).not_to_contain_text("every row")
    expect(runs.nth(1)).to_contain_text("Shown")
