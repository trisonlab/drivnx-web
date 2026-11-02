from pathlib import Path

from artifacts.workspace import open_workspace
from fastapi.testclient import TestClient
from local_api.app import create_app
from local_api.session import LaunchSession


def test_generated_api_docs_are_off(tmp_path: Path) -> None:
    # They load scripts from a CDN, and nothing may leave the machine.
    (tmp_path / "index.html").write_text("<html></html>")
    workspace = open_workspace(tmp_path / "workspace")
    client = TestClient(create_app(LaunchSession(port=1), 1, workspace, tmp_path))

    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404
