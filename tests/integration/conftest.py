from collections.abc import Iterator
from pathlib import Path

import pytest
from artifacts.workspace import open_workspace
from fastapi.testclient import TestClient
from local_api.app import create_app
from local_api.session import LaunchSession

PORT = 8123
ORIGIN = f"http://127.0.0.1:{PORT}"


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def client(tmp_path: Path, workspace_dir: Path) -> Iterator[TestClient]:
    """A signed-in client for the real app on a real workspace, without a server process."""
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html></html>")
    workspace = open_workspace(workspace_dir)
    session = LaunchSession(PORT)
    client = TestClient(
        create_app(session, PORT, workspace, ui_dir),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
    )
    client.post("/api/session", json={"code": session.launch_code})
    yield client
    workspace.close()
