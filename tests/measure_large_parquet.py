"""Measures DRIVN-63's targets on a 1 GB synthetic Parquet file, through the real UI.

`uv run --frozen python tests/measure_large_parquet.py <empty directory>`, with the UI built
first (`pnpm --dir apps/web build`). The directory needs about 1 GB and should be on the disk
being measured. Everything is removed afterwards, also after a failure. Not a test: pytest
doesn't collect this, as the figures depend on the machine.

It times connecting, a fast profile, a full profile and a cancel 3 s into a full one (DRIVN-60
AC-07, AC-12, AC-13), each from the click until the page shows the result. The page polls a running
profile once a second, so a profile's time includes up to 1 s of waiting, as it does for the user.
"""

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

import duckdb
from measure_large_csv import drop_from_cache
from playwright.sync_api import sync_playwright

SIZE = 1_000_000_000
BUDGET_METADATA = 1.0
BUDGET_FAST_PROFILE = 5.0
BUDGET_FULL_PROFILE = 60.0
BUDGET_CANCEL = 5.0
CANCEL_AFTER = 3.0
PROFILE_TIMEOUT_MS = 600_000


def write_parquet(path: Path) -> int:
    """Five columns of invented values from a fixed seed, as DuckDB writes them by default, grown
    until the file is at least 1 GB. `name` is a hash, so it doesn't compress away."""
    rows = 10_000_000
    with duckdb.connect() as connection:
        while True:
            connection.execute("SELECT setseed(0.42)")
            connection.execute(
                f"""
                COPY (
                    SELECT
                        i AS id,
                        md5(i::VARCHAR) AS name,
                        ['Paris', 'Lyon', 'Orléans', 'Málaga', 'Nice', 'Metz'][1 + i % 6] AS city,
                        round(random() * 1000, 2) AS amount,
                        DATE '2024-01-01' + (i % 366)::INTEGER AS joined
                    FROM range({rows}) t(i)
                ) TO '{path}' (FORMAT parquet)
                """
            )
            if path.stat().st_size >= SIZE:
                break
            rows = int(rows * SIZE / path.stat().st_size * 1.02)
    drop_from_cache(path)
    return rows


def measure(browser, directory: Path) -> dict:
    path = directory / "large.parquet"
    workspace = directory / "workspace"
    try:
        return measure_file(browser, path, workspace)
    finally:
        path.unlink(missing_ok=True)
        shutil.rmtree(workspace, ignore_errors=True)


def measure_file(browser, path: Path, workspace: Path) -> dict:
    started = time.monotonic()
    rows = write_parquet(path)
    generated = time.monotonic() - started
    drivnx = subprocess.Popen(
        ["drivnx", "--no-browser", str(workspace)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        for line in drivnx.stdout:
            if "#launch=" in line:
                link = line[line.index("http://") :].split()[0]
                break
        else:
            raise RuntimeError("drivnx exited without printing a link.")
        context = browser.new_context()
        page = context.new_page()
        page.goto(link)
        page.get_by_role("textbox", name="Workspace name").fill("Measuring")
        page.get_by_role("button", name="Create workspace").click()
        page.get_by_role("textbox", name="CSV file path").fill(str(path))

        started = time.monotonic()
        page.get_by_role("button", name="Connect").click()
        page.get_by_text(f"{rows:,} rows", exact=True).wait_for(timeout=120_000)
        metadata = time.monotonic() - started

        profile = page.get_by_role("tabpanel", name="Profile")
        drop_from_cache(path)
        started = time.monotonic()
        profile.get_by_role("button", name="Fast profile").click()
        profile.get_by_text("about 100,000 rows, sampled by row group").first.wait_for(
            timeout=PROFILE_TIMEOUT_MS
        )
        fast = time.monotonic() - started

        drop_from_cache(path)
        started = time.monotonic()
        profile.get_by_role("button", name="Full profile").click()
        profile.get_by_text(f"every row, {rows:,} rows").first.wait_for(timeout=PROFILE_TIMEOUT_MS)
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
        "bytes": path.stat().st_size,
        "rows": rows,
        "generating_seconds": round(generated, 1),
        "metadata_seconds": round(metadata, 3),
        "fast_profile_seconds": round(fast, 3),
        "full_profile_seconds": round(full, 3),
        "cancel_seconds": round(cancelled, 3),
        "within_budget": metadata <= BUDGET_METADATA
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
        print(json.dumps(measure(browser, directory)), flush=True)
        browser.close()


if __name__ == "__main__":
    main()
