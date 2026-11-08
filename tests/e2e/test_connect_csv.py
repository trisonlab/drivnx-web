"""Connecting a CSV in a real browser, CFX-APP-first-slice AC-15, AC-20, AC-21, AC-22, AC-23, AC-24, AC-25."""

import re
import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from ..conftest import REPO_ROOT, StartDrivnX, posix_only

CSV_DIR = REPO_ROOT / "testdata" / "csv"


def open_set_up_app(page: Page, url: str) -> None:
    page.goto(url)
    page.get_by_role("textbox", name="Workspace name").fill("Sales")
    page.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_role("status")).to_be_visible()


def connect(page: Page, path: Path | str) -> None:
    page.get_by_role("textbox", name="CSV file path").fill(str(path))
    page.get_by_role("button", name="Connect").click()


def details_of(page: Page, name: str):
    return page.get_by_role("region", name=name)


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


def test_connecting_a_csv_shows_what_the_scan_found(app: Page) -> None:
    # CFX-APP-first-slice/AC-22, CFX-APP-first-slice/AC-24 and CFX-APP-first-slice/AC-25
    connect(app, CSV_DIR / "comma_utf8.csv")

    details = details_of(app, "comma_utf8.csv")
    expect(details).to_be_visible()
    expect(details.get_by_text("Metadata")).to_be_visible()
    expect(details.get_by_text("Comma", exact=True)).to_be_visible()
    expect(details.get_by_text("UTF-8", exact=True)).to_be_visible()
    expect(details.get_by_text("A row from the file was used as column names")).to_be_visible()
    expect(
        details.get_by_role("row", name=re.compile("joined.*date.*DATE"))
    ).to_be_visible()
    explorer = app.get_by_role("navigation", name="Data Explorer")
    expect(explorer.get_by_text("joined", exact=True)).to_be_visible()
    expect(explorer.get_by_text("No sources yet")).to_have_count(0)


@pytest.mark.parametrize(
    ("file", "delimiter", "encoding", "column"),
    [
        ("semicolon_utf8.csv", "Semicolon", "UTF-8", "city"),
        ("tab_utf8.csv", "Tab", "UTF-8", "city"),
        ("pipe_utf8.csv", "Pipe", "UTF-8", "city"),
        ("comma_utf16_le.csv", "Comma", "UTF-16 LE", "city"),
        ("comma_utf16_be.csv", "Comma", "UTF-16 BE", "city"),
        ("emoji_utf16_le.csv", "Comma", "UTF-16 LE", "note"),
        ("comma_latin1.csv", "Comma", "Latin-1", "city"),
        ("prices_windows1252.csv", "Comma", "Windows-1252", "price"),
        ("tab_commas_in_text.csv", "Tab", "UTF-8", "address"),
    ],
)
def test_each_kind_of_file_shows_its_detection(
    app: Page, file: str, delimiter: str, encoding: str, column: str
) -> None:
    # CFX-APP-first-slice/AC-22
    connect(app, CSV_DIR / file)

    details = details_of(app, file)
    expect(details.get_by_text(delimiter, exact=True)).to_be_visible()
    expect(details.get_by_text(encoding, exact=True)).to_be_visible()
    expect(details.get_by_role("cell", name=column, exact=True)).to_be_visible()


def test_a_file_without_a_header_shows_generated_names(app: Page) -> None:
    # CFX-APP-first-slice/AC-22
    connect(app, CSV_DIR / "no_header_typed.csv")

    details = details_of(app, "no_header_typed.csv")
    expect(details.get_by_text("Column names are generated")).to_be_visible()
    expect(details.get_by_role("cell", name="column0")).to_be_visible()


def test_an_all_text_file_without_a_header_says_a_row_became_the_names(
    app: Page,
) -> None:
    # CFX-APP-first-slice/AC-22
    connect(app, CSV_DIR / "no_header_all_text.csv")

    details = details_of(app, "no_header_all_text.csv")
    expect(details.get_by_text("A row from the file was used as column names")).to_be_visible()
    expect(details.get_by_role("cell", name="Ann", exact=True)).to_be_visible()


