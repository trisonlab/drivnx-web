"""SQLite settings and migrations, CFX-APP-first-slice AC-19 and AC-44 (ADR 0002 points 5 and 6)."""

import errno
import hashlib
import os
import shutil
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from textwrap import dedent, indent

import pytest
from artifacts.workspace import (
    MIGRATIONS_DIR,
    Workspace,
    WorkspaceError,
    open_workspace,
)

from ..conftest import StartConflux, posix_only

CREATE_FAMILY = """
op.execute("CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT)")
op.execute(
    "CREATE TABLE child (id INTEGER PRIMARY KEY, "
    "parent_id INTEGER NOT NULL REFERENCES parent(id){on_delete})"
)
op.execute("INSERT INTO parent VALUES (1, 'kept')")
op.execute("INSERT INTO child VALUES (10, 1)")
"""
REBUILD_PARENT = """
with op.batch_alter_table("parent", recreate="always") as batch:
    batch.add_column(sa.Column("extra", sa.Text()))
"""


def add_migration(
    directory: Path, revision: str, parent: str | None, upgrade: str
) -> Path:
    versions = directory / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    shutil.copy(MIGRATIONS_DIR / "env.py", directory / "env.py")
    (versions / f"{revision}.py").write_text(
        "import sqlalchemy as sa\n"
        "from alembic import op\n\n"
        f"revision = {revision!r}\n"
        f"down_revision = {parent!r}\n"
        "branch_labels = None\n"
        "depends_on = None\n\n\n"
        "def upgrade() -> None:\n" + indent(upgrade.strip() + "\n", "    ") + "\n\n"
        "def downgrade() -> None:\n    pass\n"
    )
    return directory


