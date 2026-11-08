"""`GET /api/profile-runs/{run_id}/values`, CFX-APP-csv-profile AC-29 to AC-32."""

import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from local_api import profile_jobs
from profiling.first_rows import FIRST_ROWS

from ..conftest import posix_only
from .test_fast_profile import WAIT_FOR_A_RUN_SECONDS, connect, finished, start


def values_of(client: TestClient, run: dict, column: str, **params: str):
    return client.get(f"/api/profile-runs/{run['id']}/values", params={"column": column, **params})


def shown(client: TestClient, run: dict, column: str, **params: str) -> dict:
    response = values_of(client, run, column, **params)
    assert response.status_code == 200, response.text
    return response.json()


def cell(value: str | None, cut: bool = False) -> dict | None:
    return None if value is None else {"value": value, "cut": cut}


def held_reads(monkeypatch: pytest.MonkeyPatch) -> tuple[threading.Event, threading.Event]:
    """Reads of the file, by a run or for values, wait once they've begun until `release` is set."""
    reading, release = threading.Event(), threading.Event()
    load_rows = profile_jobs.load_rows

    def held_load_rows(*args, **kwargs):
        reading.set()
        assert release.wait(WAIT_FOR_A_RUN_SECONDS)
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_rows", held_load_rows)
    return reading, release


def profiled(client: TestClient, path: Path, text: str, mode: str = "fast") -> dict:
    path.write_text(text)
    return finished(client, start(client, connect(client, path), mode))


