"""The warning before profiling with an account that could change the chosen objects, through the
API, DRIVN-45 AC-16 (DRIVN-52)."""

from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from .test_postgres_objects_api import DETAILS, objects_in
from .test_postgres_profile_api import ended

SCHEMA = "drivnx_write_warning"
# Roles outlive the schema they were granted on, so each is made and dropped here too.
WRITER = "drivnx_api_writer"
WRITER_PASSWORD = "drivnx-api-writer"  # pragma: allowlist secret, ai-skills: allow
SUPERUSER = "drivnx_api_superuser"
SUPERUSER_PASSWORD = "drivnx-api-superuser"  # pragma: allowlist secret, ai-skills: allow
DROP = (
    f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE",
    f"DROP ROLE IF EXISTS {WRITER}",
    f"DROP ROLE IF EXISTS {SUPERUSER}",
)
# Made as the superuser. The fixture's role only reads; the writer can change `orders`.
OBJECTS = (
    f"CREATE ROLE {WRITER} LOGIN PASSWORD '{WRITER_PASSWORD}'",
    f"CREATE ROLE {SUPERUSER} LOGIN SUPERUSER PASSWORD '{SUPERUSER_PASSWORD}'",
    f"CREATE SCHEMA {SCHEMA}",
    f"CREATE TABLE {SCHEMA}.orders (id integer, note text)",
    f"CREATE TABLE {SCHEMA}.people (id integer, name text)",
    f"INSERT INTO {SCHEMA}.people VALUES (1, 'Ann')",
    *(
        grant
        for role in ("drivnx_fixture", WRITER)
        for grant in (
            f"GRANT USAGE ON SCHEMA {SCHEMA} TO {role}",
            f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {role}",
        )
    ),
    f"GRANT INSERT, UPDATE (note) ON {SCHEMA}.orders TO {WRITER}",
)


@pytest.fixture(scope="module", autouse=True)
def objects(postgres_admin: Callable[[str], None]) -> Iterator[None]:
    for statement in (*DROP, *OBJECTS):
        postgres_admin(statement)
    try:
        yield
    finally:
        for statement in DROP:
            postgres_admin(statement)


def chosen_source(client: TestClient, user: str, password: str, *names: str) -> str:
    """A saved connection as `user`, with these objects of this test's schema chosen."""
    saved = client.post(
        "/api/sources/postgres", json={**DETAILS, "user": user, "password": password}
    )
    assert saved.status_code == 201, saved.text
    source_id = saved.json()["id"]
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    chosen = client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed[name]["id"] for name in names]},
    )
    assert chosen.status_code == 200, chosen.text
    return source_id


def start(client: TestClient, source_id: str, **confirmation: object) -> dict:
    """The response to starting a fast profile, with its HTTP status code as `http`."""
    response = client.post(
        "/api/profile-runs", json={"source_id": source_id, "mode": "fast", **confirmation}
    )
    return {**response.json(), "http": response.status_code}


def test_a_role_that_can_only_read_starts_with_no_warning_and_its_run_says_so(
    client: TestClient,
) -> None:
    # DRIVN-45/AC-16: "Given a role that can only read the chosen objects, then no warning
    # appears, and the screen says the account can only read them."
    source_id = chosen_source(client, "drivnx_fixture", DETAILS["password"], "orders", "people")

    started = start(client, source_id)

    assert started["http"] == 202, started
    assert started.get("read_only") is True
    assert ended(client, started["id"])["read_only"] is True


def test_a_role_that_can_write_is_refused_naming_the_objects_and_how(client: TestClient) -> None:
    # DRIVN-45/AC-16: "the screen names the objects ... and why, and the run starts only
    # after the user confirms". The client confirms by sending `write_access` back.
    source_id = chosen_source(client, WRITER, WRITER_PASSWORD, "orders", "people")

    refused = start(client, source_id)

    assert refused["http"] == 409, refused
    assert refused["detail"] == {
        "message": f"{WRITER} can change these chosen objects: {SCHEMA}.orders (insert, update)."
        " DrivnX opens every session read-only and only reads them. Confirm to profile them"
        " anyway.",
        "write_access": {
            "role": WRITER,
            "superuser": False,
            "objects": [{"object": f"{SCHEMA}.orders", "privileges": ["INSERT", "UPDATE"]}],
            "chosen": [f"{SCHEMA}.orders", f"{SCHEMA}.people"],
        },
    }
    assert client.get("/api/profile-runs").json() == []