def read(database: Path, query: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        return connection.execute(query).fetchall()
    finally:
        connection.close()


def backups(workspace: Path) -> list[str]:
    return sorted(entry.name for entry in workspace.glob("workspace.db.bak-*"))


def fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.fixture
def workspace_dir(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.fixture
def migrations(tmp_path: Path) -> Path:
    return tmp_path / "migrations"


@pytest.fixture
def opened(workspace_dir: Path) -> Iterator[Workspace]:
    workspace = open_workspace(workspace_dir)
    yield workspace
    workspace.close()


def reopen(directory: Path, migrations: Path) -> None:
    open_workspace(directory, migrations).close()


@pytest.mark.parametrize(
    ("pragma", "expected"),
    [
        ("foreign_keys", 1),
        ("journal_mode", "wal"),
        # 2 is FULL.
        ("synchronous", 2),
        ("busy_timeout", 5000),
    ],
)
def test_connections_use_the_settings_in_adr_0002(
    opened: Workspace, pragma: str, expected: object
) -> None:
    with opened.engine.connect() as connection:
        assert connection.exec_driver_sql(f"PRAGMA {pragma}").scalar() == expected


def test_a_new_workspace_is_at_the_latest_schema_and_has_no_backup(
    opened: Workspace, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-19: there's nothing to back up yet.
    assert read(
        workspace_dir / "workspace.db", "SELECT version_num FROM alembic_version"
    )
    assert backups(workspace_dir) == []


@posix_only
def test_an_older_schema_is_backed_up_owner_only_and_migrated(
    workspace_dir: Path, migrations: Path
) -> None:
    # CFX-APP-first-slice/AC-19
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=""))
    reopen(workspace_dir, migrations)
    add_migration(
        migrations, "r2", "r1", "op.execute('CREATE TABLE added (id INTEGER)')"
    )

    previous_umask = os.umask(0)
    try:
        reopen(workspace_dir, migrations)
    finally:
        os.umask(previous_umask)

    database = workspace_dir / "workspace.db"
    assert read(database, "SELECT version_num FROM alembic_version") == [("r2",)]
    assert read(database, "SELECT name FROM parent") == [("kept",)]
    backup = workspace_dir / "workspace.db.bak-r1"
    assert mode(backup) == 0o600
    assert read(backup, "SELECT version_num FROM alembic_version") == [("r1",)]
    assert read(backup, "SELECT name FROM sqlite_master WHERE name = 'added'") == []
    assert read(backup, "SELECT name FROM parent") == [("kept",)]


def test_no_backup_is_made_when_nothing_needs_migrating(
    workspace_dir: Path, migrations: Path
) -> None:
    # CFX-APP-first-slice/AC-19
    add_migration(migrations, "r1", None, "op.execute('CREATE TABLE t (id INTEGER)')")
    reopen(workspace_dir, migrations)
    reopen(workspace_dir, migrations)

    assert backups(workspace_dir) == []


def test_only_the_last_three_backups_are_kept(
    workspace_dir: Path, migrations: Path
) -> None:
    # ADR 0002 point 6
    parent = None
    for number in range(1, 6):
        revision = f"r{number}"
        add_migration(
            migrations,
            revision,
            parent,
            f"op.execute('CREATE TABLE t{number} (id INTEGER)')",
        )
        reopen(workspace_dir, migrations)
        parent = revision

    assert backups(workspace_dir) == [
        "workspace.db.bak-r2",
        "workspace.db.bak-r3",
        "workspace.db.bak-r4",
    ]


def test_a_newer_schema_is_refused_and_the_file_is_unchanged(
    start_conflux: StartConflux, workspace_dir: Path
) -> None:
    # CFX-APP-first-slice/AC-19, through the real command and its own migrations.
    first = start_conflux("--no-browser", workspace=workspace_dir)
    first.wait_for_launch()
    first.process.terminate()
    first.process.wait(timeout=10)
    database = workspace_dir / "workspace.db"
    connection = sqlite3.connect(database)
    connection.execute("UPDATE alembic_version SET version_num = 'from-the-future'")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    before = fingerprint(database)

    second = start_conflux("--no-browser", workspace=workspace_dir)

    assert second.process.wait(timeout=10) == 1
    output = second.all_output()
    assert "newer version of Conflux" in output
    assert "Traceback" not in output
    assert fingerprint(database) == before
    assert backups(workspace_dir) == []


@pytest.mark.parametrize("on_delete", ["", " ON DELETE CASCADE"])
def test_a_batch_migration_keeps_rows_that_reference_the_rebuilt_table(
    workspace_dir: Path, migrations: Path, on_delete: str
) -> None:
    # CFX-APP-first-slice/AC-44: with foreign keys on, this deleted the child row or raised an error.
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=on_delete))
    reopen(workspace_dir, migrations)
    add_migration(migrations, "r2", "r1", REBUILD_PARENT)

    reopen(workspace_dir, migrations)

    database = workspace_dir / "workspace.db"
    assert read(database, "SELECT id, parent_id FROM child") == [(10, 1)]
    assert read(database, "SELECT id, name, extra FROM parent") == [(1, "kept", None)]
    assert read(database, "SELECT version_num FROM alembic_version") == [("r2",)]


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("op.execute('INSERT INTO child VALUES (11, 999)')", "foreign key"),
        ("raise RuntimeError('migration went wrong')", "migration went wrong"),
    ],
)
def test_a_failed_migration_rolls_back_everything(
    workspace_dir: Path, migrations: Path, failure: str, message: str
) -> None:
    # CFX-APP-first-slice/AC-44, and the backup is there too.
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=""))
    reopen(workspace_dir, migrations)
    add_migration(
        migrations,
        "r2",
        "r1",
        f"op.execute('CREATE TABLE half_done (id INTEGER)')\n"
        f"op.execute(\"UPDATE parent SET name = 'changed'\")\n{failure}",
    )

    with pytest.raises(WorkspaceError, match=message):
        reopen(workspace_dir, migrations)

    database = workspace_dir / "workspace.db"
    assert read(database, "SELECT version_num FROM alembic_version") == [("r1",)]
    assert (
        read(database, "SELECT name FROM sqlite_master WHERE name = 'half_done'") == []
    )
    assert read(database, "SELECT name FROM parent") == [("kept",)]
    assert read(database, "SELECT id FROM child") == [(10,)]
    assert backups(workspace_dir) == ["workspace.db.bak-r1"]


