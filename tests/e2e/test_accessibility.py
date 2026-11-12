"""The screens that exist so far, against WCAG 2.2 AA, from the spec's non-functional requirements.

An automated check finds only part of what WCAG asks, so this doesn't prove the screens conform.
"""

from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from ..conftest import REPO_ROOT, StartDrivnX
from ..integration.test_full_profile import large_csv
from .test_connect_csv import connect, details_of, open_set_up_app
from .test_preview import preview_of

AXE = REPO_ROOT / "apps/web/node_modules/axe-core/axe.min.js"
LEVELS = ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"]
CSV = REPO_ROOT / "testdata/csv/comma_utf8.csv"


def violations(page: Page) -> list[str]:
    page.evaluate(AXE.read_text(encoding="utf-8"))
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
        "id,name\n" + "".join(f"{row},name{row}\n" for row in range(1, 61)),
        encoding="utf-8",
        newline="",
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


# The new screens of CFX-APP-csv-profile in the states its journey doesn't reach
# (tests/e2e/test_profile_journey.py), for CFX-APP-csv-profile/AC-41.


def profile_of(page: Page):
    return page.get_by_role("tabpanel", name="Profile")


def test_a_running_profile_and_its_cancel(page: Page, launch_url: str, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-41
    path = large_csv(tmp_path / "large.csv", rows=20_000_000)
    open_set_up_app(page, launch_url)
    connect(page, path)
    expect(details_of(page, path.name)).to_be_visible()
    profile_of(page).get_by_role("button", name="Full profile").click()
    cancel = profile_of(page).get_by_role("button", name="Cancel profile")
    expect(cancel).to_be_visible()

    assert violations(page) == []

    cancel.click()
    expect(profile_of(page).get_by_text("This profile was cancelled.")).to_be_visible()

    assert violations(page) == []


def test_settings_that_cannot_read_the_file(page: Page, launch_url: str) -> None:
    # CFX-APP-csv-profile/AC-41
    latin1 = REPO_ROOT / "testdata/csv/comma_latin1.csv"
    open_set_up_app(page, launch_url)
    connect(page, latin1)
    details = details_of(page, latin1.name)
    details.get_by_text("How this file is read").click()
    details.get_by_role("combobox", name="Encoding").select_option("UTF-8")
    details.get_by_role("button", name="Save settings").click()
    expect(details.get_by_role("alert")).to_contain_text("previous settings are still in use")

    assert violations(page) == []


def test_an_earlier_run_opened_after_a_correction(page: Page, launch_url: str) -> None:
    # CFX-APP-csv-profile/AC-41
    open_set_up_app(page, launch_url)
    connect(page, CSV)
    profile = profile_of(page)
    profile.get_by_role("button", name="Fast profile").click()
    expect(profile.get_by_role("table")).to_be_visible()
    details = details_of(page, CSV.name)
    details.get_by_text("How this file is read").click()
    details.get_by_role("combobox", name="Delimiter").select_option(label="Tab")
    details.get_by_role("button", name="Save settings").click()
    earlier = profile.get_by_role("list", name="Runs of this file").get_by_role("listitem")
    expect(earlier).to_contain_text("Used earlier settings")

    assert violations(page) == []

    earlier.get_by_role("button", name="Open").click()
    expect(profile).to_contain_text(
        "This profile used earlier settings, so its findings aren't current."
    )
    expect(page.get_by_role("region", name="Findings")).to_contain_text(
        "These findings are from a profile that used earlier settings"
    )

    assert violations(page) == []


def test_a_profile_that_found_nothing(page: Page, launch_url: str, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-41: no column is distinct, one value, missing or mistyped, and no row
    # repeats, so none of the six kinds of finding applies.
    path = tmp_path / "plain.csv"
    path.write_text("size,colour\nS,red\nS,blue\nL,red\nL,blue\n", encoding="utf-8", newline="")
    open_set_up_app(page, launch_url)
    connect(page, path)
    expect(details_of(page, path.name)).to_be_visible()
    profile_of(page).get_by_role("button", name="Full profile").click()
    expect(page.get_by_role("region", name="Findings")).to_contain_text(
        "This profile found nothing to point out."
    )

    assert violations(page) == []


def test_a_profile_interrupted_when_drivnx_stopped(
    page: Page, start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-41: DrivnX stops during a run, and the next start marks it
    # interrupted.
    path = large_csv(tmp_path / "large.csv", rows=20_000_000)
    workspace = tmp_path / "workspace"
    first = start_drivnx("--no-browser", workspace=workspace)
    open_set_up_app(page, first.wait_for_launch().url)
    connect(page, path)
    expect(details_of(page, path.name)).to_be_visible()
    profile_of(page).get_by_role("button", name="Full profile").click()
    expect(profile_of(page).get_by_role("button", name="Cancel profile")).to_be_visible()
    first.process.terminate()
    first.process.wait(timeout=10)

    page.goto(start_drivnx("--no-browser", workspace=workspace).wait_for_launch().url)
    page.get_by_role("button", name=path.name).click()
    expect(
        profile_of(page).get_by_text("This profile was interrupted when DrivnX stopped.")
    ).to_be_visible()

    assert violations(page) == []
