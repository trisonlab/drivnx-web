"""A completed Parquet run's results, DRIVN-60 AC-14 (DRIVN-64)."""

import os
import threading
from pathlib import Path

import duckdb
import pytest
from artifacts.profiles import read_observations
from fastapi.testclient import TestClient
from local_api import profile_jobs

from .test_fast_profile import WAIT_FOR_A_RUN_SECONDS, finished, start
from .test_parquet_profile import (  # noqa: F401 (fixtures)
    connect,
    events,
    seeded,
    slices_of,
    written,
)
from .test_values_api import cell, shown

COUNTS = {"missing_count", "missing_percent", "distinct_count", "distinct_percent"}
LENGTHS = {"shortest_length", "longest_length", "average_length"}


@pytest.fixture
def small(tmp_path: Path) -> Path:
    # Six rows, so every value below is counted by hand.
    return written(
        tmp_path / "small.parquet",
        """
        SELECT * FROM (VALUES
            (1::BIGINT, 'ab', [1, 2], TIMESTAMP '2026-01-01 00:00:00'),
            (2, 'abc', [3], TIMESTAMP '2026-01-02 00:00:00'),
            (3, NULL, NULL, TIMESTAMP '2026-01-03 00:00:00'),
            (NULL, 'a', [4, 5], NULL),
            (5, '', [], TIMESTAMP '2026-01-05 00:00:00'),
            (5, 'abcd', [6], TIMESTAMP '2026-01-05 00:00:00')
        ) t(id, name, tags, seen_at)
        """,
    )


def by_column(run: dict) -> dict[str | None, dict[str, dict]]:
    columns: dict[str | None, dict[str, dict]] = {}
    for metric in run["metrics"]:
        columns.setdefault(metric["column"], {})[metric["metric"]] = metric
    return columns


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_each_column_has_the_csv_profiles_metrics_for_its_type(
    client: TestClient, small: Path, mode: str
) -> None:
    # DRIVN-60/AC-14: "each column shows the CSV profile's metrics". A Parquet column keeps its
    # native type, so none has values that don't fit; text lengths and masked patterns are a text
    # column's, and a list or a timestamp gets counts only (the spec's Assumptions).
    run = finished(client, start(client, connect(client, small), mode))

    assert run["status"] == "completed", run
    columns = by_column(run)
    assert set(columns["id"]) == COUNTS
    assert set(columns["name"]) == COUNTS | LENGTHS | {"masked_patterns"}
    assert set(columns["tags"]) == COUNTS
    assert set(columns["seen_at"]) == COUNTS
    values = {
        (column, metric): found["value"]
        for column, metrics in columns.items()
        for metric, found in metrics.items()
    }
    assert values[("id", "missing_count")] == 1
    assert values[("id", "distinct_count")] == 4
    # An empty text is a value in a typed file, as in a database.
    assert values[("name", "missing_count")] == 1
    assert values[("name", "distinct_count")] == 5
    assert values[("name", "shortest_length")] == 0
    assert values[("name", "longest_length")] == 4
    assert values[("name", "average_length")] == 2.0
    assert values[("tags", "missing_count")] == 1
    assert values[("seen_at", "missing_count")] == 1
    assert values[("seen_at", "distinct_count")] == 4


@pytest.mark.parametrize(
    ("file", "mode", "scope"),
    [("small", "fast", "full"), ("small", "full", "full"), ("events", "fast", "sample")],
)
def test_every_metric_says_what_it_was_measured_on_and_is_exact(
    client: TestClient, request: pytest.FixtureRequest, file: str, mode: str, scope: str
) -> None:
    # DRIVN-60/AC-14: "with their scope and precision", as a CSV's (DRIVN-9/AC-15 to AC-17). The
    # row count is the file's metadata's (AC-12).
    path: Path = request.getfixturevalue(file)

    run = finished(client, start(client, connect(client, path), mode))

    assert run["status"] == "completed", run
    assert {metric["precision"] for metric in run["metrics"]} == {"exact"}
    scopes = {
        (metric["column"], metric["metric"]): metric["scope"] for metric in run["metrics"]
    }
    assert scopes.pop((None, "row_count")) == "metadata"
    assert set(scopes.values()) == {scope}


