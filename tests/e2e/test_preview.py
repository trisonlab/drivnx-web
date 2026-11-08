"""Paging through a CSV in a real browser, CFX-APP-first-slice AC-27, AC-29, AC-31."""

import shutil
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from ..conftest import REPO_ROOT, StartDrivnX
from .test_connect_csv import connect, open_set_up_app


@pytest.fixture
def app(start_drivnx: StartDrivnX, page: Page) -> Page:
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    return page


@pytest.fixture
def people(tmp_path: Path) -> Path:
    path = tmp_path / "people.csv"
    rows = "".join(f"{number},name{number}\n" for number in range(1, 121))
    path.write_text("id,name\n" + rows)
    return path


def preview_of(page: Page):
    return page.get_by_role("region", name="Preview")


def test_paging_through_a_file(app: Page, people: Path) -> None:
    # CFX-APP-first-slice/AC-27
    connect(app, people)
    preview = preview_of(app)
    expect(preview.get_by_role("cell", name="name1", exact=True)).to_be_visible()
    expect(preview.get_by_role("row")).to_have_count(51)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    previous = preview.get_by_role("button", name="Previous")
    following = preview.get_by_role("button", name="Next")
    expect(previous).to_be_disabled()

    following.click()
    expect(preview.get_by_role("cell", name="name51", exact=True)).to_be_visible()
    expect(preview.get_by_role("cell", name="name1", exact=True)).to_have_count(0)

    following.click()
    expect(preview.get_by_role("cell", name="name120", exact=True)).to_be_visible()
    expect(preview.get_by_text("Rows 101 to 120")).to_be_visible()
    expect(following).to_be_disabled()

    previous.click()
    expect(preview.get_by_role("cell", name="name51", exact=True)).to_be_visible()
    expect(following).to_be_enabled()


def test_paging_works_from_the_keyboard_alone(app: Page, people: Path) -> None:
    # CFX-APP-first-slice/AC-27, and the spec's keyboard requirement under Non-functional.
    connect(app, people)
    preview = preview_of(app)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    following = preview.get_by_role("button", name="Next")
    following.focus()

    app.keyboard.press("Enter")
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()
    expect(following).to_be_focused()
    app.keyboard.press("Enter")
    expect(preview.get_by_text("Rows 101 to 120")).to_be_visible()
    expect(following).to_be_disabled()

    app.keyboard.press("Shift+Tab")
    expect(preview.get_by_role("button", name="Previous")).to_be_focused()
    app.keyboard.press("Enter")
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()


def test_script_text_in_a_cell_is_shown_and_nothing_runs(
    app: Page, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-29
    script = '<script>window.ran = true</script><img src=x onerror="window.ran = true">'
    path = tmp_path / "html.csv"
    path.write_text('id,note\n1,"' + script.replace('"', '""') + '"\n')
    dialogs: list[str] = []
    app.on("dialog", lambda dialog: dialogs.append(dialog.message))

    connect(app, path)

    preview = preview_of(app)
    expect(preview.get_by_role("cell", name=script)).to_be_visible()
    expect(preview.locator("td img, td script")).to_have_count(0)
    assert app.evaluate("window.ran") is None
    assert dialogs == []


def test_a_long_cell_is_cut_and_marked(app: Page, tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-29
    path = tmp_path / "long.csv"
    path.write_text(f"id,note\n1,{'x' * 5000}\n2,short\n")

    connect(app, path)

    preview = preview_of(app)
    cell = preview.get_by_role("row").nth(1).get_by_role("cell").nth(1)
    expect(cell).to_contain_text("cut at 1,000 characters")
    assert cell.inner_text().count("x") == 1000


def test_a_file_deleted_between_pages_gives_a_clear_error_and_a_way_back(
    app: Page, people: Path
) -> None:
    # CFX-APP-first-slice/AC-31
    connect(app, people)
    preview = preview_of(app)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()

    people.unlink()
    preview.get_by_role("button", name="Next").click()

    expect(preview.get_by_role("alert")).to_contain_text("moved or been deleted")
    expect(preview.get_by_role("button", name="Previous")).to_be_enabled()
    expect(app.get_by_role("heading", name="people.csv")).to_be_visible()


def test_lines_skipped_above_the_table_are_stated(app: Page) -> None:
    # CFX-APP-first-slice/AC-27
    connect(app, REPO_ROOT / "testdata" / "csv" / "title_line.csv")

    preview = preview_of(app)
    expect(preview.get_by_text("1 line above this table isn't shown.")).to_be_visible()
    expect(preview.get_by_role("cell", name="Ann", exact=True).first).to_be_visible()


def test_a_press_while_a_slow_page_loads_keeps_the_label_true(
    app: Page, people: Path
) -> None:
    # CFX-APP-first-slice/AC-27: the request is held, because a sleep here would block the reads.
    connect(app, people)
    preview = preview_of(app)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    held = []
    app.route("**/rows*", lambda route: held.append(route))
    following = preview.get_by_role("button", name="Next")

    following.click()
    following.click()

    expect(preview.get_by_text("Loading")).to_be_visible()
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    expect(preview.get_by_role("cell", name="name1", exact=True)).to_be_visible()
    assert len(held) == 1
    held[0].continue_()
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()


def test_row_values_never_reach_the_browser_disk_cache(
    browser: Browser, start_drivnx: StartDrivnX, people: Path, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-30: found verifying T06. The UI bundle is the control, because finding
    # no row values proves nothing unless the cache did store something.
    profile = tmp_path / "profile"
    context = browser.browser_type.launch_persistent_context(
        profile, executable_path=shutil.which("chromium"), args=["--no-sandbox"]
    )
    page = context.new_page()
    open_set_up_app(page, start_drivnx("--no-browser").wait_for_launch().url)
    connect(page, people)
    expect(preview_of(page).get_by_text("Rows 1 to 50")).to_be_visible()
    preview_of(page).get_by_role("button", name="Next").click()
    expect(preview_of(page).get_by_text("Rows 51 to 100")).to_be_visible()
    context.close()

    stored = b"".join(
        entry.read_bytes() for entry in profile.rglob("*") if entry.is_file()
    )
    assert b"Create workspace" in stored, (
        "the cache stored nothing, so this proves nothing"
    )
    assert b"name51" not in stored
    assert b'"name1"' not in stored
