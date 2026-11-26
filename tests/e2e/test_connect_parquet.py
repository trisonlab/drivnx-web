"""Connecting a Parquet file and paging through it in a real browser on the installed wheel,
DRIVN-60 AC-07, AC-08, AC-09 (DRIVN-61) and AC-10 (DRIVN-62)."""

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
    path = tmp_path / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute("SET threads = 1")
        connection.execute(
            f"""
            COPY (SELECT i::BIGINT AS id, [i, i + 1]::INTEGER[] AS pair FROM range(10240) t(i))
            TO '{path}' (FORMAT parquet, ROW_GROUP_SIZE 2048)
            """
        )
    return path


def test_connecting_a_parquet_file_shows_its_metadata(app: Page, events: Path) -> None:
    # DRIVN-60/AC-07 and DRIVN-60/AC-09
    connect(app, events)

    details = details_of(app, "events.parquet")
    expect(details.get_by_text("Metadata")).to_be_visible()
    expect(details.get_by_text("10,240 rows", exact=True)).to_be_visible()
    expect(details.get_by_text("5 row groups", exact=True)).to_be_visible()
    expect(details.get_by_text(re.compile(r"^Written by DuckDB version v"))).to_be_visible()
    expect(details.get_by_role("row", name=re.compile(r"id integer BIGINT"))).to_be_visible()
    expect(
        details.get_by_role("row", name=re.compile(r"pair string \(list\) INTEGER\[\]"))
    ).to_be_visible()
    assert violations(app) == []


def test_a_remote_path_is_refused_with_its_message(app: Page) -> None:
    # DRIVN-60/AC-08
    connect(app, "s3://bucket/events.parquet")

    expect(
        app.get_by_text(
            "DrivnX reads files on this computer only. Enter the full path to a file."
        )
    ).to_be_visible()
    explorer = app.get_by_role("navigation", name="Data Explorer")
    expect(explorer.get_by_text("No sources yet")).to_be_visible()
    assert violations(app) == []


def test_paging_through_a_parquet_file(app: Page, events: Path) -> None:
    # DRIVN-60/AC-10
    connect(app, events)

    preview = app.get_by_role("region", name="Preview")
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    expect(preview.get_by_role("row")).to_have_count(51)
    expect(preview.get_by_role("cell", name="[0, 1]", exact=True)).to_be_visible()
    assert violations(app) == []

    preview.get_by_role("button", name="Next").click()
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()
    expect(preview.get_by_role("cell", name="[50, 51]", exact=True)).to_be_visible()
    expect(preview.get_by_role("cell", name="[0, 1]", exact=True)).to_have_count(0)
