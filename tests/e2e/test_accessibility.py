"""The screens that exist so far, against WCAG 2.2 AA, from the spec's non-functional requirements.

An automated check finds only part of what WCAG asks, so this doesn't prove the screens conform.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import REPO_ROOT, StartDrivnX
from .test_connect_csv import connect, details_of, open_set_up_app
from .test_preview import preview_of

AXE = REPO_ROOT / "apps/web/node_modules/axe-core/axe.min.js"
LEVELS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
CSV = REPO_ROOT / "testdata/csv/comma_utf8.csv"


def violations(page: Page) -> list[str]:
    page.evaluate(AXE.read_text())
    found = page.evaluate(
        "levels => axe.run(document, {runOnly: {type: 'tag', values: levels}})", LEVELS
    )["violations"]
    return [
        f"{problem['id']}: {node['target']} {node['failureSummary']}"
        for problem in found
        for node in problem["nodes"]
    ]


@pytest.fixture
def launch_url(start_drivnx: StartDrivnX) -> str:
    return start_drivnx("--no-browser").wait_for_launch().url


def test_the_setup_screen(page: Page, launch_url: str) -> None:
    page.goto(launch_url)
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()

    assert violations(page) == []


def test_setup_with_its_error_showing(page: Page, launch_url: str) -> None:
    page.goto(launch_url)
    page.route(
        "**/api/workspace",
        lambda route: (
            route.fulfill(status=500)
            if route.request.method != "GET"
            else route.continue_()
        ),
    )
    page.get_by_role("textbox", name="Workspace name").fill("Sales")
    page.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_role("alert")).to_be_visible()

    assert violations(page) == []


def test_the_app_with_nothing_connected(page: Page, launch_url: str) -> None:
    open_set_up_app(page, launch_url)

    assert violations(page) == []


def test_the_app_after_a_failed_connection(
    page: Page, launch_url: str, tmp_path: Path
) -> None:
    open_set_up_app(page, launch_url)
    connect(page, tmp_path / "missing.csv")
    expect(page.get_by_role("alert")).to_be_visible()

    assert violations(page) == []


def test_a_connected_file_and_its_preview(page: Page, launch_url: str) -> None:
    open_set_up_app(page, launch_url)
    connect(page, CSV)
    expect(details_of(page, CSV.name)).to_be_visible()
    expect(preview_of(page).get_by_text("Rows 1 to")).to_be_visible()

    assert violations(page) == []


def test_the_check_notices_a_problem(page: Page, launch_url: str) -> None:
    # A check that can't fail proves nothing. The app's CSP drops inline style attributes, so this
    # goes through the DOM: text at about 1.6:1 and a button with no name.
    page.goto(launch_url)
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    page.evaluate(
        """() => {
            const main = document.querySelector("main");
            const faint = document.createElement("p");
            faint.textContent = "faint text";
            faint.style.color = "#ccc";
            faint.style.background = "#fff";
            main.prepend(faint, document.createElement("button"));
        }"""
    )

    found = "\n".join(violations(page))

    assert "color-contrast" in found
    assert "button-name" in found


def test_a_connected_file_reflows_at_320_pixels(page: Page, launch_url: str) -> None:
    # WCAG 1.4.10: the page itself doesn't scroll sideways. A wide table may, inside its own box.
    open_set_up_app(page, launch_url)
    connect(page, CSV)
    expect(preview_of(page).get_by_text("Rows 1 to")).to_be_visible()

    page.set_viewport_size({"width": 320, "height": 640})

    assert page.evaluate("document.documentElement.scrollWidth") <= 320


def test_the_journey_works_by_keyboard_alone(
    page: Page, launch_url: str, tmp_path: Path
) -> None:
    people = tmp_path / "people.csv"
    people.write_text(
        "id,name\n" + "".join(f"{row},name{row}\n" for row in range(1, 61))
    )
    keyboard = page.keyboard
    page.goto(launch_url)
    name = page.get_by_role("textbox", name="Workspace name")
    expect(name).to_be_visible()

    def tab_to(target) -> None:
        for _ in range(30):
            keyboard.press("Tab")
            if target.evaluate("element => element === document.activeElement"):
                # WCAG 2.4.7: what has the keyboard's focus can be seen.
                assert (
                    target.evaluate("element => getComputedStyle(element).outlineStyle")
                    != "none"
                )
                return
        pytest.fail(f"Tab never reached {target}")

    tab_to(name)
    keyboard.type("Sales")
    tab_to(page.get_by_role("button", name="Create workspace"))
    keyboard.press("Enter")

    path = page.get_by_role("textbox", name="CSV file path")
    expect(path).to_be_visible()
    tab_to(path)
    keyboard.type(str(people))
    keyboard.press("Enter")

    preview = preview_of(page)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    following = preview.get_by_role("button", name="Next")
    tab_to(following)
    keyboard.press("Enter")
    expect(preview.get_by_text("Rows 51 to 60")).to_be_visible()
