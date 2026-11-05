"""Writing to the workspace, CFX-APP-first-slice AC-41 and AC-48 (ADR 0002 point 3)."""

import sqlite3
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from artifacts import store
from artifacts.settings import WorkspaceSettings, save_settings
from artifacts.sources import StoredField, StoredSource, add_csv_source
from artifacts.store import WorkspaceBusy, write_transaction
from artifacts.workspace import Workspace, open_workspace
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError

from ..conftest import credential_shaped_values


@pytest.fixture
def workspace(tmp_path: Path) -> Iterator[Workspace]:
    opened = open_workspace(tmp_path / "workspace")
    yield opened
    opened.close()


def another_writer_is_locked_out(workspace: Workspace) -> bool:
    other = sqlite3.connect(workspace.directory / "workspace.db", timeout=0)
    try:
        other.execute("BEGIN IMMEDIATE")
        other.rollback()
        return False
    except sqlite3.OperationalError as error:
        assert "locked" in str(error)
        return True
    finally:
        other.close()


def test_a_write_transaction_holds_the_write_lock_before_its_first_write(
    workspace: Workspace,
) -> None:
    # A deferred transaction gets SQLITE_BUSY at its first write if someone committed since it read.
    assert not another_writer_is_locked_out(workspace)

    with write_transaction(workspace.engine) as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1
        assert another_writer_is_locked_out(workspace)

    assert not another_writer_is_locked_out(workspace)


def test_a_failed_write_transaction_rolls_back(workspace: Workspace) -> None:
    with pytest.raises(RuntimeError), write_transaction(workspace.engine) as connection:
        connection.execute(
            text(
                "INSERT INTO workspace_settings (id, name, ai_mode) VALUES (1, 'x', 'off')"
            )
        )
        raise RuntimeError

    assert not another_writer_is_locked_out(workspace)
    with workspace.engine.connect() as connection:
        assert (
            connection.execute(text("SELECT count(*) FROM workspace_settings")).scalar()
            == 0
        )


def test_reads_do_not_take_the_write_lock(workspace: Workspace) -> None:
    # A read has to keep working while a profile run is writing.
    with workspace.engine.connect() as connection:
        connection.execute(text("SELECT count(*) FROM workspace_settings")).scalar()
        assert not another_writer_is_locked_out(workspace)


@pytest.mark.parametrize(
    "write",
    [
        pytest.param(
            lambda engine: save_settings(engine, WorkspaceSettings("Sales", "off")),
            id="settings",
        ),
        pytest.param(
            lambda engine: add_csv_source(
                engine,
                StoredSource(
                    id="s1",
                    path=Path("/data/a.csv"),
                    delimiter=",",
                    encoding="UTF-8",
                    has_header=True,
                    size_bytes=1,
                    modified_at="2024-01-01T00:00:00+00:00",
                    fields=(StoredField("id", "BIGINT", "integer"),),
                ),
            ),
            id="sources",
        ),
    ],
)
def test_existing_writes_use_the_write_lock(workspace: Workspace, write) -> None:
    # From the T04 review: a single statement can't be made to fail here, so the statement is checked.
    statements: list[str] = []

    @event.listens_for(workspace.engine, "before_cursor_execute")
    def record(connection, cursor, statement, parameters, context, executemany) -> None:
        statements.append(statement)

    write(workspace.engine)

    assert statements[0] == "BEGIN IMMEDIATE"


@pytest.mark.parametrize(
    ("value", "shape"),
    [
        (b"postgres://me:hunter2@db/sales", "a password in a URL"),  # ai-skills: allow  # pragma: allowlist secret
        (b'{"password": "hunter2hunter2"}', "a named secret"),  # pragma: allowlist secret
        (b"password=hunter2hunter2", "a named secret"),
        (b"API_KEY: abcdefgh12345678", "a named secret"),
        (b"secret_key=abcdefghijklmnop", "a named secret"),
        (b"aws_secret_access_key=abcdefghijklmnop", "a named secret"),
        (b"SECRET_KEY: abcdefghijklmnop", "a named secret"),
        (b"password=hunter2", "a named secret"),
        (b'{\\"password\\": \\"abcdefghijkl\\"}', "a named secret"),
        (b"Authorization: Basic dXNlcjpwYXNzd29yZA==", "a basic auth header"),
        (b"Authorization: Bearer abcdefghijklmnop1234", "a bearer token"),
        (b"AKIAABCDEFGHIJKLMNOP", "an AWS access key"),  # ai-skills: allow  # pragma: allowlist secret
        (b"sk-abcdefghijklmnopqrstuvwx", "an API key"),  # pragma: allowlist secret
        (b"ghp_" + b"a" * 36, "a GitHub token"),
        (b"-----BEGIN RSA PRIVATE KEY-----", "a private key"),  # ai-skills: allow  # pragma: allowlist secret
    ],
)
def test_the_scan_recognises_credential_shaped_values(value: bytes, shape: str) -> None:
    # CFX-APP-first-slice/AC-48: the scan at the end of every run is only as good as this.
    assert shape in credential_shaped_values(b"padding " + value + b" padding")