def findings_of(client: TestClient, run: dict) -> list[tuple[str, str | None, str]]:
    response = client.get("/api/findings", params={"profile_run_id": run["id"]})
    assert response.status_code == 200, response.text
    return [
        (finding["kind"], finding["evidence"][0]["column"], finding["interpretation"])
        for finding in response.json()
    ]


def test_the_csv_profiles_findings_and_sensitive_field_rules_apply(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14: "the CSV profile's findings and sensitive-field rules apply" (DRIVN-9 AC-21,
    # AC-23, AC-27). `id` is distinct, a quarter of `note` is missing, `country` has one value,
    # the last row is there 3 times, and `customer_email` is named and shaped like an email
    # address. A typed Parquet column has no values that don't fit.
    rows = """
        SELECT
            i::BIGINT AS id,
            CASE WHEN i % 4 = 0 THEN NULL ELSE 'n' || (i % 7) END AS note,
            'FR' AS country,
            'user' || (i % 10) || '@example.com' AS customer_email
        FROM range(30000) t(i)
    """
    path = written(
        tmp_path / "every_kind.parquet",
        f"""
        SELECT * FROM ({rows})
        UNION ALL SELECT * FROM ({rows}) WHERE id = 29999
        UNION ALL SELECT * FROM ({rows}) WHERE id = 29999
        """,
    )

    run = finished(client, start(client, connect(client, path), "full"))

    assert run["status"] == "completed", run
    findings = findings_of(client, run)
    assert [(kind, column) for kind, column, _ in findings] == [
        ("possibly_sensitive", "customer_email"),
        ("duplicate_rows", None),
        ("missing_values", "note"),
        ("possible_identifier", "id"),
        ("single_value", "country"),
    ]
    assert findings[0][2] == (
        "Possibly sensitive field: its name suggests an email address, and its values are"
        " shaped like email addresses"
    )


def test_a_fast_runs_values_come_from_its_own_seeded_slices(
    client: TestClient, events: Path, seeded
) -> None:
    # DRIVN-60/AC-14: "values on request are computed now from the run's own rows (… the same
    # seeded row groups)". `id` is the row's number, so the range shows which rows were read.
    seeded(42)
    run = finished(client, start(client, connect(client, events)))
    slices = slices_of(client, run)

    found = shown(client, run, "id")

    assert (found["scope"], found["rows_read"]) == ("sample", 100_000)
    first_begin, _ = slices[0]
    last_begin, last_rows = slices[-1]
    assert (found["minimum"], found["maximum"]) == (
        cell(str(first_begin)),
        cell(str(last_begin + last_rows - 1)),
    )


def test_a_full_runs_values_come_from_every_row(client: TestClient, events: Path) -> None:
    # DRIVN-60/AC-14: a full run read every row, so its values do.
    run = finished(client, start(client, connect(client, events), "full"))

    found = shown(client, run, "id")

    assert (found["scope"], found["rows_read"]) == ("full", 400_000)
    assert (found["minimum"], found["maximum"]) == (cell("0"), cell("399999"))


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_a_small_files_values_are_of_every_row_as_its_type(
    client: TestClient, small: Path, mode: str
) -> None:
    # DRIVN-60/AC-14 and AC-30: a file no larger than the sample is read whole. Counted by hand.
    run = finished(client, start(client, connect(client, small), mode))

    number = shown(client, run, "id")
    assert (number["scope"], number["rows_read"]) == ("full", 6)
    assert (number["minimum"], number["maximum"]) == (cell("1"), cell("5"))
    assert number["frequent"][0] == {"value": cell("5"), "count": 2}
    assert (number["mean"], number["median"]) == (cell("3.2"), cell("3.0"))
    # `name` alone suggests a person's name, so its values need the confirmation.
    text = shown(client, run, "name", confirmed="true")
    # An empty text is a value in a typed file, as in a run.
    assert (text["minimum"], text["maximum"]) == (cell(""), cell("abcd"))
    assert [frequent["value"] for frequent in text["frequent"]] == [
        cell(value) for value in ("", "a", "ab", "abc", "abcd")
    ]
    assert (text["histogram"], text["mean"], text["median"]) == (None, None, None)
    moment = shown(client, run, "seen_at")
    assert (moment["minimum"], moment["maximum"]) == (
        cell("2026-01-01 00:00:00"),
        cell("2026-01-05 00:00:00"),
    )
    assert moment["median"] == cell("2026-01-03 00:00:00")


def test_values_of_a_column_the_run_doesnt_have_are_not_found(
    client: TestClient, small: Path
) -> None:
    # DRIVN-60/AC-14: as a CSV's (DRIVN-9/AC-30).
    run = finished(client, start(client, connect(client, small)))

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "city"})

    assert response.status_code == 404
    assert response.json() == {"detail": "There's no such column in this profile."}


