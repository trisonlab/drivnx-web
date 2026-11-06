"""Every metric a profile saves, against values worked out by hand, CFX-APP-csv-profile AC-19.

Each expected value below comes from reading the file in `testdata/golden`, written by
`testdata/generate_csv.py`, and not from running the code.
"""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ..conftest import REPO_ROOT

GOLDEN_DIR = REPO_ROOT / "testdata" / "golden"


def pattern(text: str, count: int) -> dict:
    return {"pattern": text, "count": count, "cut": False}


EXPECTED = {
    "missing_values.csv": {
        "rows_read": 4,
        # `name` is blank in rows 2 and 3, one empty and one only spaces. `note` is always empty.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 4)],
            },
            "name": {
                "missing_count": 2,
                "missing_percent": 50.0,
                "distinct_count": 2,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 3,
                "longest_length": 3,
                "average_length": 3.0,
                "masked_patterns": [pattern("AAA", 2)],
            },
            "note": {
                "missing_count": 4,
                "missing_percent": 100.0,
                "distinct_count": 0,
                "not_fitting_count": 0,
            },
        },
    },
    "duplicates.csv": {
        "rows_read": 5,
        # `city`: Paris 3 times, Lyon twice, so lengths 5+5+4+5+4 = 23 over 5 values.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 5,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 5)],
            },
            "city": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 2,
                "distinct_percent": 40.0,
                "not_fitting_count": 0,
                "shortest_length": 4,
                "longest_length": 5,
                "average_length": 4.6,
                "masked_patterns": [pattern("AAAAA", 3), pattern("AAAA", 2)],
            },
            "code": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 2,
                "distinct_percent": 40.0,
                "not_fitting_count": 0,
                "shortest_length": 2,
                "longest_length": 2,
                "average_length": 2.0,
                "masked_patterns": [pattern("A9", 5)],
            },
        },
    },
    "dates_iso.csv": {
        "rows_read": 3,
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 3)],
            },
            "joined": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 10,
                "longest_length": 10,
                "average_length": 10.0,
                "masked_patterns": [pattern("9999-99-99", 3)],
            },
            "stamp": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 19,
                "longest_length": 19,
                "average_length": 19.0,
                "masked_patterns": [pattern("9999-99-99 99:99:99", 3)],
            },
        },
    },
    "dates_us.csv": {
        "rows_read": 3,
        # Every value fits, though a plain cast to DATE would refuse them all.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 3)],
            },
            "joined": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 10,
                "longest_length": 10,
                "average_length": 10.0,
                "masked_patterns": [pattern("99/99/9999", 3)],
            },
        },
    },
    "unicode.csv": {
        "rows_read": 4,
        # `name`: Zoé composed (3 code points) and decomposed (4), 李明 (2), Иван (4), so 13 over 4.
        # The two Zoés are different text and the same pattern. `price` lengths are 2+2+2+1 = 7.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 4)],
            },
            "name": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 2,
                "longest_length": 4,
                "average_length": 3.25,
                "masked_patterns": [
                    pattern("AAA", 2),
                    pattern("AA", 1),
                    pattern("AAAA", 1),
                ],
            },
            "price": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 2,
                "average_length": 1.75,
                "masked_patterns": [pattern("99", 3), pattern("9", 1)],
            },
        },
    },
    "mixed_types.csv": {
        # 20,600 rows of `7`, past DuckDB's detection sample, then 1.5, abc, 12 and an empty value.
        "rows_read": 20_604,
        "columns": {
            "id": {
                # 0 to 20,603: 10 of 1 digit, 90 of 2, 900 of 3, 9,000 of 4 and 10,604 of 5, which
                # is 91,910 digits over 20,604 values.
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 20_604,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 5,
                "average_length": 4.46,
                "masked_patterns": [
                    pattern("99999", 10_604),
                    pattern("9999", 9_000),
                    pattern("999", 900),
                    pattern("99", 90),
                    pattern("9", 10),
                ],
            },
            "value": {
                # 1 of 20,604 missing is 0.00485%. 4 distinct of 20,603 is 0.0194%. Lengths are
                # 20,600 + 3 + 3 + 2 = 20,608 over 20,603. `1.5` and `abc` aren't integers.
                "missing_count": 1,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 0.02,
                "not_fitting_count": 2,
                "shortest_length": 1,
                "longest_length": 3,
                "average_length": 1.0,
                "masked_patterns": [
                    pattern("9", 20_600),
                    pattern("9.9", 1),
                    pattern("99", 1),
                    pattern("AAA", 1),
                ],
            },
        },
    },
    "high_cardinality.csv": {
        "rows_read": 1_000,
        # 0 to 999: 10 of 1 digit, 90 of 2 and 900 of 3, which is 2,890 digits over 1,000 values.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 1_000,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 3,
                "average_length": 2.89,
                "masked_patterns": [
                    pattern("999", 900),
                    pattern("99", 90),
                    pattern("9", 10),
                ],
            },
            "code": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 1_000,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 5,
                "longest_length": 5,
                "average_length": 5.0,
                "masked_patterns": [pattern("A9999", 1_000)],
            },
            "group": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 0.3,
                "not_fitting_count": 0,
                "shortest_length": 2,
                "longest_length": 2,
                "average_length": 2.0,
                "masked_patterns": [pattern("A9", 1_000)],
            },
        },
    },
    "large_integers.csv": {
        "rows_read": 4,
        # `balance`: 3 values of 17 characters and `-5` make 53 over 4. `ref`: 3 of 20 and `7` make
        # 61 over 4. Every value is a whole number, however large.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 4)],
            },
            "balance": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 2,
                "longest_length": 17,
                "average_length": 13.25,
                "masked_patterns": [pattern("-9999999999999999", 3), pattern("-9", 1)],
            },
            "ref": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 4,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 20,
                "average_length": 15.25,
                "masked_patterns": [pattern("99999999999999999999", 3), pattern("9", 1)],
            },
        },
    },
    "dates_two_formats.csv": {
        "rows_read": 3,
        # DuckDB detects one date format, and both columns are still dates. Every value fits.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 3)],
            },
            "first": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 10,
                "longest_length": 10,
                "average_length": 10.0,
                "masked_patterns": [pattern("9999-99-99", 3)],
            },
            "second": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 10,
                "longest_length": 10,
                "average_length": 10.0,
                "masked_patterns": [pattern("9999/99/99", 3)],
            },
        },
    },
    "timestamps_two_formats.csv": {
        "rows_read": 3,
        # DuckDB detects one timestamp format, and both columns are still timestamps.
        "columns": {
            "id": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 1,
                "longest_length": 1,
                "average_length": 1.0,
                "masked_patterns": [pattern("9", 3)],
            },
            "local": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 19,
                "longest_length": 19,
                "average_length": 19.0,
                "masked_patterns": [pattern("99/99/9999 99:99:99", 3)],
            },
            "utc": {
                "missing_count": 0,
                "missing_percent": 0.0,
                "distinct_count": 3,
                "distinct_percent": 100.0,
                "not_fitting_count": 0,
                "shortest_length": 19,
                "longest_length": 19,
                "average_length": 19.0,
                "masked_patterns": [pattern("9999-99-99 99:99:99", 3)],
            },
        },
    },
}