def test_the_scan_finds_nothing_in_ordinary_workspace_values() -> None:
    # CFX-APP-first-slice/AC-48: a false alarm would fail every run.
    ordinary = (
        b"/data/secret-name.csv customer_id token_count password_changed_at "
        b"https://example.com/docs 2024-01-15T10:00:00+00:00 " + b"a1b2c3d4" * 4
    )

    assert credential_shaped_values(ordinary) == []


def test_the_scan_finds_a_value_inside_a_real_sqlite_file(tmp_path: Path) -> None:
    # CFX-APP-first-slice/AC-48: not named workspace.db, or the end-of-run scan would rightly fail.
    planted = tmp_path / "planted.sqlite"
    connection = sqlite3.connect(planted)
    with connection:
        connection.execute("CREATE TABLE settings (value TEXT)")
        connection.execute("INSERT INTO settings VALUES ('password=hunter2hunter2')")
    connection.close()

    assert credential_shaped_values(planted.read_bytes()) == ["a named secret"]


def test_writers_take_turns_in_the_order_they_arrived(workspace: Workspace) -> None:
    # CFX-APP-first-slice/AC-41: found building T07. SQLite's waiters poll, and can miss every gap
    # left by a busy writer until busy_timeout runs out, so a review could fail during a profile run.
    finished: list[int] = []

    def writer(number: int) -> None:
        with write_transaction(workspace.engine):
            finished.append(number)

    threads = [threading.Thread(target=writer, args=(number,)) for number in range(6)]
    with write_transaction(workspace.engine):
        for thread in threads:
            thread.start()
            time.sleep(0.05)
    for thread in threads:
        thread.join()

    assert finished == list(range(6))


