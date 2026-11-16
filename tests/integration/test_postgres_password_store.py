"""A database's password in the OS credential store, through the API, CFX-APP-postgres AC-04,
AC-06 and AC-30 (DRIVN-48)."""

import secrets
import socket
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from artifacts.sources import StoredPostgresSource, add_postgres_source, find_source
from artifacts.workspace import Workspace, open_workspace
from fastapi.testclient import TestClient
from keyring.backend import KeyringBackend
from keyring.errors import KeyringLocked, PasswordSetError
from local_api.app import create_app
from local_api.credentials import entry
from connectors.postgres import ServerFacts
from local_api.postgres_api import CREDENTIAL_STORE, UNTIL_STOP, KeptPasswords
from local_api.session import LaunchSession

from .. import postgres_fixture
from ..conftest import CLIENT_ORIGIN, CLIENT_PORT, StartDrivnX
from .test_credential_store import (  # noqa: F401 (the fixtures)
    NO_STORE,
    MemoryStore,
    RealStore,
    real_store,
    this_computers_store,
)
from .test_postgres_sources_api import DETAILS, QUOTES, parts_of
from .test_session import sign_in

# Starts DrivnX with this computer's credential store, if any, standing for DrivnX's own.
StartApp = Callable[[KeyringBackend | None], TestClient]


@pytest.fixture
def workspace(workspace_dir: Path) -> Iterator[Workspace]:
    opened = open_workspace(workspace_dir)
    yield opened
    opened.close()


@pytest.fixture
def start_app(tmp_path: Path, workspace: Workspace) -> StartApp:
    """A signed-in client for a new app on the same workspace: what a restart is to the API, as
    a new app keeps no password of its own."""
    ui_dir = tmp_path / "ui"
    ui_dir.mkdir()
    (ui_dir / "index.html").write_text("<html></html>", encoding="utf-8", newline="")

    def start(store: KeyringBackend | None) -> TestClient:
        session = LaunchSession(CLIENT_PORT)
        client = TestClient(
            create_app(session, CLIENT_PORT, workspace, ui_dir, credential_store=store),
            base_url=CLIENT_ORIGIN,
            headers={"Origin": CLIENT_ORIGIN},
        )
        client.post("/api/session", json={"code": session.launch_code})
        return client

    return start


