"""How the workspace database is written to (ADR 0002 point 3)."""

import sqlite3
import threading
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import Connection, Engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as NoConnectionFree

BUSY_TIMEOUT_MILLISECONDS = 5000
_WRITE = "conflux_write"
_BUSY = "Conflux is busy writing to this workspace. Try again."


class WorkspaceBusy(Exception):
    """A write gave up, after waiting for its turn, a connection or SQLite's write lock."""


class _Turns:
    """Writers take turns in the order they arrived.

    SQLite's waiters poll, with sleeps of up to 100 ms, and a busy writer's gap between transactions
    is microseconds. So `busy_timeout` alone can run out while a review waits behind a profile run.
    ADR 0001 means every writer is in this process, so they can queue here instead.
    """

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._waiting: deque[threading.Lock] = deque()
        self._taken = False

    def wait(self) -> None:
        with self._guard:
            if not self._taken:
                self._taken = True
                return
            turn = threading.Lock()
            turn.acquire()
            self._waiting.append(turn)
        if turn.acquire(timeout=BUSY_TIMEOUT_MILLISECONDS / 1000):
            return
        with self._guard:
            if turn in self._waiting:
                self._waiting.remove(turn)
                raise WorkspaceBusy(_BUSY)
        # `finish` handed this writer its turn just as the wait ran out, so it still has it.

    def finish(self) -> None:
        with self._guard:
            if self._waiting:
                self._waiting.popleft().release()
            else:
                self._taken = False


_turns: WeakKeyDictionary[Engine, _Turns] = WeakKeyDictionary()


def use_explicit_transactions(engine: Engine) -> None:
    """Issue BEGIN here, because the Python driver would otherwise start a deferred one late."""
    _turns[engine] = _Turns()

    @event.listens_for(engine, "connect")
    def stop_the_driver_beginning(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None

    @event.listens_for(engine, "begin")
    def begin(connection: Connection) -> None:
        writing = connection.get_execution_options().get(_WRITE, False)
        connection.exec_driver_sql("BEGIN IMMEDIATE" if writing else "BEGIN")


@contextmanager
def write_transaction(engine: Engine) -> Iterator[Connection]:
    """A transaction that holds the write lock from the start, for reading then writing.

    A deferred one takes the lock at its first write. If someone committed since it read, SQLite
    returns SQLITE_BUSY at once and `busy_timeout` doesn't help. Holding the lock from the start
    also means two clicks on Accept can't both pass the check.
    """
    turns = _turns[engine]
    turns.wait()
    try:
        with engine.execution_options(**{_WRITE: True}).begin() as connection:
            yield connection
    except NoConnectionFree as error:
        raise WorkspaceBusy(_BUSY) from error
    except OperationalError as error:
        # The low byte is the basic code, whichever kind of SQLITE_BUSY this is.
        code = getattr(error.orig, "sqlite_errorcode", None)
        if code is None or code & 0xFF != sqlite3.SQLITE_BUSY:
            raise
        raise WorkspaceBusy(_BUSY) from error
    finally:
        turns.finish()


def empty_the_wal(engine: Engine) -> None:
    """Move everything into the database and empty the WAL, which still holds old versions of pages.

    It waits for readers up to `busy_timeout`. If one outlasts that, the old pages stay until a
    later checkpoint, which may not happen until the workspace closes.
    """
    # Not `engine.connect()`, which begins a transaction, and SQLite can't checkpoint inside one.
    connection = engine.raw_connection()
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
    finally:
        connection.close()