def test_a_writer_gives_up_if_its_turn_never_comes(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-first-slice/AC-41: a stuck write must give an error, not hang every other request.
    monkeypatch.setattr(store, "BUSY_TIMEOUT_MILLISECONDS", 50)
    errors: list[Exception] = []

    def writer() -> None:
        try:
            with write_transaction(workspace.engine):
                pass
        except WorkspaceBusy as error:
            errors.append(error)

    with write_transaction(workspace.engine):
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join()

    assert len(errors) == 1


def test_a_writer_that_gave_up_does_not_take_a_later_turn(
    workspace: Workspace, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-first-slice/AC-41: its place in the queue must not block whoever is behind it.
    monkeypatch.setattr(store, "BUSY_TIMEOUT_MILLISECONDS", 50)

    def writer() -> None:
        with pytest.raises(WorkspaceBusy), write_transaction(workspace.engine):
            pass

    with write_transaction(workspace.engine):
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join()

    monkeypatch.setattr(store, "BUSY_TIMEOUT_MILLISECONDS", 5000)
    with write_transaction(workspace.engine) as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_a_writer_handed_its_turn_as_its_wait_runs_out_keeps_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CFX-APP-first-slice/AC-41: if that turn were dropped, no writer could ever go again. Holding
    # the guard makes `finish` land between the writer's wait running out and its check of the queue.
    monkeypatch.setattr(store, "BUSY_TIMEOUT_MILLISECONDS", 500)
    turns = store._Turns()
    turns._guard = threading.RLock()
    turns.wait()
    errors: list[WorkspaceBusy] = []

    def writer() -> None:
        try:
            turns.wait()
        except WorkspaceBusy as error:
            errors.append(error)

    thread = threading.Thread(target=writer)
    thread.start()
    while not turns._waiting:
        assert thread.is_alive()
        time.sleep(0.001)
    with turns._guard:
        time.sleep(0.7)
        turns.finish()
    thread.join()

    assert errors == []
    turns.finish()
    turns.wait()
    turns.finish()


def test_a_writer_outside_this_process_gives_workspace_busy(
    workspace: Workspace,
) -> None:
    # Found reviewing T07: SQLite's own timeout gave a raw OperationalError. Takes the full 5 s.
    other = sqlite3.connect(workspace.directory / "workspace.db")
    other.execute("BEGIN IMMEDIATE")
    try:
        with pytest.raises(WorkspaceBusy), write_transaction(workspace.engine):
            pass
    finally:
        other.rollback()
        other.close()

    with write_transaction(workspace.engine) as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_running_out_of_connections_gives_workspace_busy(workspace: Workspace) -> None:
    # Found reviewing T07: the pool waited 30 s, then gave its own error. Takes the full 5 s.
    held = []
    for _ in range(15):
        connection = workspace.engine.connect()
        connection.execute(text("SELECT 1"))
        held.append(connection)
    started = time.monotonic()
    try:
        with pytest.raises(WorkspaceBusy), write_transaction(workspace.engine):
            pass
    finally:
        for connection in held:
            connection.close()

    assert time.monotonic() - started < 6
    with write_transaction(workspace.engine) as connection:
        assert connection.execute(text("SELECT 1")).scalar() == 1


def test_other_database_errors_are_not_called_busy(workspace: Workspace) -> None:
    with (
        pytest.raises(OperationalError, match="no such table"),
        write_transaction(workspace.engine) as connection,
    ):
        connection.execute(text("SELECT * FROM nothing_here"))


def changes_seen(
    workspace: Workspace, history_changes: list[str], statement: str
) -> list[str]:
    """Run this for real and undo it, taking what it finds back so the run's end doesn't fail."""
    already_found = len(history_changes)
    with workspace.engine.connect() as connection:
        connection.exec_driver_sql(statement)
        connection.rollback()
    seen = history_changes[already_found:]
    del history_changes[already_found:]
    return seen


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE observation SET value = 1",
        "update  provenance_event\n set author = 'user' where id = 'x'",
        "UPDATE dictionary_entry_revision SET state = 'confirmed'",
        "UPDATE contract_rule_revision SET params = '{}'",
        "UPDATE contract_revision SET purpose = ''",
        "UPDATE contract_rule_field SET field_id = 'x'",
        "UPDATE finding SET interpretation = ''",
        "UPDATE finding_observation SET position = 1",
        "DELETE FROM observation",
        "delete from provenance_event WHERE id = 'x'",
        "DELETE FROM profile_run",
        "UPDATE profile_run SET mode = 'sample' WHERE id = 'x'",
        "UPDATE profile_run SET status = 'failed', scope = '{}'",
        # Found verifying T08: a pattern over the SQL's text missed all of these.
        "UPDATE OR ABORT observation SET value = 1",
        "UPDATE OR REPLACE provenance_event SET author = 'x'",
        'UPDATE "observation" SET value = 1',
        "UPDATE main.observation SET value = 1",
        "DELETE FROM main.observation",
        'DELETE FROM "provenance_event"',
        "WITH x AS (SELECT 1) UPDATE observation SET value = 1",
        "WITH x AS (SELECT 1) DELETE FROM observation",
        # SQLite's authorizer reports these as inserts, so they're the part read from the text.
        "REPLACE INTO provenance_event (id) SELECT 'x' WHERE 0",
        "INSERT OR REPLACE INTO observation (id) SELECT 'x' WHERE 0",
        "INSERT OR REPLACE INTO \"observation\" (id) SELECT 'x' WHERE 0",
        "REPLACE INTO main.provenance_event (id) SELECT 'x' WHERE 0",
        "WITH x AS (SELECT 1) REPLACE INTO observation (id) SELECT 'x' WHERE 0",
        # Found reviewing T08: more spellings the first REPLACE pattern missed.
        "REPLACE INTO `observation` (id) SELECT 'x' WHERE 0",
        "REPLACE INTO [provenance_event] (id) SELECT 'x' WHERE 0",
        "REPLACE INTO 'observation' (id) SELECT 'x' WHERE 0",
        "INSERT OR REPLACE INTO `observation` (id) SELECT 'x' WHERE 0",
        "REPLACE INTO main . observation (id) SELECT 'x' WHERE 0",
        "REPLACE /* c */ INTO observation (id) SELECT 'x' WHERE 0",
        "INSERT OR REPLACE -- c\n INTO observation (id) SELECT 'x' WHERE 0",
        # Found reviewing T08: ADR 0002 point 4 says rules never lose rows, and an item's subject
        # never changes.
        "DELETE FROM contract_rule",
        "UPDATE contract_rule SET rule_type = 'x'",
        "DELETE FROM dictionary_entry",
        "UPDATE dictionary_entry SET field_id = NULL",
        (
            "INSERT INTO provenance_event (id) SELECT 'x' WHERE 0 "
            "ON CONFLICT (id) DO UPDATE SET author = 'x'"
        ),
    ],
)
def test_the_history_watch_sees_a_change_to_written_history(
    workspace: Workspace, history_changes: list[str], statement: str
) -> None:
    # CFX-APP-first-slice/AC-42: the watch over the whole test run is only as good as this.
    assert changes_seen(workspace, history_changes, statement)


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO provenance_event (id) SELECT 'x' WHERE 0",
        "SELECT * FROM observation",
        "UPDATE profile_run SET status = 'failed', ended_at = 't' WHERE id = 'x'",
        "UPDATE profile_run SET status = 'interrupted' WHERE status = 'running'",
        "UPDATE workspace_settings SET name = 'x'",
        "UPDATE workspace_settings SET name = replace(name, 'a', 'b')",
        "UPDATE contract SET current_version = 2",
        (
            "INSERT INTO workspace_settings (id, name, ai_mode) SELECT 1, 'a', 'off' WHERE 0 "
            "ON CONFLICT (id) DO UPDATE SET name = 'b'"
        ),
        "REPLACE INTO workspace_settings (id, name, ai_mode) SELECT 1, 'a', 'off' WHERE 0",
    ],
)
def test_the_history_watch_allows_everything_else(
    workspace: Workspace, history_changes: list[str], statement: str
) -> None:
    # CFX-APP-first-slice/AC-42: a false alarm would fail every run.
    assert changes_seen(workspace, history_changes, statement) == []