def test_saving_a_connection_puts_its_password_in_the_store_under_drivnx_and_the_source(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-04: "when a connection is saved, then its password is stored there
    # under an entry naming DrivnX and the source".
    store = MemoryStore()

    response = start_app(store).post("/api/sources/postgres", json=DETAILS)

    assert response.status_code == 201
    saved = response.json()
    assert saved["password_kept"] == CREDENTIAL_STORE
    assert "password" not in saved
    source = find_source(workspace.engine, saved["id"])
    assert store.passwords == {entry(source): DETAILS["password"]}


def test_after_a_restart_the_password_comes_from_the_store_without_asking(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-04: "DrivnX reads it from there after a restart".
    store = MemoryStore()
    start_app(store).post("/api/sources/postgres", json=DETAILS)

    (source,) = start_app(store).get("/api/sources").json()

    assert source["password_needed"] is False
    assert source["password_kept"] == CREDENTIAL_STORE
    # Not connected yet in this run: opening the source asks the server (see /connect).
    assert source["server_version"] is None


def test_opening_a_source_after_a_restart_connects_with_the_stored_password(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-04, and AC-02's server version and encryption after a restart.
    store = MemoryStore()
    source_id = start_app(store).post("/api/sources/postgres", json=DETAILS).json()["id"]
    after = start_app(store)

    response = after.post(f"/api/sources/{source_id}/connect")

    assert response.status_code == 200
    connected = response.json()
    assert connected["server_version"].split(".")[0].isdigit()
    assert connected["encrypted"] is True
    assert connected["password_needed"] is False
    (listed,) = after.get("/api/sources").json()
    assert listed["server_version"] == connected["server_version"]


REFUSED = {"detail": "The server refused this user name and password."}
WRONG = "not-the-fixture-password"  # pragma: allowlist secret, ai-skills: allow


def saved_with_a_wrong_stored_password(
    start_app: StartApp, workspace: Workspace
) -> tuple[MemoryStore, StoredPostgresSource]:
    """A saved source whose stored password the server now refuses, as after it was changed."""
    store = MemoryStore()
    source_id = start_app(store).post("/api/sources/postgres", json=DETAILS).json()["id"]
    source = find_source(workspace.engine, source_id)
    assert isinstance(source, StoredPostgresSource)
    store.passwords[entry(source)] = WRONG
    return store, source


def test_a_stored_password_the_server_refuses_asks_again_and_a_new_one_replaces_it(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06: "when the source is opened, then the user is asked for the
    # password, and saving a new one replaces the stored one".
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)

    opened = after.post(f"/api/sources/{source.id}/connect")

    assert (opened.status_code, opened.json()) == (422, REFUSED)
    (listed,) = after.get("/api/sources").json()
    assert listed["password_needed"] is True

    given = after.post(f"/api/sources/{source.id}/password", json={"password": DETAILS["password"]})

    assert given.status_code == 200
    assert store.passwords[entry(source)] == DETAILS["password"]
    (again,) = start_app(store).get("/api/sources").json()
    assert again["password_needed"] is False


def test_a_refused_stored_password_still_says_the_next_one_goes_in_the_store(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06 with AC-04: the password asked for replaces the stored one, so the
    # screen mustn't say it's kept only until DrivnX stops (AC-05).
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)
    after.post(f"/api/sources/{source.id}/connect")

    (listed,) = after.get("/api/sources").json()

    assert (listed["password_needed"], listed["password_kept"]) == (True, CREDENTIAL_STORE)


def test_a_stored_password_refused_while_listing_asks_again(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06 when Refresh is the first read: the source asks for the password
    # too, and the saved list stays as it was.
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)
    listed_before = after.get(f"/api/sources/{source.id}/objects").json()

    refreshed = after.post(f"/api/sources/{source.id}/discover")

    assert (refreshed.status_code, refreshed.json()) == (422, REFUSED)
    (listed,) = after.get("/api/sources").json()
    assert listed["password_needed"] is True
    assert after.get(f"/api/sources/{source.id}/objects").json() == listed_before


def test_an_unreachable_server_keeps_the_password(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06 asks again only when the server refuses the password; a server
    # that doesn't answer may answer later, with the same password.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    source = StoredPostgresSource(
        id=uuid.uuid4().hex,
        host="127.0.0.1",
        port=closed_port,
        database=DETAILS["database"],
        user=DETAILS["user"],
        tls_mode="prefer",
        ca_path=None,
    )
    add_postgres_source(workspace.engine, source)
    store = MemoryStore()
    store.passwords[entry(source)] = DETAILS["password"]
    after = start_app(store)

    for path in ("connect", "discover"):
        response = after.post(f"/api/sources/{source.id}/{path}")

        assert response.status_code == 422, path
        assert response.json() != REFUSED
        (listed,) = after.get("/api/sources").json()
        assert listed["password_needed"] is False, path


def test_a_wrong_password_given_again_leaves_the_stored_one(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-06: only a password the server takes replaces the stored one.
    store, source = saved_with_a_wrong_stored_password(start_app, workspace)
    after = start_app(store)
    after.post(f"/api/sources/{source.id}/connect")

    given = after.post(
        f"/api/sources/{source.id}/password",
        json={"password": "also-wrong"},  # pragma: allowlist secret, ai-skills: allow
    )

    assert (given.status_code, given.json()) == (422, REFUSED)
    assert store.passwords[entry(source)] == WRONG
    (listed,) = after.get("/api/sources").json()
    assert listed["password_needed"] is True


class LockedStore(MemoryStore):
    """A store that can't keep a password: locked, its prompt dismissed, or the password too
    long for Windows' 2,560 bytes."""

    def set_password(self, service: str, username: str, password: str) -> None:
        raise PasswordSetError("The keyring is locked.")


def test_a_store_that_cannot_keep_it_says_until_drivnx_stops(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05 where the store fails: kept in memory, and the screen says so.
    store = LockedStore()

    response = start_app(store).post("/api/sources/postgres", json=DETAILS)

    assert response.status_code == 201
    assert response.json()["password_kept"] == UNTIL_STOP
    assert response.json()["password_needed"] is False
    assert store.passwords == {}


class StoreSaid(Exception):
    """Not a KeyringError, as keyring raises none for Windows' `pywintypes.error` or Secret
    Service's dismissed prompt. Its message stands for the store's own text."""


STORE_TEXT = "the store's own words, never shown"


class WriteFailingStore(MemoryStore):
    def set_password(self, service: str, username: str, password: str) -> None:
        raise StoreSaid(STORE_TEXT)


def test_a_store_failing_with_another_error_keeps_the_password_until_drivnx_stops(
    start_app: StartApp, postgres_server: str, caplog: pytest.LogCaptureFixture
) -> None:
    # CFX-APP-postgres/AC-05 (ADR 0008 Decision 8): any failure of the store is no store, for
    # that save, and only the kind of failure is logged.
    response = start_app(WriteFailingStore()).post("/api/sources/postgres", json=DETAILS)

    assert response.status_code == 201
    assert (response.json()["password_kept"], response.json()["password_needed"]) == (
        UNTIL_STOP,
        False,
    )
    assert "StoreSaid" in caplog.text
    assert STORE_TEXT not in caplog.text


class ReadFailingStore(MemoryStore):
    def get_password(self, service: str, username: str) -> str | None:
        raise StoreSaid(STORE_TEXT)


def test_a_store_failing_with_another_error_when_read_asks_for_the_password(
    start_app: StartApp, postgres_server: str, caplog: pytest.LogCaptureFixture
) -> None:
    # CFX-APP-postgres/AC-05 (ADR 0008 Decision 8): the sources are still listed, and the
    # source asks for its password, as with no store.
    store = ReadFailingStore()
    start_app(store).post("/api/sources/postgres", json=DETAILS)

    response = start_app(store).get("/api/sources")

    assert response.status_code == 200
    (source,) = response.json()
    assert source["password_needed"] is True
    assert "StoreSaid" in caplog.text
    assert STORE_TEXT not in caplog.text


def test_opening_a_source_reads_the_stored_password_without_writing_it_again(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-04: still in the store, so the screen says so, even when the store
    # couldn't take a write now.
    store = MemoryStore()
    source_id = start_app(store).post("/api/sources/postgres", json=DETAILS).json()["id"]
    unwritable = LockedStore()
    unwritable.passwords = dict(store.passwords)

    response = start_app(unwritable).post(f"/api/sources/{source_id}/connect")

    assert response.status_code == 200
    assert response.json()["password_kept"] == CREDENTIAL_STORE


class UnreadableStore(MemoryStore):
    """A store that keeps a password but can't give it back: locked after a restart."""

    def get_password(self, service: str, username: str) -> str | None:
        raise KeyringLocked("The keyring is locked.")


def test_a_store_that_cannot_be_read_after_a_restart_asks_for_the_password(
    start_app: StartApp, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05 where the store fails: the source asks, as with no store.
    store = UnreadableStore()
    start_app(store).post("/api/sources/postgres", json=DETAILS)

    response = start_app(store).get("/api/sources")

    assert response.status_code == 200
    (source,) = response.json()
    assert source["password_needed"] is True


class CountingStore(MemoryStore):
    """Counts how often DrivnX reads the store: a locked Secret Service shows its unlock prompt
    on every read, even of an entry it doesn't have."""

    def __init__(self, locked: bool) -> None:
        super().__init__()
        self.locked = locked
        self.reads = 0

    def get_password(self, service: str, username: str) -> str | None:
        self.reads += 1
        if self.locked:
            raise KeyringLocked("The keyring is locked.")
        return super().get_password(service, username)


@pytest.mark.parametrize("locked", [True, False], ids=["locked", "no entry"])
def test_after_a_restart_the_store_is_read_once_however_often_the_sources_are_listed(
    start_app: StartApp, postgres_server: str, locked: bool
) -> None:
    # CFX-APP-postgres/AC-04 and AC-05: an unlock prompt the user dismissed doesn't come back
    # each time the list reloads; the source asks for the password instead.
    start_app(None).post("/api/sources/postgres", json=DETAILS)
    store = CountingStore(locked)
    after = start_app(store)

    for _ in range(3):
        (source,) = after.get("/api/sources").json()

    assert store.reads == 1
    assert source["password_needed"] is True


def test_a_locked_store_is_read_once_for_all_the_sources(
    start_app: StartApp, workspace: Workspace, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05 (ADR 0008 Decision 6): a locked Secret Service prompts on each read,
    # so a dismissed prompt doesn't come back once for each database source.
    start_app(None).post("/api/sources/postgres", json=DETAILS)
    add_postgres_source(
        workspace.engine,
        StoredPostgresSource(
            id=uuid.uuid4().hex,
            host="127.0.0.1",
            port=5433,
            database="another",
            user=DETAILS["user"],
            tls_mode="prefer",
            ca_path=None,
        ),
    )
    store = CountingStore(locked=True)

    listed = start_app(store).get("/api/sources").json()

    assert [source["password_needed"] for source in listed] == [True, True]
    assert store.reads == 1


def test_a_refusal_of_an_older_password_leaves_the_one_given_since(
    workspace: Workspace,
) -> None:
    # CFX-APP-postgres/AC-06 in two tabs: one opens the source with the stored password while the
    # other gives a new one the server takes; the first one's refusal is of the old password.
    source = StoredPostgresSource(
        id=uuid.uuid4().hex,
        host="db.example.com",
        port=5432,
        database="sales",
        user="analyst",
        tls_mode="prefer",
        ca_path=None,
    )
    store = MemoryStore()
    store.passwords[entry(source)] = "old-password"  # pragma: allowlist secret, ai-skills: allow
    passwords = KeptPasswords(store)
    opening = passwords.get(source)
    assert opening is not None
    passwords.keep(source, "new-password", ServerFacts(version="18.1", encrypted=True))

    passwords.refused(source.id, opening)

    kept = passwords.get(source)
    assert kept is not None and kept.password == "new-password"  # pragma: allowlist secret


def stored(saved: dict) -> StoredPostgresSource:
    """The source as the workspace holds it, from the API's answer to saving it."""
    return StoredPostgresSource(
        **{name: saved[name] for name in ("id", "host", "port", "database", "user", "tls_mode")},
        ca_path=saved["ca_path"],
    )


def test_no_part_of_a_stored_password_reaches_the_workspace_or_the_most_detailed_logs(
    start_drivnx: StartDrivnX, tmp_path: Path, this_computers_store: RealStore | None,
    postgres_server: str,
) -> None:
    # CFX-APP-postgres/AC-30 through the API: a refused password, the right one saved, a
    # restart, a stored password the server now refuses, a wrong one and the right one again.
    # With this computer's store where it has one, else with the password in memory (AC-05).
    store = this_computers_store
    planted = secrets.token_urlsafe(18)  # 24 random characters, refused by the server
    refused_later = secrets.token_urlsafe(18)  # stored, as if the password changed since
    right = postgres_fixture.QUOTES_PASSWORD
    run = ("--no-browser", "--log-level", "trace")
    env = {} if store else NO_STORE
    directory = tmp_path / "workspace"

    before = start_drivnx(*run, env=env, workspace=directory)
    first = sign_in(before.wait_for_launch())
    wrong = first.post("/api/sources/postgres", json={**QUOTES, "password": planted})
    assert wrong.status_code == 422
    source = stored(first.post("/api/sources/postgres", json=QUOTES).json())
    if store:
        store.written.append(entry(source))
    before.process.terminate()
    before.process.wait(timeout=30)
    if store:
        store.set(*entry(source), refused_later)
    after = start_drivnx(*run, env=env, workspace=directory)
    second = sign_in(after.wait_for_launch())
    if store:
        assert second.post(f"/api/sources/{source.id}/connect").status_code == 422
    password = f"/api/sources/{source.id}/password"
    assert second.post(password, json={"password": planted}).status_code == 422
    assert second.post(password, json={"password": right}).status_code == 200
    after.process.terminate()
    after.process.wait(timeout=30)

    output = (before.all_output() + after.all_output()).encode("utf-8")
    # uvicorn's trace lines, so the logs were at their most detailed.
    assert "ASGI [" in output.decode()
    written = [path for path in directory.rglob("*") if path.is_file()]
    assert any(path.name == "workspace.db" for path in written)
    contents = {path.name: path.read_bytes() for path in written}
    for secret in (right, planted, refused_later):
        for part in parts_of(secret):
            assert part not in output
            for name, content in contents.items():
                assert part not in content, name


def test_a_restart_reads_the_password_from_the_os_store(
    start_drivnx: StartDrivnX, tmp_path: Path, real_store: RealStore, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-04 with DrivnX as a user runs it, and this computer's own store.
    workspace = tmp_path / "workspace"
    before = start_drivnx("--no-browser", workspace=workspace)
    saved = sign_in(before.wait_for_launch()).post("/api/sources/postgres", json=DETAILS).json()
    source = stored(saved)
    real_store.written.append(entry(source))
    before.process.terminate()
    before.process.wait(timeout=30)

    after = sign_in(start_drivnx("--no-browser", workspace=workspace).wait_for_launch())

    (listed,) = after.get("/api/sources").json()
    assert (listed["password_kept"], listed["password_needed"]) == (CREDENTIAL_STORE, False)
    assert after.post(f"/api/sources/{source.id}/connect").status_code == 200


def test_connecting_needs_a_database_whose_password_is_known(
    start_app: StartApp, tmp_path: Path, postgres_server: str
) -> None:
    # CFX-APP-postgres/AC-05: without a store, after a restart, the password is asked for first.
    source_id = start_app(None).post("/api/sources/postgres", json=DETAILS).json()["id"]
    after = start_app(None)
    csv = tmp_path / "people.csv"
    csv.write_text("id\n1\n", encoding="utf-8")
    file_id = after.post("/api/sources", json={"path": str(csv)}).json()["id"]

    for connecting, status, says in (
        (source_id, 409, "Enter the password for this database first."),
        (file_id, 409, "A file has no password."),
        ("0" * 32, 404, "There's no such source."),
    ):
        response = after.post(f"/api/sources/{connecting}/connect")

        assert (response.status_code, response.json()) == (status, {"detail": says})