def test_a_failed_migration_releases_the_lock(
    workspace_dir: Path, migrations: Path
) -> None:
    add_migration(migrations, "r1", None, "raise RuntimeError('nope')")

    with pytest.raises(WorkspaceError):
        reopen(workspace_dir, migrations)
    add_migration(migrations, "r1", None, "op.execute('CREATE TABLE t (id INTEGER)')")

    reopen(workspace_dir, migrations)


@pytest.mark.parametrize(
    "contents", [b"this is not a database", b"SQLite format 3\x00" + b"\x00" * 200]
)
def test_a_file_that_is_not_a_database_is_refused_and_unchanged(
    workspace_dir: Path, contents: bytes
) -> None:
    workspace_dir.mkdir()
    database = workspace_dir / "workspace.db"
    database.write_bytes(contents)

    with pytest.raises(WorkspaceError, match="isn't a Conflux workspace"):
        open_workspace(workspace_dir)

    assert database.read_bytes() == contents
    assert sorted(entry.name for entry in workspace_dir.iterdir()) == ["workspace.db"]


def test_someone_elses_database_with_the_same_name_is_refused_and_unchanged(
    workspace_dir: Path,
) -> None:
    workspace_dir.mkdir()
    database = workspace_dir / "workspace.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE their_data (id INTEGER)")
    connection.commit()
    connection.close()
    before = fingerprint(database)

    with pytest.raises(WorkspaceError, match="isn't a Conflux workspace"):
        open_workspace(workspace_dir)

    assert fingerprint(database) == before
    assert sorted(entry.name for entry in workspace_dir.iterdir()) == ["workspace.db"]


@posix_only
def test_an_unwritable_location_gets_a_clear_error(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        with pytest.raises(WorkspaceError, match="Can't open"):
            open_workspace(locked / "workspace")
    finally:
        locked.chmod(0o700)


def test_a_relative_path_is_resolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    workspace = open_workspace(Path("relative"))
    workspace.close()

    assert workspace.directory == tmp_path / "relative"
    assert (tmp_path / "relative" / "workspace.db").is_file()


def test_a_crash_during_a_transaction_does_not_lock_the_user_out(
    workspace_dir: Path,
) -> None:
    # ADR 0002 "reopen intact", found in the T03 review. A crash in rollback-journal mode leaves a hot
    # journal, and a read-only open can't recover from it, so the workspace was refused every time.
    workspace_dir.mkdir()
    database = workspace_dir / "workspace.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
    connection.execute("INSERT INTO alembic_version VALUES ('0001')")
    connection.execute("CREATE TABLE kept (x)")
    connection.execute("INSERT INTO kept VALUES (1), (2), (3)")
    connection.commit()
    connection.close()
    crash = f"""
    import os, sqlite3
    connection = sqlite3.connect({str(database)!r}, isolation_level=None)
    connection.execute("PRAGMA cache_size = 1")
    connection.execute("BEGIN IMMEDIATE")
    connection.execute(
        "WITH RECURSIVE n(i) AS (SELECT 1 UNION ALL SELECT i + 1 FROM n WHERE i < 3000) "
        "INSERT INTO kept SELECT hex(randomblob(2000)) FROM n"
    )
    os._exit(0)
    """
    subprocess.run([sys.executable, "-I", "-c", dedent(crash)], check=True)
    assert (workspace_dir / "workspace.db-journal").exists()

    workspace = open_workspace(workspace_dir)
    workspace.close()

    assert read(database, "SELECT count(*) FROM kept") == [(3,)]
    assert not (workspace_dir / "workspace.db-journal").exists()


@posix_only
def test_an_old_damaged_backup_is_replaced(
    workspace_dir: Path, migrations: Path
) -> None:
    # Found in the T03 review: this raised a DatabaseError traceback, and the workspace couldn't open.
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=""))
    reopen(workspace_dir, migrations)
    add_migration(
        migrations, "r2", "r1", "op.execute('CREATE TABLE added (id INTEGER)')"
    )
    backup = workspace_dir / "workspace.db.bak-r1"
    backup.write_bytes(b"damaged" * 1000)

    reopen(workspace_dir, migrations)

    assert read(backup, "SELECT version_num FROM alembic_version") == [("r1",)]
    assert read(backup, "SELECT name FROM parent") == [("kept",)]
    assert mode(backup) == 0o600


