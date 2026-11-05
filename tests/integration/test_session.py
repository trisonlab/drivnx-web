"""Session protection, CFX-APP-first-slice/AC-06 and AC-08 to AC-13."""

import os
import re
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest
from artifacts.workspace import open_workspace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from local_api.app import create_app
from local_api.session import LAUNCH_CODE_LIFETIME_SECONDS, LaunchSession

from ..conftest import REPO_ROOT, Launch, StartConflux, posix_only


@pytest.mark.parametrize(
    "path", ["/api", "/api/", "/api/workspace", "/api/session/extra"]
)
def test_api_without_a_session_cookie_is_refused(
    start_conflux: StartConflux, path: str
) -> None:
    # CFX-APP-first-slice/AC-08
    origin = start_conflux("--no-browser").wait_for_launch().origin

    assert httpx.get(origin + path).status_code == 401


def sign_in(launch: Launch) -> httpx.Client:
    client = httpx.Client(base_url=launch.origin, headers={"Origin": launch.origin})
    assert client.post("/api/session", json={"code": launch.code}).status_code == 204
    return client


def test_launch_code_signs_in(start_conflux: StartConflux) -> None:
    # CFX-APP-first-slice/AC-08: a valid cookie gets past the check, to a 404 for an unknown route.
    client = sign_in(start_conflux("--no-browser").wait_for_launch())

    assert client.get("/api/no-such-route").status_code == 404


def test_a_wrong_session_cookie_is_refused(start_conflux: StartConflux) -> None:
    # CFX-APP-first-slice/AC-08
    launch = start_conflux("--no-browser").wait_for_launch()
    cookie_name = f"conflux_session_{launch.port}"

    response = httpx.get(
        launch.origin + "/api/workspace", cookies={cookie_name: "wrong"}
    )

    assert response.status_code == 401


def test_a_launch_code_works_only_once(start_conflux: StartConflux) -> None:
    # CFX-APP-first-slice/AC-10
    launch = start_conflux("--no-browser").wait_for_launch()
    sign_in(launch)

    second = httpx.post(
        launch.origin + "/api/session",
        json={"code": launch.code},
        headers={"Origin": launch.origin},
    )

    assert second.status_code == 401
    assert "set-cookie" not in second.headers


def test_a_wrong_launch_code_is_refused_and_the_right_one_still_works(
    start_conflux: StartConflux,
) -> None:
    # CFX-APP-first-slice/AC-10: guessing can't burn the real code and lock the user out.
    launch = start_conflux("--no-browser").wait_for_launch()

    wrong = httpx.post(
        launch.origin + "/api/session",
        json={"code": "wrong"},
        headers={"Origin": launch.origin},
    )

    assert wrong.status_code == 401
    sign_in(launch)


@pytest.mark.parametrize("path", ["/api/session", "/api/workspace"])
@pytest.mark.parametrize(
    "host", ["evil.example", "localhost:{port}", "127.0.0.1", "127.0.0.1:1"]
)
def test_a_wrong_host_is_refused(
    start_conflux: StartConflux, path: str, host: str
) -> None:
    # CFX-APP-first-slice/AC-09, including /api/session
    launch = start_conflux("--no-browser").wait_for_launch()

    response = httpx.get(
        launch.origin + path, headers={"Host": host.format(port=launch.port)}
    )

    assert response.status_code == 403


@pytest.mark.parametrize("path", ["/api/session", "/api/workspace"])
@pytest.mark.parametrize(
    "origin",
    [
        "http://evil.example",
        "http://127.0.0.1:1",
        "http://localhost:{port}",
        "https://127.0.0.1:{port}",
        "http://127.0.0.1:{port}/",
        "null",
    ],
)
def test_a_wrong_origin_is_refused(
    start_conflux: StartConflux, path: str, origin: str
) -> None:
    # CFX-APP-first-slice/AC-09, including /api/session and a page on another local port
    launch = start_conflux("--no-browser").wait_for_launch()
    wrong = origin.format(port=launch.port)

    get = httpx.get(launch.origin + path, headers={"Origin": wrong})
    post = httpx.post(
        launch.origin + path, json={"code": launch.code}, headers={"Origin": wrong}
    )

    assert (get.status_code, post.status_code) == (403, 403)
    # The refused POST must not have burned the code.
    sign_in(launch)


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_a_state_changing_request_without_origin_is_refused(
    start_conflux: StartConflux, method: str
) -> None:
    # CFX-APP-first-slice/AC-09
    launch = start_conflux("--no-browser").wait_for_launch()

    response = httpx.request(
        method, launch.origin + "/api/session", json={"code": launch.code}
    )

    assert response.status_code == 403
    sign_in(launch)