def test_a_possibly_sensitive_columns_values_need_the_extra_confirmation(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14: "the CSV profile's … sensitive-field rules apply", so its values are refused
    # without the confirmation, and shown with it (DRIVN-9/AC-29).
    path = written(
        tmp_path / "people.parquet",
        "SELECT * FROM (VALUES (1, 'ann@example.com'), (2, 'bob@example.com')) t(id, email)",
    )
    run = finished(client, start(client, connect(client, path)))

    refused = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "email"})
    confirmed = shown(client, run, "email", confirmed="true")

    assert refused.status_code == 409
    assert refused.json() == {
        "detail": "This column may hold personal data. Confirm that you want to see its values."
    }
    assert confirmed["minimum"] == cell("ann@example.com")


def test_a_file_that_has_gone_gives_a_clear_message_and_the_saved_metrics_still_show(
    client: TestClient, small: Path
) -> None:
    # DRIVN-60/AC-14 and AC-31: as a CSV's (DRIVN-9/AC-32).
    run = finished(client, start(client, connect(client, small)))
    small.unlink()

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "id"})

    assert response.status_code == 409
    assert response.json() == {
        "detail": "DrivnX can't find this file. It may have moved or been deleted."
    }
    assert client.get(f"/api/profile-runs/{run['id']}").json()["metrics"]


@pytest.mark.parametrize(
    "select",
    ["SELECT 1::BIGINT AS code, 'x' AS name", "SELECT 'one' AS id, 'x' AS name"],
    ids=["renamed", "retyped"],
)
def test_a_file_whose_columns_changed_shows_no_values_from_the_wrong_column(
    client: TestClient, tmp_path: Path, select: str
) -> None:
    # DRIVN-60/AC-14: values are read by position, so they'd otherwise be another column's. Said
    # as a run that meets it says (AC-31), as a Parquet file has no settings to save.
    path = written(tmp_path / "events.parquet", "SELECT 1::BIGINT AS id, 'x' AS name")
    run = finished(client, start(client, connect(client, path)))
    written(path, select)

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "id"})

    assert response.status_code == 409
    assert response.json() == {"detail": "The file's columns no longer match the saved ones."}


