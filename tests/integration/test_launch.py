"""CFX-APP-first-slice/AC-05: how Conflux starts, and where the launch URL goes."""

import socket
import time
from pathlib import Path

import httpx
import pytest

from ..conftest import StartConflux, linux_only, posix_only


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


@linux_only
def test_prints_the_url_when_the_browser_fails_to_open(
    start_conflux: StartConflux,
) -> None:
    url = start_conflux(browser="false %s").wait_for_url().group(0)

    assert httpx.get(url).status_code == 200


@posix_only
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
    url = opened.read_text()
    assert url.startswith(f"http://127.0.0.1:{free_port}/#launch=")
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
    start_conflux: StartConflux, installed_environment: Path
) -> None:
    bundle = next(installed_environment.rglob("local_api/ui"))
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


def _browser_stand_in(directory: Path, name: str, opened: Path) -> None:
    directory.mkdir()
    script = directory / name
    script.write_text(f'#!/bin/sh\nprintf %s "$1" > "{opened}"\n')
    script.chmod(0o755)


@linux_only
@pytest.mark.parametrize(
    "empty_setting", [{}, {"DISPLAY": ""}, {"WAYLAND_DISPLAY": ""}, {"BROWSER": ""}]
)
def test_without_a_display_the_url_is_printed_and_no_browser_is_tried(
    start_conflux: StartConflux, tmp_path: Path, empty_setting: dict[str, str]
) -> None:
    # A text browser such as www-browser can't run the app, and would hide the URL (T01 review).
    opened = tmp_path / "opened"
    _browser_stand_in(tmp_path / "bin", "www-browser", opened)

    conflux = start_conflux(
        env={"TERM": "xterm", "PATH": str(tmp_path / "bin"), **empty_setting}
    )

    assert "#launch=" in conflux.wait_for_url().group(0)
    assert not opened.exists()


@linux_only
def test_with_a_display_the_browser_is_opened(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    opened = tmp_path / "opened"
    _browser_stand_in(tmp_path / "bin", "xdg-open", opened)

    start_conflux(env={"DISPLAY": ":99", "PATH": str(tmp_path / "bin")})

    deadline = time.monotonic() + 10
    while not opened.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert "#launch=" in opened.read_text()


@posix_only
def test_the_terminal_says_how_to_stop_and_what_to_do_if_no_browser_opened(
    start_conflux: StartConflux, tmp_path: Path
) -> None:
    opened = tmp_path / "opened"
    browser = tmp_path / "browser"
    browser.write_text(f'#!/bin/sh\nprintf %s "$1" > "{opened}"\n')
    browser.chmod(0o755)

    conflux = start_conflux(browser=f"{browser} %s")

    deadline = time.monotonic() + 10
    while not opened.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    code = opened.read_text().partition("#launch=")[2]
    conflux.process.terminate()
    conflux.process.wait(timeout=10)
    output = conflux.all_output()
    assert "Press Ctrl+C to stop" in output
    assert "--no-browser" in output
    assert code
    assert code not in output


def test_with_no_browser_the_terminal_gives_the_link_and_how_to_stop(
    start_conflux: StartConflux,
) -> None:
    conflux = start_conflux("--no-browser")

    assert "#launch=" in conflux.wait_for_url().group(0)
    conflux.process.terminate()
    conflux.process.wait(timeout=10)
    assert "Press Ctrl+C to stop" in conflux.all_output()
