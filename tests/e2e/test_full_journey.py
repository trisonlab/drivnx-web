"""The whole journey through the installed wheel, CFX-APP-first-slice/AC-51."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect

from ..conftest import StartConflux, posix_only
from .test_connect_csv import connect, details_of
from .test_first_launch import finish_setup, run_conflux_and_catch_the_browser
from .test_preview import preview_of


@posix_only
def test_the_journey_through_the_installed_command_on_one_origin(
    start_conflux: StartConflux, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-51, through CFX-APP-first-slice/AC-01, CFX-APP-first-slice/AC-14,
    # CFX-APP-first-slice/AC-20, CFX-APP-first-slice/AC-24 and CFX-APP-first-slice/AC-27.
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    people = tmp_path / "people.csv"
    people.write_text(
        "id,name\n" + "".join(f"{row},name{row}\n" for row in range(1, 121))
    )
    file_before = (people.read_bytes(), people.stat().st_mtime_ns)
    default = tmp_path / "home" / "Conflux" / "default"
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))

    # AC-01: the workspace is created, and the app loads signed in with the code gone.
    url = run_conflux_and_catch_the_browser(start_conflux, tmp_path, cwd=elsewhere)
    page.goto(url)
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    assert "launch" not in page.url
    assert (default / "workspace.db").is_file()

    # AC-14: it says where the workspace is, and asks for a name and an AI mode, with off selected.
    expect(page.get_by_text(str(default))).to_be_visible()
    expect(page.get_by_role("radio", name="Off")).to_be_checked()
    finish_setup(page, "Sales")

    # AC-20: connecting leaves the file as it was, with no copy of it in the workspace.
    connect(page, people)
    details = details_of(page, "people.csv")
    expect(details).to_be_visible()
    assert (people.read_bytes(), people.stat().st_mtime_ns) == file_before
    assert not list(default.rglob("people*"))

    # AC-24: the app shows the file and each of its columns.
    expect(details.get_by_role("row", name=re.compile("^id"))).to_be_visible()
    expect(details.get_by_role("row", name=re.compile("^name"))).to_be_visible()

    # AC-27: the first 50 rows, and the pages either side, with Next off on the last.
    preview = preview_of(page)
    expect(preview.get_by_text("Rows 1 to 50")).to_be_visible()
    expect(preview.get_by_role("row")).to_have_count(51)
    following = preview.get_by_role("button", name="Next")
    following.click()
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()
    following.click()
    expect(preview.get_by_text("Rows 101 to 120")).to_be_visible()
    expect(following).to_be_disabled()
    preview.get_by_role("button", name="Previous").click()
    expect(preview.get_by_text("Rows 51 to 100")).to_be_visible()

    # AC-51: the bundled UI and the API, all from the one address the command printed.
    origin = urlsplit(url)
    assert origin.hostname == "127.0.0.1"
    assert {urlsplit(address).netloc for address in requested} == {origin.netloc}
    paths = [urlsplit(address).path for address in requested]
    assert any(path.startswith("/assets/") for path in paths)
    assert any(path.startswith("/api/") for path in paths)
