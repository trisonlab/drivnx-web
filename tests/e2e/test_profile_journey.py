"""The profile journey in a real browser, against the installed wheel, CFX-APP-csv-profile AC-33
and AC-41."""

import re
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Page, expect

from ..conftest import RunningDrivnX, StartDrivnX
from .test_accessibility import violations
from .test_connect_csv import connect, details_of, open_set_up_app

ROWS = 3_000
TITLE = "Quarterly Zephyrine export"
# Invented, with a comma in each, so detection reads the tab-separated table wrongly until it's
# corrected.
CITIES = (
    "Quillmarsh, Norvale",
    "Brindlewick, Ostray",
    "Vexhollow, Carran",
    "Thistlemoor, Ebbing",
)


def journey_csv(path: Path) -> list[str]:
    """Write a synthetic file that needs correcting, and return every distinct value in it.

    A title line sits above a tab-separated table. `email` looks personal, a quarter of `city` is
    missing, and every value is distinctive enough that finding it in a file can't be chance.
    """
    rows = [
        (
            f"rq{row}vz",
            f"zq{row}k@planted.example",
            "" if row % 4 == 0 else CITIES[row % len(CITIES)],
            str(91_827_364_500 + row),
        )
        for row in range(ROWS)
    ]
    table = [("id", "email", "city", "amount"), *rows]
    path.write_text(
        TITLE + "\n" + "".join("\t".join(row) + "\n" for row in table),
        encoding="utf-8",
        newline="",
    )
    return sorted({TITLE, *(value for row in rows for value in row if value)})


def walk_the_profile_journey(
    page: Page, path: Path, at_each_state: Callable[[], None]
) -> None:
    """Connect, correct how it's read, fast profile, the values of a flagged column and a number
    column, full profile, and a finding's evidence."""
    connect(page, path)
    details = details_of(page, path.name)
    expect(details).to_be_visible()

    details.get_by_text("How this file is read").click()
    expect(details.get_by_role("combobox", name="Delimiter")).to_be_visible()
    at_each_state()

    details.get_by_role("combobox", name="Delimiter").select_option(label="Tab")
    details.get_by_role("spinbutton", name="Lines to skip above the table").fill("1")
    details.get_by_role("button", name="Save settings").click()
    expect(details.get_by_text("Settings saved")).to_be_visible()
    expect(details.get_by_role("cell", name="email", exact=True)).to_be_visible()
    profile = page.get_by_role("tabpanel", name="Profile")
    expect(profile.get_by_text("No profile has been run with these settings yet.")).to_be_visible()
    at_each_state()

    profile.get_by_role("button", name="Fast profile").click()
    expect(profile.get_by_role("row").filter(has_text="amount")).to_be_visible()
    expect(page.get_by_text("Profile status: Completed")).to_be_visible()
    at_each_state()

    profile.get_by_role("button", name="Show values of email").click()
    asking = profile.get_by_role(
        "alertdialog", name="This column may hold personal data. Show its values?"
    )
    expect(asking).to_be_visible()
    at_each_state()

    asking.get_by_role("button", name="Show values").click()
    shown = profile.get_by_role("region", name="Values in email")
    expect(shown.get_by_role("table", name="Most frequent values")).to_contain_text(
        "@planted.example"
    )
    at_each_state()

    # A number column's values add a histogram, mean and median, worked out on request too.
    profile.get_by_role("button", name="Show values of amount").click()
    numbers = profile.get_by_role("region", name="Values in amount")
    expect(numbers.get_by_role("table", name="Histogram")).to_be_visible()
    at_each_state()

    profile.get_by_role("button", name="Full profile").click()
    expect(profile.get_by_text(f"This file has {ROWS:,} rows.")).to_be_visible()
    findings = page.get_by_role("complementary", name="Intelligence & Review").get_by_role(
        "region", name="Findings"
    )
    missing = findings.get_by_role("article").filter(has_text="city is missing")
    expect(missing).to_be_visible()
    at_each_state()

    missing.get_by_role("button", name="Show the evidence").click()
    city = profile.get_by_role("row").filter(has_text="city")
    expect(city.locator(":focus")).to_be_visible()
    at_each_state()