def test_a_bracket_in_the_name_opens_that_file_and_not_a_similar_one(
    app: Page, tmp_path: Path
) -> None:
    # Found while building T05: DuckDB read report[1].csv as a pattern, and opened report1.csv.
    shutil.copy(CSV_DIR / "comma_utf8.csv", tmp_path / "report[1].csv")
    (tmp_path / "report1.csv").write_text("wrong,file\n1,2\n")

    connect(app, tmp_path / "report[1].csv")

    details = details_of(app, "report[1].csv")
    expect(details.get_by_role("cell", name="joined")).to_be_visible()
    expect(details.get_by_role("cell", name="wrong")).to_have_count(0)


@pytest.mark.parametrize(
    ("path", "message"),
    [
        pytest.param("/no/such/file.csv", "no file", marks=posix_only),
        pytest.param("/tmp", "folder", marks=posix_only),
        ("relative/file.csv", "full path"),
        pytest.param("/dev/zero", "not a regular file", marks=posix_only),
        (str(CSV_DIR / "empty.csv"), "empty"),
        (str(CSV_DIR / "binary.csv"), "UTF-16"),
    ],
)
def test_a_bad_path_shows_a_specific_error(app: Page, path: str, message: str) -> None:
    # CFX-APP-first-slice/AC-21
    connect(app, path)

    expect(app.get_by_role("alert")).to_contain_text(message)
    expect(app.get_by_text("No sources yet")).to_be_visible()


def test_sources_survive_a_restart_and_a_moved_file_is_marked(
    start_drivnx: StartDrivnX, browser: Browser, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-15 and CFX-APP-first-slice/AC-23
    workspace = tmp_path / "workspace"
    data = tmp_path / "data.csv"
    shutil.copy(CSV_DIR / "comma_utf8.csv", data)
    before = data.read_bytes()
    first = start_drivnx("--no-browser", workspace=workspace)
    context = browser.new_context()
    page = context.new_page()
    open_set_up_app(page, first.wait_for_launch().url)
    connect(page, data)
    expect(details_of(page, "data.csv")).to_be_visible()
    context.close()
    first.process.terminate()
    first.process.wait(timeout=10)
    assert data.read_bytes() == before

    second = start_drivnx("--no-browser", workspace=workspace)
    context = browser.new_context()
    page = context.new_page()
    page.goto(second.wait_for_launch().url)
    details = details_of(page, "data.csv")
    expect(details.get_by_role("cell", name="joined")).to_be_visible()
    expect(details.get_by_text("can't find this file")).to_have_count(0)
    context.close()
    second.process.terminate()
    second.process.wait(timeout=10)
    data.rename(tmp_path / "moved.csv")

    third = start_drivnx("--no-browser", workspace=workspace)
    context = browser.new_context()
    page = context.new_page()
    page.goto(third.wait_for_launch().url)

    expect(
        details_of(page, "data.csv").get_by_text("can't find this file")
    ).to_be_visible()
    expect(
        page.get_by_role("navigation", name="Data Explorer").get_by_text("Unavailable")
    ).to_be_visible()
    context.close()


def test_connecting_reports_no_console_errors_or_outside_requests(
    start_drivnx: StartDrivnX, page: Page
) -> None:
    # CFX-APP-first-slice/AC-16 still holds now that a source is read.
    requests: list[str] = []
    problems: list[str] = []
    page.on("request", lambda request: requests.append(request.url))
    page.on("pageerror", lambda error: problems.append(str(error)))
    page.on(
        "console",
        lambda message: (
            problems.append(message.text) if message.type == "error" else None
        ),
    )
    launch = start_drivnx("--no-browser").wait_for_launch()
    open_set_up_app(page, launch.url)

    connect(page, CSV_DIR / "comma_utf8.csv")
    expect(details_of(page, "comma_utf8.csv")).to_be_visible()

    assert [url for url in requests if not url.startswith(launch.origin)] == []
    assert problems == []
