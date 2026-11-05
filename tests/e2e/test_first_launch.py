"""First launch in a real browser, CFX-APP-first-slice AC-01, AC-14, AC-15, AC-16, AC-32, AC-33, AC-34."""

import time
from pathlib import Path

import pytest
from playwright.sync_api import Browser, Page, expect

from ..conftest import StartConflux, posix_only


def test_a_new_workspace_shows_where_it_is_and_asks_for_a_name_and_ai_mode(
    start_conflux: StartConflux, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-14
    workspace = tmp_path / "my workspace"
    launch = start_conflux("--no-browser", workspace=workspace).wait_for_launch()

    page.goto(launch.url)

    expect(page.get_by_text(str(workspace))).to_be_visible()
    expect(page.get_by_text("No credentials are stored", exact=False)).to_be_visible()
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    expect(page.get_by_role("radio", name="Off")).to_be_checked()
    expect(page.get_by_role("radio", name="External")).not_to_be_checked()
    expect(page.get_by_role("radio", name="Local", exact=True)).not_to_be_checked()


def finish_setup(page: Page, name: str, mode: str = "Off") -> None:
    page.get_by_role("textbox", name="Workspace name").fill(name)
    page.get_by_role("radio", name=mode, exact=True).check()
    page.get_by_role("button", name="Create workspace").click()
    expect(page.get_by_role("heading", name=name)).to_be_visible()


def run_conflux_and_catch_the_browser(
    start_conflux: StartConflux, tmp_path: Path, cwd: Path | None = None
) -> str:
    """Run `conflux` with no argument, and return the URL it asked a browser to open."""
    opened = tmp_path / "opened"
    browser = tmp_path / "browser"
    browser.write_text(
        f'#!/bin/sh\nprintf %s "$1" > "{opened}.tmp"\nmv "{opened}.tmp" "{opened}"\n'
    )
    browser.chmod(0o755)

    start_conflux(workspace=None, browser=f"{browser} %s", cwd=cwd)
    deadline = time.monotonic() + 15
    while not opened.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    return opened.read_text()


@posix_only
def test_conflux_with_no_argument_opens_the_browser_signed_in_on_the_default_workspace(
    start_conflux: StartConflux, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-01
    default = tmp_path / "home" / "Conflux" / "default"
    assert not default.exists()

    url = run_conflux_and_catch_the_browser(start_conflux, tmp_path)
    assert url.startswith("http://127.0.0.1:")

    page.goto(url)

    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    expect(page.get_by_text(str(default))).to_be_visible()
    assert "launch" not in page.url
    assert (default / "workspace.db").is_file()


@pytest.mark.parametrize(
    ("mode", "privacy"),
    [
        ("Off", "Processing: Local · External AI: Off"),
        ("External", "Processing: Local · External AI: On"),
        ("Local", "Processing: Local · Local AI: On"),
    ],
)
def test_setup_opens_the_app_and_a_restart_keeps_it(
    start_conflux: StartConflux,
    browser: Browser,
    tmp_path: Path,
    mode: str,
    privacy: str,
) -> None:
    # CFX-APP-first-slice/AC-15 and CFX-APP-first-slice/AC-34
    workspace = tmp_path / "workspace"
    first = start_conflux("--no-browser", workspace=workspace)
    context = browser.new_context()
    page = context.new_page()
    page.goto(first.wait_for_launch().url)
    finish_setup(page, "Customer data", mode)
    expect(page.get_by_role("status")).to_have_text(privacy)
    context.close()
    first.process.terminate()
    first.process.wait(timeout=10)

    second = start_conflux("--no-browser", workspace=workspace)
    context = browser.new_context()
    page = context.new_page()
    page.goto(second.wait_for_launch().url)

    expect(page.get_by_role("heading", name="Customer data")).to_be_visible()
    expect(page.get_by_role("status")).to_have_text(privacy)
    expect(page.get_by_text(f"AI mode: {mode}")).to_be_visible()
    expect(page.get_by_role("textbox", name="Workspace name")).to_have_count(0)
    context.close()


def test_the_app_layout_shows_and_marks_what_is_not_built(
    start_conflux: StartConflux, page: Page
) -> None:
    # CFX-APP-first-slice/AC-32 and CFX-APP-first-slice/AC-33
    page.goto(start_conflux("--no-browser").wait_for_launch().url)
    finish_setup(page, "Sales")

    expect(page.get_by_role("navigation", name="Data Explorer")).to_be_visible()
    expect(
        page.get_by_role("complementary", name="Intelligence & Review")
    ).to_contain_text("Not available yet")
    expect(page.get_by_text("Profile status: Not available yet")).to_be_visible()
    expect(page.get_by_role("button", name="Export")).to_be_disabled()
    expect(page.get_by_role("tab")).to_have_count(4)
    for tab in page.get_by_role("tab").all():
        expect(tab).to_be_disabled()
        expect(tab).to_contain_text("Not available yet")


@pytest.mark.parametrize("mode", ["Off", "External", "Local"])
def test_the_browser_only_talks_to_conflux_and_reports_no_errors(
    start_conflux: StartConflux, page: Page, mode: str
) -> None:
    # CFX-APP-first-slice/AC-16: no AI request in any mode, and no console errors, including the favicon.
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
    launch = start_conflux("--no-browser").wait_for_launch()

    page.goto(launch.url)
    finish_setup(page, "Quiet", mode)
    page.reload()
    expect(page.get_by_role("heading", name="Quiet")).to_be_visible()
    page.wait_for_timeout(300)

    assert requests
    assert [url for url in requests if not url.startswith(launch.origin)] == []
    assert problems == []


def test_saving_after_the_runtime_stopped_says_to_start_it_again(
    start_conflux: StartConflux, page: Page
) -> None:
    # Found in the T04 review: this blamed the workspace name, and retrying could never work.
    conflux = start_conflux("--no-browser")
    page.goto(conflux.wait_for_launch().url)
    page.get_by_role("textbox", name="Workspace name").fill("A good name")
    conflux.process.terminate()
    conflux.process.wait(timeout=10)

    page.get_by_role("button", name="Create workspace").click()

    expect(page.get_by_role("alert")).to_contain_text("Start Conflux again")
    expect(page.get_by_role("alert")).not_to_contain_text("workspace name")
