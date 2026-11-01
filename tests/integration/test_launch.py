"""CFX-APP-first-slice/AC-05: how Conflux starts, and where the launch URL goes."""

import socket
import time
from pathlib import Path

import httpx
import pytest

from .conftest import StartConflux


def test_no_browser_prints_the_url_and_serves_the_ui_bundle(
    start_conflux: StartConflux,
) -> None:
    url = start_conflux("--no-browser").wait_for_url().group(0)

    page = httpx.get(url)

    assert page.status_code == 200
    assert '<div id="root">' in page.text


def test_port_option_listens_on_that_port(
    start_conflux: StartConflux, free_port: int
) -> None:
    url = start_conflux("--no-browser", "--port", str(free_port)).wait_for_url()

    assert int(url.group(1)) == free_port
    assert httpx.get(url.group(0)).status_code == 200


def test_without_a_port_the_os_picks_a_free_one(start_conflux: StartConflux) -> None:
    first = start_conflux("--no-browser").wait_for_url()
    second = start_conflux("--no-browser").wait_for_url()

    assert first.group(1) != second.group(1)
    assert httpx.get(second.group(0)).status_code == 200


def test_prints_the_url_when_the_browser_fails_to_open(
    start_conflux: StartConflux,
) -> None:
    url = start_conflux(browser="false %s").wait_for_url().group(0)

    assert httpx.get(url).status_code == 200


def test_opens_the_browser_and_keeps_the_url_out_of_the_terminal(
    start_conflux: StartConflux, free_port: int, tmp_path: Path
) -> None:
    opened = tmp_path / "opened"
    browser = tmp_path / "browser"
    browser.write_text(
        f'#!/bin/sh\nprintf %s "$1" > "{opened}.tmp"\nmv "{opened}.tmp" "{opened}"\n'
    )
    browser.chmod(0o755)

    conflux = start_conflux("--port", str(free_port), browser=f"{browser} %s")

    deadline = time.monotonic() + 10
    while not opened.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    url = f"http://127.0.0.1:{free_port}/"
    assert opened.read_text() == url
    assert httpx.get(url).status_code == 200
    conflux.process.terminate()
    conflux.process.wait(timeout=10)
    assert url not in conflux.all_output()


def test_restarting_on_the_same_port_works_straight_away(
    start_conflux: StartConflux, free_port: int
) -> None:
    first = start_conflux("--no-browser", "--port", str(free_port))
    url = first.wait_for_url().group(0)
    # A browser keeps its connection open, so the server closes it first, which leaves TIME_WAIT.
    with httpx.Client() as browser_like:
        browser_like.get(url)
        first.process.terminate()
        first.process.wait(timeout=10)

    second = start_conflux("--no-browser", "--port", str(free_port))

    assert httpx.get(second.wait_for_url().group(0)).status_code == 200


def test_a_port_already_in_use_gives_a_clear_error(
    start_conflux: StartConflux, free_port: int
) -> None:
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", free_port))
        taken.listen()

        conflux = start_conflux("--no-browser", "--port", str(free_port))

        assert conflux.process.wait(timeout=10) != 0
        output = conflux.all_output()
    assert f"Port {free_port} is not available" in output
    assert "Traceback" not in output


@pytest.mark.parametrize("port", ["70000", "-1", "abc"])
def test_an_invalid_port_gives_a_clear_error(
    start_conflux: StartConflux, port: str
) -> None:
    conflux = start_conflux("--no-browser", f"--port={port}")

    assert conflux.process.wait(timeout=10) == 2
    output = conflux.all_output()
    assert "--port" in output
    assert "Traceback" not in output


def test_a_missing_ui_bundle_gives_a_clear_error(
    start_conflux: StartConflux, conflux_executable: str
) -> None:
    environment = Path(conflux_executable).parents[1]
    bundle = next(environment.rglob("local_api/ui"))
    hidden = bundle.with_name("ui-hidden")
    bundle.rename(hidden)
    try:
        conflux = start_conflux("--no-browser")

        assert conflux.process.wait(timeout=10) == 1
        output = conflux.all_output()
    finally:
        hidden.rename(bundle)
    assert "UI bundle is missing" in output
    assert "Traceback" not in output
