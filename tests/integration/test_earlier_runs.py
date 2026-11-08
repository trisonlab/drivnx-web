"""DRIVN-31: earlier runs, and knowing when results are out of date."""

import os
import time
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ..conftest import StartDrivnX
from .test_fast_profile import WAIT_FOR_A_RUN_SECONDS, connect, finished, settings_of, start
from .test_full_profile import large_csv, meeting_every_kind
from .test_session import sign_in


def run_detail(client: TestClient, run: dict) -> dict:
    response = client.get(f"/api/profile-runs/{run['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_a_run_on_an_unchanged_file_is_not_out_of_date(client: TestClient, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-37
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path)))

    assert run_detail(client, run)["file_changed"] is False


def test_a_file_whose_size_changed_since_the_run_is_said_to_have_changed(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-37
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path)))
    stat = path.stat()

    path.write_text("id,name\n1,Ann\n2,Bob\n")
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))

    assert run_detail(client, run)["file_changed"] is True


def test_a_file_modified_since_the_run_is_said_to_have_changed(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-37: the same size, written again later.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path)))
    stat = path.stat()

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert run_detail(client, run)["file_changed"] is True


def test_a_file_that_is_gone_says_nothing_about_being_out_of_date(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-37: there's nothing to compare, and the source already says it's gone.
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    run = finished(client, start(client, connect(client, path)))

    path.unlink()

    assert run_detail(client, run)["file_changed"] is None


def test_every_run_of_a_source_is_listed_and_any_completed_one_opens(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-35
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n")
    source = connect(client, path)
    first = finished(client, start(client, source))
    path.write_text("id,name\n1,Ann\n2,Bob\n")
    second = finished(client, start(client, source, "full"))

    listed = client.get("/api/profile-runs").json()

    assert [(run["id"], run["mode"], run["status"]) for run in listed] == [
        (second["id"], "full", "completed"),
        (first["id"], "fast", "completed"),
    ]
    assert all(run["started_at"] and run["ended_at"] for run in listed)
    rows_read = {
        metric["value"]
        for metric in run_detail(client, first)["metrics"]
        if (metric["column"], metric["metric"]) == (None, "rows_read")
    }
    assert rows_read == {1}


def test_a_run_made_before_a_correction_still_opens_on_its_earlier_settings(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-36
    path = tmp_path / "people.csv"
    path.write_text("id,name\n1,Ann\n2,Bob\n")
    source = connect(client, path)
    run = finished(client, start(client, source))
    response = client.put(
        f"/api/sources/{source['id']}/settings", json=settings_of(source, has_header=False)
    )
    assert response.status_code == 200, response.text

    earlier = run_detail(client, run)

    assert (earlier["status"], earlier["current_settings"]) == ("completed", False)
    assert earlier["metrics"]


def ended(client: httpx.Client, run: dict) -> dict:
    deadline = time.monotonic() + WAIT_FOR_A_RUN_SECONDS
    while time.monotonic() < deadline:
        current = client.get(f"/api/profile-runs/{run['id']}").json()
        if current["status"] != "running":
            return current
        time.sleep(0.05)
    raise AssertionError("The run didn't end.")


def test_a_restart_keeps_completed_runs_and_marks_one_in_progress_interrupted(
    start_drivnx: StartDrivnX, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-38, through the real command and a stop during a run.
    directory = tmp_path / "workspace"
    before = start_drivnx("--no-browser", workspace=directory)
    client = sign_in(before.wait_for_launch())
    every_kind = client.post(
        "/api/sources", json={"path": str(meeting_every_kind(tmp_path / "every_kind.csv"))}
    ).json()
    completed = ended(
        client,
        client.post(
            "/api/profile-runs", json={"source_id": every_kind["id"], "mode": "full"}
        ).json(),
    )
    assert completed["status"] == "completed"
    findings = client.get("/api/findings", params={"profile_run_id": completed["id"]}).json()
    assert findings
    large = client.post(
        "/api/sources", json={"path": str(large_csv(tmp_path / "large.csv"))}
    ).json()
    in_progress = client.post(
        "/api/profile-runs", json={"source_id": large["id"], "mode": "full"}
    ).json()
    assert in_progress["status"] == "running"

    before.process.terminate()
    before.process.wait(timeout=30)
    after = sign_in(start_drivnx("--no-browser", workspace=directory).wait_for_launch())

    listed = after.get("/api/profile-runs").json()
    assert [(run["id"], run["status"]) for run in listed] == [
        (in_progress["id"], "interrupted"),
        (completed["id"], "completed"),
    ]
    assert after.get(f"/api/profile-runs/{completed['id']}").json() == completed
    assert (
        after.get("/api/findings", params={"profile_run_id": completed["id"]}).json() == findings
    )