def test_a_signed_in_get_without_origin_is_allowed(start_conflux: StartConflux) -> None:
    # CFX-APP-first-slice/AC-09: browsers don't send Origin on same-origin GET requests.
    launch = start_conflux("--no-browser").wait_for_launch()
    client = sign_in(launch)
    del client.headers["Origin"]

    assert client.get("/api/session").json() == {"authenticated": True}


PORT = 8123
ORIGIN = f"http://127.0.0.1:{PORT}"


def in_process_client(session: LaunchSession, tmp_path: Path) -> TestClient:
    (tmp_path / "index.html").write_text("<html></html>")
    workspace = open_workspace(tmp_path / "workspace")
    return TestClient(
        create_app(session, PORT, workspace, tmp_path),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )


def api_reads(client: TestClient) -> list[tuple[str, str]]:
    """Read from the OpenAPI document, because app.routes hides routes on an included router."""
    assert isinstance(client.app, FastAPI)
    return [
        (method.upper(), path)
        for path, operations in client.app.openapi()["paths"].items()
        if path.startswith("/api")
        for method in sorted(operations)
        if method in ("get", "head")
    ]


@pytest.mark.parametrize(
    ("seconds_later", "status"),
    [
        (0, 204),
        (LAUNCH_CODE_LIFETIME_SECONDS - 1, 204),
        (LAUNCH_CODE_LIFETIME_SECONDS, 401),
        (LAUNCH_CODE_LIFETIME_SECONDS + 1, 401),
    ],
)
def test_a_launch_code_expires_after_two_minutes(
    tmp_path: Path, seconds_later: int, status: int
) -> None:
    # CFX-APP-first-slice/AC-10: the spec's 2 minutes, written out as 120 on purpose.
    assert LAUNCH_CODE_LIFETIME_SECONDS == 120
    clock = [1000.0]
    session = LaunchSession(PORT, now=lambda: clock[0])
    client = in_process_client(session, tmp_path)

    clock[0] += seconds_later
    response = client.post("/api/session", json={"code": session.launch_code})

    assert response.status_code == status


def test_the_session_cookie_has_the_required_attributes(
    start_conflux: StartConflux,
) -> None:
    # CFX-APP-first-slice/AC-11
    launch = start_conflux("--no-browser").wait_for_launch()

    response = httpx.post(
        launch.origin + "/api/session",
        json={"code": launch.code},
        headers={"Origin": launch.origin},
    )

    parts = [part.strip() for part in response.headers["set-cookie"].split(";")]
    name, _, token = parts[0].partition("=")
    assert name == f"conflux_session_{launch.port}"
    assert {part.lower() for part in parts[1:]} >= {
        "httponly",
        "samesite=strict",
        "path=/api",
    }
    assert token
    assert token not in response.text
    assert token not in str(response.url)


