"""Measures the spec's performance targets on a 1 GB synthetic CSV, through the real UI.

`uv run --frozen python tests/measure_large_csv.py <empty directory>`. The directory needs about
1 GB for each encoding, one at a time, and should be on the disk being measured, as a RAM disk
flatters the figures. Everything is removed afterwards, also after a failure. Not a test: pytest doesn't collect this, as the figures depend on the machine.

It times connecting and paging (CFX-APP-first-slice), then a fast profile, a full profile and a
cancel 2 s into a full one (CFX-APP-csv-profile), each from the click until the page shows the
result. The page polls a running profile once a second, so a profile's time includes up to 1 s of
waiting, as it does for the user.
"""

import csv
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

SIZE = 1_000_000_000
NAMES = ("Ann", "Bob", "Zoé", "José", "Cid", "Dee")
CITIES = ("Paris", "Lyon", "Orléans", "Málaga", "Nice", "Metz")
BUDGET_METADATA = 5.0
BUDGET_FIRST_PAGE = 2.0
BUDGET_FAST_PROFILE = 5.0
BUDGET_FULL_PROFILE = 60.0
BUDGET_CANCEL = 5.0
CANCEL_AFTER = 2.0
PROFILE_TIMEOUT_MS = 600_000


def drop_from_cache(path: Path) -> None:
    """Out of the page cache, so reading it really touches the disk. Linux and macOS differ here."""
    if hasattr(os, "posix_fadvise"):
        with path.open("rb") as file:
            os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)


def write_csv(path: Path, encoding: str) -> int:
    """Invented rows from a fixed seed, so every run measures the same file."""
    pick = random.Random(42)
    rows = 0
    with path.open("w", encoding=encoding, newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("id", "name", "city", "amount", "joined"))
        while file.tell() < SIZE:
            for _ in range(10_000):
                rows += 1
                writer.writerow(
                    (
                        rows,
                        pick.choice(NAMES),
                        pick.choice(CITIES),
                        f"{pick.random() * 1000:.2f}",
                        f"2024-{pick.randint(1, 12):02}-{pick.randint(1, 28):02}",
                    )
                )
        file.flush()
        os.fsync(file.fileno())
    drop_from_cache(path)
    return rows


def measure(browser, directory: Path, encoding: str) -> dict:
    path = directory / f"large_{encoding}.csv"
    workspace = directory / f"workspace_{encoding}"
    try:
        return measure_file(browser, path, workspace, encoding)
    finally:
        path.unlink(missing_ok=True)
        shutil.rmtree(workspace, ignore_errors=True)


def measure_file(browser, path: Path, workspace: Path, encoding: str) -> dict:
    started = time.monotonic()
    rows = write_csv(path, encoding)
    generated = time.monotonic() - started
    drivnx = subprocess.Popen(
        ["drivnx", "--no-browser", str(workspace)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for line in drivnx.stdout:
            if found := re.search(r"http://127\.0\.0\.1:\d+/#launch=\S+", line):
                break
        else:
            raise RuntimeError("drivnx exited without printing a link.")
        context = browser.new_context()
        page = context.new_page()
        page.goto(found.group(0))
        page.get_by_role("textbox", name="Workspace name").fill("Measuring")
        page.get_by_role("button", name="Create workspace").click()
        page.get_by_role("textbox", name="CSV file path").fill(str(path))

        started = time.monotonic()
        page.get_by_role("button", name="Connect").click()
        page.get_by_role("region", name=path.name).wait_for(timeout=120_000)
        metadata = time.monotonic() - started

        started = time.monotonic()
        preview = page.get_by_role("region", name="Preview")
        preview.get_by_text("Rows 1 to 50").wait_for(timeout=120_000)
        first_page = time.monotonic() - started
        connect_to_first_page = metadata + first_page

        later = []
        for number in range(2, 5):
            started = time.monotonic()
            preview.get_by_role("button", name="Next").click()
            first_row = (number - 1) * 50 + 1
            preview.get_by_text(f"Rows {first_row} to").wait_for(timeout=120_000)
            later.append(round(time.monotonic() - started, 3))

        profile = page.get_by_role("tabpanel", name="Profile")
        drop_from_cache(path)
        started = time.monotonic()
        profile.get_by_role("button", name="Fast profile").click()
        page.get_by_text("Profile status: Completed").wait_for(timeout=PROFILE_TIMEOUT_MS)
        fast = time.monotonic() - started

        # The fast profile already says Completed, so this waits for what only a full one shows.
        drop_from_cache(path)
        started = time.monotonic()
        profile.get_by_role("button", name="Full profile").click()
        profile.get_by_text(f"This file has {rows:,} rows.").wait_for(timeout=PROFILE_TIMEOUT_MS)
        full = time.monotonic() - started

        drop_from_cache(path)
        clicked = time.monotonic()
        profile.get_by_role("button", name="Full profile").click()
        cancel = profile.get_by_role("button", name="Cancel profile")
        cancel.wait_for(timeout=PROFILE_TIMEOUT_MS)
        time.sleep(max(0.0, CANCEL_AFTER - (time.monotonic() - clicked)))
        started = time.monotonic()
        cancel.click()
        profile.get_by_text("This profile was cancelled.").wait_for(timeout=PROFILE_TIMEOUT_MS)
        cancelled = time.monotonic() - started
        context.close()
    finally:
        drivnx.terminate()
        drivnx.wait(timeout=30)
    return {
        "encoding": encoding,
        "bytes": path.stat().st_size,
        "rows": rows,
        "generating_seconds": round(generated, 1),
        "metadata_seconds": round(metadata, 3),
        "first_page_seconds": round(first_page, 3),
        "connect_to_first_page_seconds": round(connect_to_first_page, 3),
        "next_page_seconds": later,
        "fast_profile_seconds": round(fast, 3),
        "full_profile_seconds": round(full, 3),
        "cancel_seconds": round(cancelled, 3),
        "within_budget": metadata <= BUDGET_METADATA
        and first_page <= BUDGET_FIRST_PAGE
        and fast <= BUDGET_FAST_PROFILE
        and full <= BUDGET_FULL_PROFILE
        and cancelled <= BUDGET_CANCEL,
    }


def main() -> None:
    directory = Path(sys.argv[1]).resolve()  # DrivnX refuses a relative path to a file
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("chromium"), args=["--no-sandbox"]
        )
        for encoding in ("utf-8", "latin-1"):
            print(json.dumps(measure(browser, directory, encoding)), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