def test_the_profile_journey_on_the_installed_wheel_from_one_origin(
    start_drivnx: StartDrivnX, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-41, with an automated accessibility check in each state.
    path = tmp_path / "planted.csv"
    journey_csv(path)
    requested: list[str] = []
    page.on("request", lambda request: requested.append(request.url))
    url = start_drivnx("--no-browser").wait_for_launch().url
    open_set_up_app(page, url)

    def no_violations() -> None:
        assert violations(page) == []

    walk_the_profile_journey(page, path, no_violations)

    origin = urlsplit(url)
    assert origin.hostname == "127.0.0.1"
    assert {urlsplit(address).netloc for address in requested} == {origin.netloc}
    paths = [urlsplit(address).path for address in requested]
    assert any(path.startswith("/assets/") for path in paths)
    assert any(path.startswith("/api/") for path in paths)


# What `zq0k@planted.example` is saved as: letters become A and digits 9 (ADR 0004).
MASKED_EMAIL = b"AA9A@AAAAAAA.AAAAAAA"
LOCK_NAME = ".drivnx.lock"
# Any part of a planted value that could tell someone what it was: a value saved cut short, or a
# word of it, is found as well as the whole. Masked patterns can't match, as they hold no letter
# but A and no digit but 9. Every planted value is checked to match before a scan is trusted.
PLANTED = re.compile(
    rb"zq\w+?k|rq\d+vz|9182736\d|7293846\d|planted\.example|Zephyrine"
    rb"|Quillmarsh|Norvale|Brindlewick|Ostray|Vexhollow|Carran|Thistlemoor|Ebbing"
)


def every_file_in(folder: Path) -> bytes:
    """Every byte DrivnX keeps in the folder, as anyone copying it would get them.

    The lock file is never written, and on Windows its first byte is locked while DrivnX runs, so
    it's checked to be empty instead of read.
    """
    assert (folder / "workspace.db").is_file()  # so a scan of the wrong folder can't pass
    files = sorted(path for path in folder.rglob("*") if path.is_file())
    assert all(path.stat().st_size == 0 for path in files if path.name == LOCK_NAME)
    return b"".join(path.read_bytes() for path in files if path.name != LOCK_NAME)


def planted_in(data: bytes) -> list[bytes]:
    """Every part of a planted value found in the data. SQLite and the logs hold text as UTF-8."""
    return PLANTED.findall(data)


def assert_no_value_kept_or_logged(
    drivnx: RunningDrivnX, workspace: Path, values: list[str]
) -> None:
    """Scan the workspace while DrivnX runs, with the WAL and anything a job left behind, then
    stop it and scan the workspace again and everything it printed."""
    assert [value for value in values if not planted_in(value.encode())] == []
    assert planted_in(every_file_in(workspace)) == []
    drivnx.process.terminate()
    drivnx.process.wait(timeout=10)
    assert planted_in(every_file_in(workspace)) == []
    output = drivnx.all_output()
    assert "ASGI [" in output  # uvicorn's trace lines, so the logs were at their most detailed
    assert planted_in(output.encode()) == []


def test_a_journey_leaves_no_value_from_the_file_in_the_workspace_or_logs(
    start_drivnx: StartDrivnX, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-33, with values shown on request and the logs at their most detailed.
    path = tmp_path / "planted.csv"
    planted = journey_csv(path)
    workspace = tmp_path / "workspace"
    drivnx = start_drivnx("--no-browser", "--log-level", "trace", workspace=workspace)
    open_set_up_app(page, drivnx.wait_for_launch().url)

    walk_the_profile_journey(page, path, lambda: None)

    assert MASKED_EMAIL in every_file_in(workspace)  # so a scan of the wrong files can't pass
    assert_no_value_kept_or_logged(drivnx, workspace, planted)


def late_quote_csv(path: Path) -> list[str]:
    """A quote never closed after the first 100,000 rows, which fails a full profile, between
    planted values, and return them. The rows before are ordinary and not planted."""
    before = ("7293846150", "zqbeforek@planted.example")
    broken = ("7293846151", "zqoopsk@planted.example")
    after = [(f"72938462{row:02d}", f"zq{row}afterk@planted.example") for row in range(3)]
    path.write_text(
        "id,name\n"
        + "".join(f"{row},n{row}\n" for row in range(120_000))
        + ",".join(before)
        + "\n"
        + f'{broken[0]},"{broken[1]}\n'
        + "".join(",".join(row) + "\n" for row in after),
        encoding="utf-8",
        newline="",
    )
    return [*before, *broken, *(value for row in after for value in row)]


def test_a_failed_full_profile_logs_no_value_from_the_file(
    start_drivnx: StartDrivnX, page: Page, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-33: a row DuckDB can't read is where an error could quote the file.
    path = tmp_path / "late_quote.csv"
    planted = late_quote_csv(path)
    workspace = tmp_path / "workspace"
    drivnx = start_drivnx("--no-browser", "--log-level", "trace", workspace=workspace)
    open_set_up_app(page, drivnx.wait_for_launch().url)
    connect(page, path)
    expect(details_of(page, path.name)).to_be_visible()
    profile = page.get_by_role("tabpanel", name="Profile")

    profile.get_by_role("button", name="Full profile").click()

    expect(profile.get_by_role("alert")).to_contain_text("A row in this file can't be read")
    assert violations(page) == []  # CFX-APP-csv-profile/AC-41: a profile that couldn't finish
    assert_no_value_kept_or_logged(drivnx, workspace, planted)