def test_a_columns_values_are_shown_labelled_with_the_part_of_the_file_and_when(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-30
    run = profiled(client, tmp_path / "sales.csv", "region,amount\nNorth,10\nSouth,2.5\nNorth,7\n")
    before = datetime.now(UTC)

    found = shown(client, run, "amount")

    assert found["profile_run_id"] == run["id"]
    assert found["column"] == "amount"
    assert (found["scope"], found["rows_read"]) == ("full", 3)
    assert before - timedelta(seconds=1) <= datetime.fromisoformat(found["computed_at"])
    assert datetime.fromisoformat(found["computed_at"]) <= datetime.now(UTC)
    assert (found["minimum"], found["maximum"]) == (cell("2.5"), cell("10.0"))
    assert found["frequent"][0] == {"value": cell("10"), "count": 1}
    assert len(found["histogram"]) == 10
    assert found["histogram"][0]["low"] == cell("2.5")
    assert (found["mean"], found["median"]) == (cell("6.5"), cell("7.0"))
    assert found["standard_deviation"] is not None
    text = shown(client, run, "region")
    assert (text["minimum"], text["maximum"]) == (cell("North"), cell("South"))
    assert text["frequent"] == [
        {"value": cell("North"), "count": 2},
        {"value": cell("South"), "count": 1},
    ]
    assert (text["histogram"], text["mean"], text["median"]) == (None, None, None)


def test_values_come_from_the_part_of_the_file_the_run_read(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-30: the first 100,000 rows after a fast profile, every row after a
    # full one.
    text = "id\n" + "".join(f"{number}\n" for number in range(FIRST_ROWS + 5))
    path = tmp_path / "numbers.csv"
    path.write_text(text)
    source = connect(client, path)
    fast = finished(client, start(client, source, "fast"))
    full = finished(client, start(client, source, "full"))

    from_fast, from_full = shown(client, fast, "id"), shown(client, full, "id")

    assert (from_fast["scope"], from_fast["rows_read"]) == ("sample", FIRST_ROWS)
    assert from_fast["maximum"] == cell(str(FIRST_ROWS - 1))
    assert (from_full["scope"], from_full["rows_read"]) == ("full", FIRST_ROWS + 5)
    assert from_full["maximum"] == cell(str(FIRST_ROWS + 4))


def test_values_are_cut_at_1000_characters_and_never_kept_by_the_browser(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-31: as in the preview.
    long, longer = "a" * 1000, "b" * 1001
    run = profiled(client, tmp_path / "notes.csv", f"note\n{long}\n{longer}\n{longer}\n")

    response = values_of(client, run, "note")
    found = response.json()

    assert response.headers["cache-control"] == "no-store"
    assert found["minimum"] == cell(long)
    assert found["maximum"] == cell("b" * 1000, cut=True)
    assert found["frequent"] == [
        {"value": cell("b" * 1000, cut=True), "count": 2},
        {"value": cell(long), "count": 1},
    ]


def test_a_number_columns_histogram_and_statistics_are_cut_too(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-31: a bucket's edge is a value from the file as much as the rest.
    run = profiled(client, tmp_path / "levels.csv", "level\n1\n5\n9\n")

    found = shown(client, run, "level")

    assert found["histogram"][0] == {"low": cell("1"), "high": cell("1"), "count": 1}
    assert found["median"] == cell("5.0")


def test_a_file_that_has_gone_gives_a_clear_message_and_the_saved_metrics_still_show(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-32
    path = tmp_path / "people.csv"
    run = profiled(client, path, "id,city\n1,Oslo\n2,Bergen\n")
    path.unlink()

    response = values_of(client, run, "city")

    assert response.status_code == 409
    assert response.json() == {
        "detail": "DrivnX can't find this file. It may have moved or been deleted."
    }
    assert client.get(f"/api/profile-runs/{run['id']}").json()["metrics"]


@posix_only
def test_a_file_that_cant_be_read_gives_a_clear_message(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-32
    path = tmp_path / "people.csv"
    run = profiled(client, path, "id,city\n1,Oslo\n2,Bergen\n")
    path.chmod(0)

    try:
        response = values_of(client, run, "city")
    finally:
        path.chmod(0o600)

    assert response.status_code == 409
    assert response.json()["detail"] == "DrivnX doesn't have permission to read this file."


def test_a_file_whose_columns_changed_points_to_its_settings(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-32: as a run's start is (AC-12).
    path = tmp_path / "people.csv"
    run = profiled(client, path, "id,city\n1,Oslo\n2,Bergen\n")
    path.write_text("id,town\n1,Oslo\n2,Bergen\n")

    response = values_of(client, run, "city")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This file's columns no longer match the saved ones. "
        "Open this source's settings and save them to read the columns again."
    )


SENSITIVE = "This column may hold personal data. Confirm that you want to see its values."


def test_a_possibly_sensitive_columns_values_need_the_extra_confirmation(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-29: refused with no values without it, shown with it.
    run = profiled(
        client, tmp_path / "people.csv", "id,email\n1,ann@example.com\n2,bob@example.com\n"
    )

    refused = values_of(client, run, "email")
    confirmed = shown(client, run, "email", confirmed="true")

    assert refused.status_code == 409
    assert refused.json() == {"detail": SENSITIVE}
    assert confirmed["minimum"] == cell("ann@example.com")


def test_a_column_not_flagged_needs_no_confirmation(client: TestClient, tmp_path: Path) -> None:
    # CFX-APP-csv-profile/AC-29
    run = profiled(
        client, tmp_path / "people.csv", "id,email\n1,ann@example.com\n2,bob@example.com\n"
    )

    assert shown(client, run, "id")["maximum"] == cell("2")


def test_a_confirmation_not_given_as_true_is_no_confirmation(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-29
    run = profiled(client, tmp_path / "people.csv", "email\nann@example.com\n")

    assert values_of(client, run, "email", confirmed="false").json() == {"detail": SENSITIVE}


def test_values_of_a_run_that_didnt_complete_are_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "people.csv"
    path.write_text("id,city\n1,Oslo\n")
    _, release = held_reads(monkeypatch)
    running = start(client, connect(client, path))

    try:
        response = values_of(client, running, "city")
    finally:
        release.set()
        finished(client, running)

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This profile hasn't completed, so it has no values to show."
    )


def test_values_while_a_profile_is_running_are_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Two DuckDB jobs could take twice the memory a profile is allowed.
    path = tmp_path / "people.csv"
    path.write_text("id,city\n1,Oslo\n")
    source = connect(client, path)
    done = finished(client, start(client, source))
    _, release = held_reads(monkeypatch)
    running = start(client, source)

    try:
        response = values_of(client, done, "city")
    finally:
        release.set()
        finished(client, running)

    assert response.status_code == 409
    assert response.json()["detail"] == BUSY


BUSY = "DrivnX is already reading a file. Wait for it to finish, then try again."


def test_values_while_other_values_are_being_worked_out_are_refused(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "people.csv"
    run = profiled(client, path, "id,city\n1,Oslo\n")
    reading, release = held_reads(monkeypatch)
    first: list = []
    asking = threading.Thread(target=lambda: first.append(values_of(client, run, "city")))
    asking.start()
    try:
        assert reading.wait(WAIT_FOR_A_RUN_SECONDS)
        second = values_of(client, run, "id")
    finally:
        release.set()
        asking.join(WAIT_FOR_A_RUN_SECONDS)

    assert second.status_code == 409
    assert second.json()["detail"] == BUSY
    assert first[0].status_code == 200
    assert values_of(client, run, "id").status_code == 200


def test_a_profile_wont_start_while_values_are_being_worked_out(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The other way round, for the same reason: two DuckDB jobs could take twice the memory.
    path = tmp_path / "people.csv"
    run = profiled(client, path, "id,city\n1,Oslo\n")
    reading, release = held_reads(monkeypatch)
    asking = threading.Thread(target=lambda: values_of(client, run, "city"))
    asking.start()
    try:
        assert reading.wait(WAIT_FOR_A_RUN_SECONDS)
        refused = client.post(
            "/api/profile-runs", json={"source_id": run["source_id"], "mode": "fast"}
        )
    finally:
        release.set()
        asking.join(WAIT_FOR_A_RUN_SECONDS)

    assert refused.status_code == 409
    assert refused.json()["detail"] == BUSY
    assert len(client.get("/api/profile-runs", params={"source_id": run["source_id"]}).json()) == 1


def test_values_of_a_run_made_with_earlier_settings_are_refused(
    client: TestClient, tmp_path: Path
) -> None:
    path = tmp_path / "people.csv"
    path.write_text("id;name\n1;Ann\n")
    source = connect(client, path)
    run = finished(client, start(client, source))
    corrected = client.put(
        f"/api/sources/{source['id']}/settings",
        json={
            "delimiter": ",",
            "encoding": source["encoding"],
            "has_header": True,
            "skip_lines": 0,
        },
    )
    assert corrected.status_code == 200, corrected.text

    response = values_of(client, run, "id")

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "This profile used earlier settings. Profile the file again to see its values."
    )


@pytest.mark.parametrize(("run_id", "column"), [("no-such-run", "id"), (None, "no_such_column")])
def test_values_of_no_such_run_or_column_are_not_found(
    client: TestClient, tmp_path: Path, run_id: str | None, column: str
) -> None:
    run = profiled(client, tmp_path / "people.csv", "id,city\n1,Oslo\n")

    response = values_of(client, {"id": run_id or run["id"]}, column)

    assert response.status_code == 404


@pytest.mark.parametrize(
    ("change", "status"),
    [
        ({"cookies": "none"}, 401),
        ({"headers": {"Host": "evil.example"}}, 403),
        ({"headers": {"Origin": "http://evil.example"}}, 403),
    ],
    ids=["no session", "another host", "another origin"],
)
def test_the_values_route_keeps_the_session_host_and_origin_checks(
    client: TestClient, tmp_path: Path, change: dict, status: int
) -> None:
    # CFX-APP-csv-profile/AC-34, as CFX-APP-first-slice/AC-08 and AC-09 ask of every route
    run = profiled(client, tmp_path / "people.csv", "id,city\n1,Oslo\n")
    if change.get("cookies") == "none":
        client.cookies.clear()

    response = client.get(
        f"/api/profile-runs/{run['id']}/values",
        params={"column": "city"},
        headers=change.get("headers"),
    )

    assert response.status_code == status
    assert "Oslo" not in response.text


def test_values_spill_only_inside_the_workspace_and_stay_within_the_memory_limit(
    client: TestClient,
    workspace_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-csv-profile/AC-34, with CFX-APP-csv-profile/AC-13: nothing in the current directory.
    current = tmp_path / "current"
    current.mkdir()
    monkeypatch.chdir(current)
    filler = "".join(chr(0x41 + number % 26) for number in range(1_000))
    run = profiled(
        client,
        tmp_path / "big_cells.csv",
        "id,text\n" + "".join(f"{row},{row}{filler}\n" for row in range(100_000)),
    )
    seen: dict[str, object] = {}
    values_for = profile_jobs.values_for

    def watching_values_for(connection, *args, **kwargs):
        seen["limit"] = connection.execute(
            "SELECT current_setting('memory_limit')"
        ).fetchone()[0]
        seen["spilled"] = list((workspace_dir / "duckdb-tmp").rglob("*"))
        return values_for(connection, *args, **kwargs)

    monkeypatch.setattr(profile_jobs, "values_for", watching_values_for)
    monkeypatch.setattr(profile_jobs, "PROFILE_MEMORY_LIMIT", "100MB")

    assert values_of(client, run, "text").status_code == 200
    assert seen["limit"] == "95.3 MiB"
    assert seen["spilled"] != []
    assert list(current.iterdir()) == []
    assert list((workspace_dir / "duckdb-tmp").iterdir()) == []
