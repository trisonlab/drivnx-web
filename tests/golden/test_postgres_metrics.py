"""Every metric a database's fast profile saves, against values worked out by hand,
CFX-APP-postgres AC-25 (DRIVN-54).

Each expected value below comes from reading the rows in `OBJECTS`, and PostgreSQL's own rules for
its types, not from running the code. Distinct values are counted on PostgreSQL's text form of
each value, and only NULL is missing (the plan agreed for DRIVN-51). A column that isn't text gets
its counts only: no lengths and no masked patterns (the spec's Assumptions).
"""

import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from ..integration.test_postgres_objects_api import DETAILS, objects_in

SCHEMA = "drivnx_golden"
DROP = (f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",)
# Made as the superuser, and read as the fixture's role.
OBJECTS = (
    f"CREATE SCHEMA {SCHEMA}",
    # Dates and times, with and without a time zone.
    f"""CREATE TABLE {SCHEMA}.moments (
        id integer PRIMARY KEY, instant timestamptz, wall timestamp, day date, clock time,
        clock_zoned timetz
    )""",
    f"""INSERT INTO {SCHEMA}.moments VALUES
        (1, '2024-03-10 12:00:00+00', '2024-03-10 12:00:00', '2024-03-10', '12:00', '12:00+00'),
        (2, '2024-03-10 13:00:00+01', '2024-03-10 12:00:00', '2024-03-10', '12:00:00.5',
            '13:00+01'),
        (3, NULL, NULL, NULL, NULL, NULL),
        (4, 'infinity', '-infinity', 'infinity', '24:00', '23:59:59.999999+14'),
        (5, '0044-03-15 12:00:00+00 BC', '2024-03-10 12:00:00.000001', '0044-03-15 BC', '00:00',
            '00:00-12')""",
    f"ANALYZE {SCHEMA}.moments",
    f"GRANT USAGE ON SCHEMA {SCHEMA} TO drivnx_fixture",
    f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO drivnx_fixture",
)


def counts(missing: int, distinct: int, rows: int) -> dict:
    """A column that isn't text: its missing and distinct counts, each distinct percentage of the
    values that aren't missing, as a CSV's (CFX-APP-csv-profile AC-19)."""
    present = rows - missing
    saved = {
        "missing_count": missing,
        "missing_percent": round(100 * missing / rows, 2),
        "distinct_count": distinct,
    }
    if present:
        saved["distinct_percent"] = round(100 * distinct / present, 2)
    return saved


EXPECTED = {
    "moments": {
        "rows_read": 5,
        "columns": {
            "id": counts(missing=0, distinct=5, rows=5),
            # Rows 1 and 2 are the same instant written with two offsets: one value. Row 3 is NULL;
            # 4 is infinity and 5 a date BC, so 3 of 4.
            "instant": counts(missing=1, distinct=3, rows=5),
            # Rows 1 and 2 are the same; 5 is a microsecond later, and 4 is -infinity: 3 of 4.
            "wall": counts(missing=1, distinct=3, rows=5),
            # Rows 1 and 2 are the same day; infinity and 44 BC are the others: 3 of 4.
            "day": counts(missing=1, distinct=3, rows=5),
            # 12:00, half a second later, 24:00 (not midnight) and midnight: 4 of 4.
            "clock": counts(missing=1, distinct=4, rows=5),
            # 12:00+00 and 13:00+01 are the same instant, but PostgreSQL keeps the zone in a timetz,
            # and they aren't equal: 4 of 4.
            "clock_zoned": counts(missing=1, distinct=4, rows=5),
        },
    },
}


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def profiled(client: TestClient, name: str) -> dict:
    """A completed fast profile of this one object of the golden schema."""
    source_id = client.post("/api/sources/postgres", json=DETAILS).json()["id"]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    chosen = client.put(
        f"/api/sources/{source_id}/selection", json={"object_ids": [listed[name]["id"]]}
    )
    assert chosen.status_code == 200, chosen.text
    started = client.post("/api/profile-runs", json={"source_id": source_id, "mode": "fast"})
    assert started.status_code == 202, started.text
    deadline = time.monotonic() + 30
    while True:
        run = client.get(f"/api/profile-runs/{started.json()['id']}").json()
        if run["status"] != "running":
            return run
        assert time.monotonic() < deadline
        time.sleep(0.01)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_every_saved_metric_equals_its_known_value(client: TestClient, name: str) -> None:
    # CFX-APP-postgres/AC-25: compared whole, so an extra or missing metric fails too.
    run = profiled(client, name)

    assert run["status"] == "completed", run["failure"]
    assert run["objects"] == [{"object": f"{SCHEMA}.{name}", "method": "whole"}]
    saved: dict[str, dict] = {}
    rows_read = None
    for metric in run["metrics"]:
        # A small table is read whole, so every metric is exact over all of it.
        assert (metric["scope"], metric["precision"]) == ("full", "exact")
        if metric["column"] is None:
            assert metric["metric"] == "rows_read"
            rows_read = metric["value"]
        else:
            saved.setdefault(metric["column"], {})[metric["metric"]] = metric["value"]
    assert rows_read == EXPECTED[name]["rows_read"]
    assert saved == EXPECTED[name]["columns"]