def test_a_backup_that_cannot_be_made_stops_the_migration(
    workspace_dir: Path, migrations: Path
) -> None:
    # CFX-APP-first-slice/AC-19: no backup, no migration.
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=""))
    reopen(workspace_dir, migrations)
    add_migration(
        migrations, "r2", "r1", "op.execute('CREATE TABLE added (id INTEGER)')"
    )
    (workspace_dir / "workspace.db.bak-r1").mkdir()

    with pytest.raises(WorkspaceError, match="nothing was changed"):
        reopen(workspace_dir, migrations)

    database = workspace_dir / "workspace.db"
    assert read(database, "SELECT version_num FROM alembic_version") == [("r1",)]


@posix_only
def test_wal_files_are_owner_only_whatever_the_umask(workspace_dir: Path) -> None:
    # CFX-APP-first-slice/AC-17: these files only exist while a connection is writing.
    previous_umask = os.umask(0)
    try:
        workspace = open_workspace(workspace_dir)
        try:
            with workspace.engine.connect() as connection:
                connection.exec_driver_sql("CREATE TABLE t (x)")
                connection.commit()
                assert mode(workspace_dir / "workspace.db-wal") == 0o600
                assert mode(workspace_dir / "workspace.db-shm") == 0o600
        finally:
            workspace.close()
    finally:
        os.umask(previous_umask)


@posix_only
def test_opening_tightens_loose_permissions(workspace_dir: Path) -> None:
    # The "workspace copied or stolen" threat: a copy can arrive with the umask's permissions.
    open_workspace(workspace_dir).close()
    database = workspace_dir / "workspace.db"
    backup = workspace_dir / "workspace.db.bak-old"
    backup.write_bytes(b"")
    stale_wal = workspace_dir / "workspace.db-wal"
    stale_wal.write_bytes(b"")
    lock = workspace_dir / ".conflux.lock"
    notes = workspace_dir / "notes.txt"
    notes.write_text("the user's own file")
    notes.chmod(0o644)
    workspace_dir.chmod(0o755)
    for path in (database, backup, stale_wal, lock):
        path.chmod(0o644)

    open_workspace(workspace_dir).close()

    assert mode(workspace_dir) == 0o700
    assert mode(database) == 0o600
    assert mode(backup) == 0o600
    assert mode(lock) == 0o600
    # Conflux only touches its own files.
    assert mode(notes) == 0o644


@posix_only
def test_a_lock_failure_is_only_called_already_open_when_it_is_held(
    workspace_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A filesystem without locks is a different problem, with a different fix.
    import fcntl

    def no_locks(_descriptor: int, _operation: int) -> None:
        raise OSError(errno.ENOLCK, "No locks available")

    monkeypatch.setattr(fcntl, "flock", no_locks)

    with pytest.raises(WorkspaceError, match="Can't open .*No locks available"):
        open_workspace(workspace_dir)


def test_a_failed_migration_keeps_its_values_out_of_the_error(
    workspace_dir: Path, migrations: Path
) -> None:
    # Section 7: this text reaches the terminal, and a migration can carry personal data.
    add_migration(migrations, "r1", None, CREATE_FAMILY.format(on_delete=""))
    reopen(workspace_dir, migrations)
    add_migration(
        migrations,
        "r2",
        "r1",
        "op.get_bind().execute(\n"
        "    sa.text('INSERT INTO no_such_table (name) VALUES (:name)'),\n"
        "    {'name': 'jane_doe_salaries'},\n"
        ")",
    )

    with pytest.raises(WorkspaceError, match="no_such_table") as failure:
        reopen(workspace_dir, migrations)

    assert "jane_doe_salaries" not in str(failure.value)