def test_a_value_that_cant_be_read_says_so_and_quotes_nothing_from_the_file(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14 and AC-31: as a run that meets it fails. DuckDB's message can quote a row.
    path = tmp_path / "events.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            "COPY (SELECT i AS id, 'value-' || lpad(i::VARCHAR, 4, '0') AS v FROM range(1000) t(i))"
            f" TO '{path}' (FORMAT parquet, COMPRESSION uncompressed)"
        )
    run = finished(client, start(client, connect(client, path)))
    assert run["status"] == "completed", run
    damaged = bytearray(path.read_bytes())
    at = damaged.find(b"value-0500")
    assert at > 0, "the value isn't stored as plain text, so this test proves nothing"
    damaged[at] = 0xFF
    path.write_bytes(bytes(damaged))

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "v"})

    assert response.status_code == 409
    assert response.json() == {"detail": "DrivnX couldn't read this file."}


def test_values_while_a_profile_is_running_are_refused(
    client: TestClient, small: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # DRIVN-60/AC-14: as a CSV's, since two DuckDB jobs could take twice a profile's memory.
    source = connect(client, small)
    done = finished(client, start(client, source))
    release = threading.Event()
    load = profile_jobs.load_parquet_rows

    def held_load(*args, **kwargs):
        assert release.wait(WAIT_FOR_A_RUN_SECONDS)
        return load(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_parquet_rows", held_load)
    running = start(client, source)

    try:
        response = client.get(f"/api/profile-runs/{done['id']}/values", params={"column": "id"})
    finally:
        release.set()
        finished(client, running)

    assert response.status_code == 409
    assert response.json() == {
        "detail": "DrivnX is already reading a file. Wait for it to finish, then try again."
    }


def run_detail(client: TestClient, run: dict) -> dict:
    response = client.get(f"/api/profile-runs/{run['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_every_run_of_a_parquet_file_is_listed_and_any_completed_one_opens(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14: "earlier runs are listed and opened as a CSV's" (DRIVN-9/AC-35).
    path = written(tmp_path / "events.parquet", "SELECT i AS id FROM range(3) t(i)")
    source = connect(client, path)
    first = finished(client, start(client, source))
    written(path, "SELECT i AS id FROM range(5) t(i)")
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
    assert rows_read == {3}


def test_a_run_on_an_unchanged_parquet_file_is_not_out_of_date(
    client: TestClient, small: Path
) -> None:
    # DRIVN-60/AC-14
    run = finished(client, start(client, connect(client, small)))

    assert run_detail(client, run)["file_changed"] is False


@pytest.mark.parametrize("change", ["size", "modified time"])
def test_a_parquet_file_changed_since_its_run_is_said_to_have_changed(
    client: TestClient, small: Path, change: str
) -> None:
    # DRIVN-60/AC-14: "When the file's size or modified time has changed since the run".
    run = finished(client, start(client, connect(client, small)))
    stat = small.stat()

    if change == "size":
        written(small, "SELECT i::BIGINT AS id FROM range(100) t(i)")
        os.utime(small, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert small.stat().st_size != stat.st_size
    else:
        os.utime(small, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))

    assert run_detail(client, run)["file_changed"] is True


def test_values_are_never_saved_nor_kept_by_the_browser_and_are_cut_at_1000_characters(
    client: TestClient, tmp_path: Path
) -> None:
    # DRIVN-60/AC-14: "computed now … and never saved" (ADR 0004), as a CSV's (DRIVN-9/AC-31).
    long, longer = "a" * 1000, "b" * 1001
    path = written(
        tmp_path / "notes.parquet",
        f"SELECT * FROM (VALUES ('{long}'), ('{longer}'), ('{longer}')) t(note)",
    )
    run = finished(client, start(client, connect(client, path)))
    engine = client.app.state.workspace.engine
    saved_before = read_observations(engine, run["id"])

    response = client.get(f"/api/profile-runs/{run['id']}/values", params={"column": "note"})

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    found = response.json()
    assert found["minimum"] == cell(long)
    assert found["frequent"] == [
        {"value": cell("b" * 1000, cut=True), "count": 2},
        {"value": cell(long), "count": 1},
    ]
    assert read_observations(engine, run["id"]) == saved_before
    assert run_detail(client, run)["metrics"] == run["metrics"]
