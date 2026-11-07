"""`GET /api/findings`, CFX-APP-csv-profile AC-23."""

import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from local_api import profile_jobs

from .test_fast_profile import WAIT_FOR_A_RUN_SECONDS, connect, finished, start
from .test_full_profile import meeting_every_kind


def findings_of(client: TestClient, run: dict) -> list[dict]:
    response = client.get("/api/findings", params={"profile_run_id": run["id"]})
    assert response.status_code == 200, response.text
    return response.json()


def test_a_runs_findings_say_what_was_observed_inferred_and_why_with_their_evidence(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-23
    source = connect(client, meeting_every_kind(tmp_path / "every_kind.csv"))
    run = finished(client, start(client, source, "full"))

    findings = findings_of(client, run)

    assert [finding["kind"] for finding in findings] == [
        "not_fitting",
        "duplicate_rows",
        "missing_values",
        "possible_identifier",
        "single_value",
    ]
    first = findings[0]
    assert first["state"] == "inferred"
    assert first["profile_run_id"] == run["id"]
    assert all(first[part] for part in ("interpretation", "why_it_matters", "recommended_action"))
    assert first["evidence"] == [
        {
            "column": "amount",
            "metric": "not_fitting_count",
            "value": 1,
            "scope": "full",
            "precision": "exact",
        }
    ]
    assert findings[2]["evidence"][0] == {
        "column": "note",
        "metric": "missing_count",
        "value": 7_500,
        "scope": "full",
        "precision": "exact",
    }


def test_only_that_runs_findings_are_given_and_only_once_it_has_completed(
    client: TestClient, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-csv-profile/AC-23 and CFX-APP-csv-profile/AC-26
    source = connect(client, meeting_every_kind(tmp_path / "every_kind.csv"))
    earlier = finished(client, start(client, source, "full"))
    release = threading.Event()
    load_rows = profile_jobs.load_rows

    def held_load_rows(*args, **kwargs):
        assert release.wait(WAIT_FOR_A_RUN_SECONDS)
        return load_rows(*args, **kwargs)

    monkeypatch.setattr(profile_jobs, "load_rows", held_load_rows)
    running = start(client, source, "full")

    try:
        assert findings_of(client, running) == []
        assert {finding["profile_run_id"] for finding in findings_of(client, earlier)} == {
            earlier["id"]
        }
        assert client.get("/api/findings", params={"profile_run_id": "nope"}).status_code == 404
    finally:
        release.set()
        finished(client, running)


def test_columns_matching_a_sensitive_field_rule_are_flagged_saying_what_matched(
    client: TestClient, tmp_path: Path
) -> None:
    # CFX-APP-csv-profile/AC-21 and CFX-APP-csv-profile/AC-27: synthetic columns for each rule, with
    # some that match none, through a real run. Only names and masked patterns are read.
    path = tmp_path / "people.csv"
    path.write_text(
        "customer_email,contact,Phone,ref,date_of_birth,amount,note\n"
        + "".join(
            f"user{row}@example.com,c{row}@example.org,+44 20 7946 {row:04d},"
            f"4111 1111 1111 {row:04d},1990-01-{row % 28 + 1:02d},{row}.5,note {row}\n"
            for row in range(50)
        )
    )
    source = connect(client, path)
    run = finished(client, start(client, source, "fast"))

    flagged = [
        (finding["evidence"][0]["column"], finding["interpretation"])
        for finding in findings_of(client, run)
        if finding["kind"] == "possibly_sensitive"
    ]

    assert flagged == [
        (
            "customer_email",
            (
                "Possibly sensitive field: its name suggests an email address, and its values are"
                " shaped like email addresses"
            ),
        ),
        ("contact", "Possibly sensitive field: its values are shaped like email addresses"),
        (
            "Phone",
            (
                "Possibly sensitive field: its name suggests a phone number, and its values are"
                " shaped like phone numbers"
            ),
        ),
        ("ref", "Possibly sensitive field: its values are shaped like payment card numbers"),
        ("date_of_birth", "Possibly sensitive field: its name suggests a date of birth"),
    ]
