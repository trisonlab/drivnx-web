"""A database's values on request when DrivnX stops or is killed mid-read, DRIVN-45 AC-33 for
values (DRIVN-56, found in review), against the real command and the PostgreSQL fixture."""

import subprocess
import threading
import time
from collections.abc import Callable, Iterator

from pathlib import Path

import httpx
import pytest

from ..conftest import StartDrivnX, posix_only
from .test_credential_store import NO_STORE
from .test_postgres_discovery import server_on_windows
from .test_postgres_earlier_runs import GONE_WITHIN_SECONDS, none_left_on_the_server
from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_profile_api import ended
from .test_postgres_stopping import (  # noqa: F401 (the fixture)
    SLEEP_SECONDS,
    drivnx_queries,
    reading,
)
from .test_session import sign_in

SCHEMA = "drivnx_values_stopping"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
QUICK = f"CREATE OR REPLACE VIEW {SCHEMA}.later_slow AS SELECT n FROM generate_series(1, 3) AS n"
# The same columns, so the run's object isn't out of date, but its rows come only after the sleep.
SLOW = f"{QUICK} WHERE (SELECT true FROM pg_sleep({SLEEP_SECONDS}))"
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    QUICK,
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)
# How each read of the view reaches the server, as in the stopping tests.
READS = {
    "fast": f'COPY (SELECT "n"::text AS c0 FROM "{SCHEMA}"."later_slow"%',
    "full": f'COPY (SELECT "n" FROM "{SCHEMA}"."later_slow"%',
}


@pytest.fixture
def view(postgres_admin: Callable[[str], None]) -> Iterator[Callable[[], None]]:
    """The view, quick for its run, and made slow by calling what this yields."""
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield lambda: postgres_admin(SLOW)
    finally:
        for statement in DROP:
            postgres_admin(statement)


def asking_for_values(
    start_drivnx: StartDrivnX,
    mode: str,
    made_slow: Callable[[], None],
    drivnx_queries: Callable[[str], int],
) -> subprocess.Popen:
    """A started DrivnX reading the slow view's values for a run made while it was quick."""
    started = start_drivnx("--no-browser", env=NO_STORE)
    client = sign_in(started.wait_for_launch())
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    chose = client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed["later_slow"]["id"]]},
    )
    assert chose.status_code == 200, chose.text
    run = client.post("/api/profile-runs", json={"source_id": source_id, "mode": mode})
    assert run.status_code == 202, run.text
    assert ended(client, run.json()["id"])["status"] == "completed"
    made_slow()

    def ask() -> None:
        try:
            client.get(
                f"/api/profile-runs/{run.json()['id']}/values",
                params={"schema": SCHEMA, "object": "later_slow", "column": "n"},
                timeout=SLEEP_SECONDS * 2,
            )
        except httpx.HTTPError:
            # DrivnX stopped under it, as the test means it to.
            pass

    threading.Thread(target=ask, daemon=True).start()
    reading(drivnx_queries, READS[mode])
    return started.process


# Windows' `terminate()` is a hard kill, not a stop, which the test after this one is.
@posix_only
def test_stopping_drivnx_during_a_full_runs_values_stops_their_read_within_5_seconds(
    start_drivnx: StartDrivnX,
    view: Callable[[], None],
    drivnx_queries: Callable[[str], int],
) -> None:
    # DRIVN-45 AC-33 and the Non-functional "stops within 5 s": a full read of values may last
    # the run's time limit, so stopping DrivnX mustn't wait for it, and leaves nothing running.
    process = asking_for_values(start_drivnx, "full", view, drivnx_queries)

    process.terminate()
    stopped_at = time.monotonic()

    process.wait(timeout=GONE_WITHIN_SECONDS)
    none_left_on_the_server(drivnx_queries, stopped_at)


@pytest.mark.parametrize("mode", ["fast", "full"])
def test_drivnx_killed_during_a_runs_values_leaves_nothing_on_the_server(
    start_drivnx: StartDrivnX,
    view: Callable[[], None],
    drivnx_queries: Callable[[str], int],
    tmp_path: Path,
    mode: str,
) -> None:
    # DRIVN-45 AC-33: "no query DrivnX started is still running on the server". Killed, so it
    # can't cancel anything: the server checks the values' session is still there, as it does a
    # run's (ADR 0009 Decision 7). Only where the server can.
    if server_on_windows(tmp_path / "version"):
        pytest.skip("PostgreSQL on Windows can't check a connection (ADR 0009).")
    process = asking_for_values(start_drivnx, mode, view, drivnx_queries)

    process.kill()
    killed_at = time.monotonic()
    process.wait(timeout=30)

    none_left_on_the_server(drivnx_queries, killed_at)