def profiled(client: TestClient, path: Path) -> dict:
    connected = client.post("/api/sources", json={"path": str(path)})
    assert connected.status_code == 201, connected.text
    started = client.post(
        "/api/profile-runs", json={"source_id": connected.json()["id"], "mode": "fast"}
    )
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
    # CFX-APP-csv-profile/AC-19: compared whole, so an extra or missing metric fails too.
    run = profiled(client, GOLDEN_DIR / name)

    assert run["status"] == "completed"
    saved: dict[str, dict] = {}
    rows_read = None
    for metric in run["metrics"]:
        assert (metric["scope"], metric["precision"]) == ("full", "exact")
        if metric["column"] is None:
            assert metric["metric"] == "rows_read"
            rows_read = metric["value"]
        else:
            saved.setdefault(metric["column"], {})[metric["metric"]] = metric["value"]
    assert rows_read == EXPECTED[name]["rows_read"]
    assert saved == EXPECTED[name]["columns"]


def test_a_golden_file_exists_for_every_kind_the_spec_names() -> None:
    # CFX-APP-csv-profile/AC-19: missing values, duplicates, dates, Unicode, mixed types and high
    # cardinality. Dates has several files, as what DuckDB detects depends on the whole file.
    assert set(EXPECTED) == {path.name for path in GOLDEN_DIR.iterdir()}
