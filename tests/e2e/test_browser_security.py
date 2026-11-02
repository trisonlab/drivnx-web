"""Spike 6 as repeatable checks: what a real browser does, which API tests can't show.

CFX-APP-first-slice AC-09 and AC-11. Accepted risk R1 stays documented in the threat register.
"""

import http.server
import threading
from collections.abc import Iterator

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from ..conftest import Launch, StartConflux

FETCH = """async ([url, options]) => {
  try {
    const response = await fetch(url, options);
    return { type: response.type, status: response.status };
  } catch (error) {
    return { error: String(error) };
  }
}"""


class _OtherLocalSite(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html>another local site</html>")

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def other_local_site() -> Iterator[str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _OtherLocalSite)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture
def launch(start_conflux: StartConflux) -> Launch:
    return start_conflux("--no-browser").wait_for_launch()


@pytest.fixture
def signed_in(browser: Browser, launch: Launch) -> Iterator[BrowserContext]:
    context = browser.new_context()
    page = context.new_page()
    page.goto(launch.url)
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    page.close()
    yield context
    context.close()


def test_the_code_leaves_the_address_bar_and_the_token_is_out_of_reach(
    page: Page, launch: Launch
) -> None:
    # CFX-APP-first-slice/AC-11
    page.goto(launch.url)
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()

    assert "launch" not in page.url
    cookies = [
        c for c in page.context.cookies() if c["name"].startswith("conflux_session_")
    ]
    assert len(cookies) == 1
    assert cookies[0]["httpOnly"] is True
    assert cookies[0]["sameSite"] == "Strict"
    assert cookies[0]["path"] == "/api"
    assert page.evaluate("document.cookie") == ""
    assert page.evaluate("localStorage.length + sessionStorage.length") == 0


def test_a_reload_and_a_new_tab_stay_signed_in(
    signed_in: BrowserContext, launch: Launch
) -> None:
    page = signed_in.new_page()
    page.goto(launch.origin + "/")
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()
    page.reload()
    expect(page.get_by_role("textbox", name="Workspace name")).to_be_visible()


def test_a_second_browser_with_a_used_code_is_refused_and_told_what_to_do(
    browser: Browser, signed_in: BrowserContext, launch: Launch
) -> None:
    # CFX-APP-first-slice/AC-10
    other = browser.new_context()
    page = other.new_page()

    page.goto(launch.url)

    expect(page.get_by_role("alert")).to_contain_text("Start Conflux again")
    other.close()


def test_another_local_page_cannot_use_the_api_with_fetch(
    signed_in: BrowserContext, launch: Launch, other_local_site: str
) -> None:
    # CFX-APP-first-slice/AC-09: SameSite treats other local ports as the same site.
    page = signed_in.new_page()
    page.goto(other_local_site)

    read = page.evaluate(
        FETCH, [launch.origin + "/api/workspace", {"credentials": "include"}]
    )
    write = page.evaluate(
        FETCH,
        [
            launch.origin + "/api/workspace",
            {
                "method": "PUT",
                "credentials": "include",
                "headers": {"Content-Type": "application/json"},
                "body": '{"name": "hijacked", "ai_mode": "external"}',
            },
        ],
    )
    blind = page.evaluate(
        FETCH,
        [
            launch.origin + "/api/session",
            {
                "method": "POST",
                "mode": "no-cors",
                "credentials": "include",
                "body": "{}",
            },
        ],
    )

    assert "error" in read
    assert "error" in write
    # A blind request gets through the browser, but its response is hidden and the guard refuses it.
    assert blind == {"type": "opaque", "status": 0}
    check = signed_in.new_page()
    check.goto(launch.origin + "/")
    expect(check.get_by_role("textbox", name="Workspace name")).to_be_visible()


def test_a_form_posted_from_another_local_page_is_refused(
    signed_in: BrowserContext, launch: Launch, other_local_site: str
) -> None:
    # CFX-APP-first-slice/AC-09: a form can't be stopped by the browser, so the Origin check must.
    page = signed_in.new_page()
    page.goto(other_local_site)
    page.set_content(
        f'<form id="f" method="post" action="{launch.origin}/api/session"><input name="x"></form>'
    )

    with page.expect_response(launch.origin + "/api/session") as response:
        page.evaluate("document.getElementById('f').submit()")

    assert response.value.status == 403


def test_another_local_page_cannot_frame_the_signed_in_app(
    signed_in: BrowserContext, launch: Launch, other_local_site: str
) -> None:
    # Found in the T02 review.
    page = signed_in.new_page()
    page.goto(other_local_site)

    page.set_content(
        f'<iframe src="{launch.origin}/" width="600" height="300"></iframe>'
    )
    page.wait_for_timeout(800)

    frames = [frame for frame in page.frames if frame is not page.main_frame]
    assert len(frames) == 1
    assert not frames[0].url.startswith(launch.origin)
