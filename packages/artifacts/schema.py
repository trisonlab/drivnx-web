"""Brings a workspace database up to the code's schema (ADR 0002 point 6)."""

import os
import sqlite3
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, event
from sqlalchemy.engine import URL, Connection
from sqlalchemy.pool import NullPool

OWNER_ONLY_FILE = 0o600
BACKUPS_KEPT = 3
VERSION_TABLE = "alembic_version"


class WorkspaceError(Exception):
    """The workspace can't be opened. The message is written for the user."""


def create_owner_only_file(path: Path) -> None:
    """SQLite would create the file using the umask, usually 0644, so Conflux creates it first."""
    os.close(os.open(path, os.O_CREAT | os.O_WRONLY, OWNER_ONLY_FILE))


def read_revision(database: Path) -> str | None:
    """Return the schema revision, or None for an empty file.

    This only reads, but it can't open the file read-only: after a crash SQLite has to write to
    recover, and a read-only connection would fail on that forever.
    """
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=rw", uri=True)
        try:
            if not connection.execute("SELECT 1 FROM sqlite_master").fetchone():
                return None
            row = connection.execute(
                f"SELECT version_num FROM {VERSION_TABLE}"
            ).fetchone()
            return row[0] if row else None
        finally:
            connection.close()
    except sqlite3.DatabaseError as error:
        raise WorkspaceError(
            f"{database} isn't a Conflux workspace: {error}."
        ) from None


def _back_up(database: Path, revision: str) -> None:
    backup_path = database.with_name(f"{database.name}.bak-{revision}")
    # An earlier attempt may have left a file here, and it may be damaged.
    backup_path.unlink(missing_ok=True)
    # Created first, so it is never readable by others, even for a moment.
    create_owner_only_file(backup_path)
    source = sqlite3.connect(database)
    target = sqlite3.connect(backup_path)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()
    backups = sorted(
        database.parent.glob(f"{database.name}.bak-*"),
        key=lambda path: path.stat().st_mtime_ns,
    )
    for old in backups[:-BACKUPS_KEPT]:
        old.unlink()


def _upgrade(database: Path, config: Config) -> None:
    """Upgrade in one transaction, with foreign keys off for this connection only.

    Batch mode rebuilds a table and drops the old one. With foreign keys on, that drop would delete
    the rows referencing it, or fail. The pragma does nothing inside a transaction, so it is set when
    the connection opens. The Python driver doesn't start transactions around DDL, so `BEGIN` is explicit.
    """
    engine = create_engine(
        URL.create("sqlite", database=str(database)), poolclass=NullPool
    )

    @event.listens_for(engine, "connect")
    def on_connect(dbapi_connection: Any, _record: Any) -> None:
        dbapi_connection.isolation_level = None
        dbapi_connection.execute("PRAGMA foreign_keys = OFF")

    @event.listens_for(engine, "begin")
    def on_begin(connection: Connection) -> None:
        connection.exec_driver_sql("BEGIN IMMEDIATE")

    try:
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
            violations = connection.exec_driver_sql(
                "PRAGMA foreign_key_check"
            ).fetchall()
            if violations:
                raise WorkspaceError(f"foreign key violations: {violations[:5]}")
    finally:
        engine.dispose()


def migrate(database: Path, migrations_dir: Path) -> None:
    config = Config()
    # Alembic's config treats % as interpolation, and a path may contain one.
    config.set_main_option("script_location", str(migrations_dir).replace("%", "%%"))
    script = ScriptDirectory.from_config(config)

    current = read_revision(database)
    if current is not None:
        try:
            script.get_revision(current)
        except CommandError:
            raise WorkspaceError(
                f"{database.parent} was last used by a newer version of Conflux. "
                "Update Conflux to open it."
            ) from None
    if current == script.get_current_head():
        return

    try:
        if current is not None:
            _back_up(database, current)
        _upgrade(database, config)
    except Exception as error:
        raise WorkspaceError(
            f"Updating {database.parent} failed and nothing was changed: {error}"
        ) from error
