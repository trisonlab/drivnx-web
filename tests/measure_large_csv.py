"""Measures the spec's performance targets on a 1 GB synthetic CSV, through the real UI.

`uv run --frozen python tests/measure_large_csv.py <empty directory>`. The directory needs about
1 GB for each encoding, one at a time, and should be on the disk being measured, as a RAM disk
flatters the figures. Everything is removed afterwards, also after a failure. Not a test: pytest doesn't collect this, as the figures depend on the machine.
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
        # Out of the page cache, so reading it really touches the disk. Linux and macOS differ here.
        if hasattr(os, "posix_fadvise"):
            os.posix_fadvise(file.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
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
    conflux = subprocess.Popen(
        ["conflux", "--no-browser", str(workspace)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for line in conflux.stdout:
            if found := re.search(r"http://127\.0\.0\.1:\d+/#launch=\S+", line):
                break
        else:
            raise RuntimeError("conflux exited without printing a link.")
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
        context.close()
    finally:
        conflux.terminate()
        conflux.wait(timeout=30)
    return {
        "encoding": encoding,
        "bytes": path.stat().st_size,
        "rows": rows,
        "generating_seconds": round(generated, 1),
        "metadata_seconds": round(metadata, 3),
        "first_page_seconds": round(first_page, 3),
        "connect_to_first_page_seconds": round(connect_to_first_page, 3),
        "next_page_seconds": later,
        "within_budget": metadata <= BUDGET_METADATA
        and first_page <= BUDGET_FIRST_PAGE,
    }


def main() -> None:
    directory = Path(sys.argv[1])
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            executable_path=shutil.which("chromium"), args=["--no-sandbox"]
        )
        for encoding in ("utf-8", "latin-1"):
            print(json.dumps(measure(browser, directory, encoding)), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