def test_a_role_that_can_write_starts_once_the_user_confirms(client: TestClient) -> None:
    # DRIVN-45/AC-16: "the run starts only after the user confirms".
    source_id = chosen_source(client, WRITER, WRITER_PASSWORD, "orders", "people")
    access = start(client, source_id)["detail"]["write_access"]

    started = start(client, source_id, write_access=access)

    assert started["http"] == 202, started
    assert ended(client, started["id"])["read_only"] is False


def confirmed_source(client: TestClient, *names: str) -> str:
    """The writer's source with these objects chosen, its warning confirmed and its run ended."""
    source_id = chosen_source(client, WRITER, WRITER_PASSWORD, *names)
    access = start(client, source_id)["detail"]["write_access"]
    started = start(client, source_id, write_access=access)
    assert started["http"] == 202, started
    ended(client, started["id"])
    return source_id


def test_the_same_access_starts_again_without_asking(client: TestClient) -> None:
    # DRIVN-45, the Assumptions: "the write warning is asked again only when the chosen
    # objects or the role's privileges change since the last confirmation".
    source_id = confirmed_source(client, "orders", "people")

    again = start(client, source_id)

    assert again["http"] == 202, again
    assert ended(client, again["id"])["read_only"] is False


def test_another_chosen_object_asks_again(client: TestClient) -> None:
    # DRIVN-45, the Assumptions: asked again when the chosen objects change, even when the
    # new one is only read (agreed for DRIVN-52).
    source_id = confirmed_source(client, "orders")
    listed = objects_in(client.get(f"/api/sources/{source_id}/objects").json(), SCHEMA)
    client.put(
        f"/api/sources/{source_id}/selection",
        json={"object_ids": [listed["orders"]["id"], listed["people"]["id"]]},
    )

    asked = start(client, source_id)

    assert asked["http"] == 409, asked
    assert asked["detail"]["write_access"]["chosen"] == [f"{SCHEMA}.orders", f"{SCHEMA}.people"]


@pytest.mark.parametrize(
    "change, undo, privileges",
    [
        ("GRANT DELETE ON {} TO {}", "REVOKE DELETE ON {} FROM {}", ["INSERT", "UPDATE", "DELETE"]),
        ("REVOKE INSERT ON {} FROM {}", "GRANT INSERT ON {} TO {}", ["UPDATE"]),
    ],
)
def test_a_grant_or_a_revoke_asks_again(
    client: TestClient,
    postgres_admin: Callable[[str], None],
    change: str,
    undo: str,
    privileges: list[str],
) -> None:
    # DRIVN-45, the Assumptions: asked again when the role's privileges change.
    source_id = confirmed_source(client, "orders")
    postgres_admin(change.format(f"{SCHEMA}.orders", WRITER))
    try:
        asked = start(client, source_id)
    finally:
        postgres_admin(undo.format(f"{SCHEMA}.orders", WRITER))

    assert asked["http"] == 409, asked
    assert asked["detail"]["write_access"]["objects"] == [
        {"object": f"{SCHEMA}.orders", "privileges": privileges}
    ]


def test_a_confirmation_of_access_that_has_since_changed_is_refused_with_the_new(
    client: TestClient, postgres_admin: Callable[[str], None]
) -> None:
    # DRIVN-45/AC-16: the user confirms what they were shown, and only that.
    source_id = chosen_source(client, WRITER, WRITER_PASSWORD, "orders")
    shown = start(client, source_id)["detail"]["write_access"]
    postgres_admin(f"GRANT TRUNCATE ON {SCHEMA}.orders TO {WRITER}")
    try:
        refused = start(client, source_id, write_access=shown)
    finally:
        postgres_admin(f"REVOKE TRUNCATE ON {SCHEMA}.orders FROM {WRITER}")

    assert refused["http"] == 409, refused
    assert refused["detail"]["write_access"]["objects"] == [
        {"object": f"{SCHEMA}.orders", "privileges": ["INSERT", "UPDATE", "TRUNCATE"]}
    ]
    assert client.get("/api/profile-runs").json() == []


def test_a_superuser_is_refused_naming_its_role(client: TestClient) -> None:
    # DRIVN-45/AC-16: "or a superuser ... the screen names ... the superuser role and why".
    # It could change anything, so the role is the reason, whatever the objects.
    source_id = chosen_source(client, SUPERUSER, SUPERUSER_PASSWORD, "people")

    refused = start(client, source_id)

    assert refused["http"] == 409, refused
    assert refused["detail"]["message"] == (
        f"{SUPERUSER} is a superuser, so it can change anything in this database. DrivnX opens"
        " every session read-only and only reads it. Confirm to profile it anyway."
    )
    assert refused["detail"]["write_access"]["superuser"] is True
