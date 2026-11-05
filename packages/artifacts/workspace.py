import errno
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError

from artifacts.schema import (
    WorkspaceError,
    create_owner_only_file,
    migrate,
    read_revision,
)
from artifacts.store import BUSY_TIMEOUT_MILLISECONDS, use_explicit_transactions

DATABASE_NAME = "workspace.db"
LOCK_NAME = ".conflux.lock"
DUCKDB_TEMP_NAME = "duckdb-tmp"
LOCK_HELD_ERRORS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}
OWNER_ONLY_DIRECTORY = 0o700
OWNER_ONLY_FILE = 0o600
MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SYNC_FOLDER_NAMES = {
    "dropbox",
    "google drive",
    "googledrive",
    "icloud drive",
    "iclouddrive",
    "mobile documents",
    "cloudstorage",
}


@dataclass
class Workspace:
    directory: Path
    engine: Engine
    _lock_descriptor: int

    @property
    def temp_directory(self) -> Path:
        """Where DuckDB spills. Throwaway, but it can hold source data (ADR 0002 point 7)."""
        return self.directory / DUCKDB_TEMP_NAME

    def close(self) -> None:
        self.engine.dispose()
        os.close(self._lock_descriptor)


def default_workspace_directory() -> Path:
    return Path.home() / "Conflux" / "default"


def sync_folder_in(directory: Path) -> str | None:
    """Name the sync folder this directory is inside, if any (ADR 0002 point 5)."""
    for part in directory.resolve().parts:
        name = part.casefold()
        if name in SYNC_FOLDER_NAMES or name.startswith("onedrive"):
            return part
    return None


def _prepare_directory(directory: Path) -> None:
    """Refuse anything but a new or empty directory, or an existing workspace (ADR 0002 point 1)."""
    if directory.exists() and not directory.is_dir():
        raise WorkspaceError(f"{directory} isn't a directory.")
    contents = (
        {entry.name for entry in directory.iterdir()} if directory.exists() else set()
    )
    if DATABASE_NAME in contents:
        # Before anything is written here: this may be someone else's file with the same name.
        read_revision(directory / DATABASE_NAME)
        return
    if contents and contents != {LOCK_NAME}:
        raise WorkspaceError(
            f"{directory} has other files in it and isn't a Conflux workspace. "
            "Choose a new or empty directory, such as a subdirectory of it."
        )
    directory.mkdir(parents=True, exist_ok=True, mode=OWNER_ONLY_DIRECTORY)


def _tighten_permissions(directory: Path) -> None:
    """Make Conflux's own files owner-only. A copied workspace can arrive with looser permissions."""
    directory.chmod(OWNER_ONLY_DIRECTORY)
    for path in [directory / LOCK_NAME, *directory.glob(f"{DATABASE_NAME}*")]:
        path.chmod(OWNER_ONLY_FILE)


def _hold_lock(path: Path) -> int:
    """Take an OS-held lock (ADR 0001), which the OS releases when the process dies, even on a crash."""
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, OWNER_ONLY_FILE)
    try:
        if sys.platform == "win32":
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as error:
        os.close(descriptor)
        if error.errno not in LOCK_HELD_ERRORS:
            raise
        raise WorkspaceError(
            f"{path.parent} is already open in another Conflux. "
            "Use that one, or stop it and start again."
        ) from None
    return descriptor


def _connect_to(database: Path) -> Engine:
    engine = create_engine(
        URL.create("sqlite", database=str(database)),
        hide_parameters=True,  # Section 7: its error text reaches the logs.
        pool_timeout=BUSY_TIMEOUT_MILLISECONDS / 1000,
    )
    use_explicit_transactions(engine)

    @event.listens_for(engine, "connect")
    def apply_settings(dbapi_connection: Any, _record: Any) -> None:
        """ADR 0002 point 5."""
        dbapi_connection.execute("PRAGMA foreign_keys = ON")
        # Freed space is zeroed, so a value that's been replaced can't be read back (ADR 0004).
        dbapi_connection.execute("PRAGMA secure_delete = ON")
        dbapi_connection.execute("PRAGMA journal_mode = WAL")
        dbapi_connection.execute("PRAGMA synchronous = FULL")
        dbapi_connection.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MILLISECONDS}")

    with engine.connect():
        pass
    return engine


def _open_database(directory: Path, migrations_dir: Path) -> Engine:
    database = directory / DATABASE_NAME
    try:
        create_owner_only_file(database)
        migrate(database, migrations_dir)
        # The lock is held, so no job can still be using this. A crash can leave spill files behind.
        temp = directory / DUCKDB_TEMP_NAME
        if temp.exists():
            shutil.rmtree(temp)
        temp.mkdir(mode=OWNER_ONLY_DIRECTORY)
        return _connect_to(database)
    except OSError as error:
        raise WorkspaceError(f"Can't open {directory}: {error.strerror}") from None
    except DBAPIError as error:
        raise WorkspaceError(f"Can't open {directory}: {error.orig}") from None


def open_workspace(directory: Path, migrations_dir: Path = MIGRATIONS_DIR) -> Workspace:
    """Open a workspace and hold its lock until `close`, or until the process ends."""
    directory = directory.resolve()
    try:
        _prepare_directory(directory)
        lock = _hold_lock(directory / LOCK_NAME)
        _tighten_permissions(directory)
    except OSError as error:
        raise WorkspaceError(f"Can't open {directory}: {error.strerror}") from None
    try:
        return Workspace(directory, _open_database(directory, migrations_dir), lock)
    except WorkspaceError:
        os.close(lock)
        raise