def test_get_and_head_routes_change_nothing(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-13: the launch code survives every read.
    session = LaunchSession(PORT)
    client = in_process_client(session, tmp_path)
    reads = api_reads(client)
    assert reads

    for method, path in reads:
        response = client.request(method, path)
        assert response.status_code < 500
        assert "set-cookie" not in response.headers

    assert (
        client.post("/api/session", json={"code": session.launch_code}).status_code
        == 204
    )


def test_get_and_head_routes_change_no_workspace_data(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-13
    session = LaunchSession(PORT)
    client = in_process_client(session, tmp_path)
    client.post("/api/session", json={"code": session.launch_code})
    assert (
        client.put(
            "/api/workspace", json={"name": "Snapshot", "ai_mode": "off"}
        ).status_code
        == 200
    )
    csv_path = REPO_ROOT / "testdata" / "csv" / "comma_utf8.csv"
    connected = client.post("/api/sources", json={"path": str(csv_path)})
    assert connected.status_code == 201
    source_id = connected.json()["id"]
    run_id = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast"}
    ).json()["id"]
    deadline = time.monotonic() + 30
    while client.get(f"/api/profile-runs/{run_id}").json()["status"] == "running":
        assert time.monotonic() < deadline
        time.sleep(0.01)

    def snapshot() -> list[str]:
        connection = sqlite3.connect(tmp_path / "workspace" / "workspace.db")
        try:
            return list(connection.iterdump())
        finally:
            connection.close()

    before = snapshot()
    assert any("comma_utf8.csv" in line for line in before)
    assert ("GET", "/api/workspace") in api_reads(client)
    assert ("GET", "/api/sources") in api_reads(client)
    assert ("GET", "/api/sources/{source_id}/rows") in api_reads(client)
    assert ("GET", "/api/profile-runs") in api_reads(client)
    assert ("GET", "/api/profile-runs/{run_id}") in api_reads(client)

    for method, path in api_reads(client):
        # A literal {source_id} would only ever reach the 404, and read nothing.
        response = client.request(
            method, path.format(source_id=source_id, run_id=run_id)
        )
        assert response.status_code == 200

    assert snapshot() == before


def test_the_session_token_and_launch_code_never_reach_the_workspace(
    tmp_path: Path,
) -> None:
    # CFX-APP-first-slice/AC-48: random values, so no pattern in the end-of-run scan could spot them.
    session = LaunchSession(PORT)
    client = in_process_client(session, tmp_path)
    code = session.launch_code
    client.post("/api/session", json={"code": code})
    token = next(iter(client.cookies.values()))
    saved = client.put("/api/workspace", json={"name": "Snapshot", "ai_mode": "off"})

    stored = [
        path.read_bytes()
        for path in (tmp_path / "workspace").rglob("workspace.db*")
        if path.is_file()
    ]

    assert saved.status_code == 200, "nothing was written, so this proves nothing"
    assert stored, "no workspace files were found, so this proves nothing"
    assert any(b"Snapshot" in data for data in stored)
    assert token
    for data in stored:
        assert token.encode() not in data
        assert code.encode() not in data


@posix_only
def test_stopping_ends_the_session(start_conflux: StartConflux, free_port: int) -> None:
    # CFX-APP-first-slice/AC-06
    first = start_conflux("--no-browser", "--port", str(free_port))
    client = sign_in(first.wait_for_launch())
    assert client.get("/api/session").json() == {"authenticated": True}

    first.process.send_signal(signal.SIGINT)
    assert first.process.wait(timeout=10) == 0
    assert "Traceback" not in first.all_output()

    second = start_conflux("--no-browser", "--port", str(free_port)).wait_for_launch()
    assert second.port == free_port
    assert client.get("/api/session").json() == {"authenticated": False}
    assert client.get("/api/workspace").status_code == 401


@posix_only
def test_logs_contain_no_token_or_launch_code(
    start_conflux: StartConflux, free_port: int, tmp_path: Path
) -> None:
    # CFX-APP-first-slice/AC-12: the code goes to a browser here, so printing it isn't what is tested.
    # The unreadable row is there because DuckDB's error message quotes the row.
    opened = tmp_path / "opened"
    browser = tmp_path / "browser"
    browser.write_text(f'#!/bin/sh\nprintf %s "$1" > "{opened}"\n')
    browser.chmod(0o755)
    conflux = start_conflux(
        "--port", str(free_port), "--log-level", "trace", browser=f"{browser} %s"
    )
    origin = f"http://127.0.0.1:{free_port}"
    for _ in range(200):
        if opened.exists() and opened.read_text():
            break
        time.sleep(0.05)
    code = opened.read_text().partition("#launch=")[2]
    assert code

    response = httpx.post(
        origin + "/api/session", json={"code": code}, headers={"Origin": origin}
    )
    match = re.search(r"=([^;]+);", response.headers["set-cookie"])
    assert match
    token = match.group(1)
    private = tmp_path / "private.csv"
    private.write_text(
        "id,note\n1,private-row-value\n"
        + "2,more\n" * 100_000
        + "x,private-bad-row,y\n"
    )
    with httpx.Client(
        base_url=origin, cookies=response.cookies, headers={"Origin": origin}
    ) as client:
        assert client.get("/api/session").json() == {"authenticated": True}
        client.get("/api/workspace")
        client.put("/api/workspace", json={"name": "Logs", "ai_mode": "off"})
        source = client.post("/api/sources", json={"path": str(private)}).json()["id"]
        rows = client.get(f"/api/sources/{source}/rows")
        assert "private-row-value" in rows.text
        unreadable = client.get(
            f"/api/sources/{source}/rows", params={"offset": 99_990}
        )
        assert unreadable.status_code == 409
    httpx.post(origin + "/api/session", json={"code": code}, headers={"Origin": origin})

    conflux.process.terminate()
    conflux.process.wait(timeout=10)
    output = conflux.all_output()
    assert "/api/session" in output, "trace logging produced no request lines"
    assert code not in output
    assert token not in output
    assert "/rows" in output, "trace logging produced no preview request lines"
    assert "private-row-value" not in output
    assert "private-bad-row" not in output


VITE_ORIGIN = "http://127.0.0.1:5173"


def test_the_dev_origin_is_refused_without_the_dev_flag(
    start_conflux: StartConflux,
) -> None:
    # CFX-APP-first-slice/AC-52
    launch = start_conflux("--no-browser").wait_for_launch()

    response = httpx.get(
        launch.origin + "/api/session", headers={"Origin": VITE_ORIGIN}
    )

    assert response.status_code == 403


def test_the_dev_flag_allows_only_that_origin_and_links_to_it(
    start_conflux: StartConflux, free_port: int
) -> None:
    # CFX-APP-first-slice/AC-52
    conflux = start_conflux(
        "--no-browser", "--port", str(free_port), "--dev-origin", VITE_ORIGIN
    )

    url = conflux.wait_for_url().group(0)

    origin = f"http://127.0.0.1:{free_port}"
    assert url.startswith(VITE_ORIGIN + "/#launch=")
    headers = {"Origin": VITE_ORIGIN}
    assert httpx.get(origin + "/api/session", headers=headers).status_code == 200
    other = {"Origin": "http://127.0.0.1:5174"}
    assert httpx.get(origin + "/api/session", headers=other).status_code == 403


@pytest.mark.parametrize(
    "origin", ["http://evil.example:5173", "https://127.0.0.1:5173", "127.0.0.1:5173"]
)
def test_the_dev_flag_refuses_anything_but_a_loopback_origin(
    start_conflux: StartConflux, origin: str
) -> None:
    # CFX-APP-first-slice/AC-52
    conflux = start_conflux("--no-browser", f"--dev-origin={origin}")

    assert conflux.process.wait(timeout=10) == 2
    assert "--dev-origin" in conflux.all_output()


@posix_only
def test_sign_in_works_through_the_vite_proxy(
    start_conflux: StartConflux, free_port: int, unused_port: int
) -> None:
    # CFX-APP-first-slice/AC-52: the real dev server, so a Host check it breaks would show here.
    vite_origin = f"http://127.0.0.1:{unused_port}"
    code = (
        start_conflux(
            "--no-browser", "--port", str(free_port), "--dev-origin", vite_origin
        )
        .wait_for_launch()
        .code
    )
    vite = subprocess.Popen(
        ["pnpm", "exec", "vite", "--port", str(unused_port)],
        cwd=REPO_ROOT / "apps/web",
        env={**os.environ, "API_PORT": str(free_port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        with httpx.Client(
            base_url=vite_origin, headers={"Origin": vite_origin}
        ) as client:
            for _ in range(100):
                try:
                    client.get("/")
                    break
                except httpx.TransportError:
                    time.sleep(0.1)

            response = client.post("/api/session", json={"code": code})

            assert response.status_code == 204
            assert client.get("/api/session").json() == {"authenticated": True}
    finally:
        os.killpg(vite.pid, signal.SIGTERM)
        vite.wait(timeout=10)


def test_an_unencodable_launch_code_is_refused_without_a_crash(
    start_conflux: StartConflux,
) -> None:
    # CFX-APP-first-slice/AC-10: found in the T02 review; it returned 500 and logged a traceback.
    launch = start_conflux("--no-browser").wait_for_launch()

    response = httpx.post(
        launch.origin + "/api/session",
        content=b'{"code":"\\ud800"}',
        headers={"Origin": launch.origin, "Content-Type": "application/json"},
    )

    assert response.status_code == 401
    sign_in(launch)


@pytest.mark.parametrize(
    "path", ["/", "/assets/missing.js", "/api/session", "/api/workspace"]
)
def test_every_response_refuses_framing_and_outside_content(
    start_conflux: StartConflux, path: str
) -> None:
    # Found in the T02 review: another local page could frame the signed-in UI, and other local
    # ports count as the same site, so SameSite doesn't help.
    origin = start_conflux("--no-browser").wait_for_launch().origin

    headers = httpx.get(origin + path).headers

    policy = headers["content-security-policy"]
    assert "frame-ancestors 'none'" in policy
    assert "default-src 'self'" in policy
    assert headers["x-content-type-options"] == "nosniff"


def test_a_host_refusal_also_carries_the_security_headers(
    start_conflux: StartConflux,
) -> None:
    origin = start_conflux("--no-browser").wait_for_launch().origin

    response = httpx.get(origin + "/api/session", headers={"Host": "evil.example"})

    assert response.status_code == 403
    assert "content-security-policy" in response.headers
