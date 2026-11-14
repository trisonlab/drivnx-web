"""Saving a PostgreSQL source beside the CSV ones, CFX-APP-postgres AC-02 (DRIVN-47)."""

import shutil
import sqlite3
from pathlib import Path

import pytest
from artifacts.sources import (
    StoredPostgresSource,
    add_postgres_source,
    find_source,
    find_source_by_connection,
    list_sources,
)
from artifacts.workspace import MIGRATIONS_DIR, open_workspace

FIXTURE = StoredPostgresSource(
    id="a" * 32,
    host="127.0.0.1",
    port=54329,
    database="drivnx_fixture",
    user="drivnx_fixture",
    tls_mode="prefer",
    ca_path=None,
)

# A CSV source as the schema before PostgreSQL stored it, with a run that measured it.
BEFORE_POSTGRES = [
    "INSERT INTO source (id, kind, path, delimiter, encoding, has_header, size_bytes,"
    " modified_at, skip_lines, corrected)"
    " VALUES ('s1', 'csv', '/data/a.csv', ',', 'UTF-8', 1, 10, '2026-01-01T00:00:00+00:00',"
    " 2, 1)",
    "INSERT INTO source (id, kind, path, delimiter, encoding, has_header, size_bytes,"
    " modified_at) VALUES ('s2', 'csv', '/data/b.csv', ';', 'Latin-1', 0, 20,"
    " '2026-01-02T00:00:00+00:00')",
    "INSERT INTO dataset (id, source_id, name) VALUES ('d1', 's1', 'a.csv')",
    "INSERT INTO field (id, dataset_id, position, name, physical_type, normalised_type)"
    " VALUES ('f1', 'd1', 0, 'id', 'BIGINT', 'integer')",
    "INSERT INTO profile_run (id, scope, mode, status, started_at)"
    " VALUES ('r1', '{\"dataset_id\": \"d1\"}', 'fast', 'completed', '2026-01-03T00:00:00+00:00')",
    "INSERT INTO observation (id, profile_run_id, dataset_id, field_id, metric, value, scope,"
    " method, precision) VALUES ('o1', 'r1', 'd1', 'f1', 'null_count', '0', 'sample', 'count',"
    " 'exact')",
]
TABLES = ("source", "dataset", "field", "profile_run", "observation")
NEW_SOURCE_COLUMNS = {"host", "port", "database_name", "user_name", "tls_mode", "ca_path"}


def rows(database: Path, table: str) -> list[tuple[object, ...]]:
    connection = sqlite3.connect(database)
    try:
        columns = [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        # Only the columns the schema before PostgreSQL had, in their order.
        kept = [column for column in columns if column not in NEW_SOURCE_COLUMNS]
        return connection.execute(
            f"SELECT {', '.join(kept)} FROM {table} ORDER BY rowid"
        ).fetchall()
    finally:
        connection.close()


def test_a_workspace_from_before_postgres_keeps_every_csv_source_and_run(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-02: migration 0007 rebuilds `source`, which every dataset refers to.
    before = tmp_path / "migrations"
    shutil.copytree(
        MIGRATIONS_DIR, before, ignore=shutil.ignore_patterns("0007_*", "__pycache__")
    )
    directory = tmp_path / "workspace"
    open_workspace(directory, before).close()
    database = directory / "workspace.db"
    connection = sqlite3.connect(database)
    with connection:
        for statement in BEFORE_POSTGRES:
            connection.execute(statement)
    connection.close()
    expected = {table: rows(database, table) for table in TABLES}

    workspace = open_workspace(directory)
    try:
        listed = list_sources(workspace.engine)
    finally:
        workspace.close()

    assert {table: rows(database, table) for table in TABLES} == expected
    assert [source.id for source in listed] == ["s1", "s2"]
    assert listed[0].skip_lines == 2 and listed[0].corrected


def test_a_postgres_source_is_kept_after_a_restart_with_nowhere_for_a_password(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-02: a saved source stays after a restart, and only the password is a
    # secret, which `workspace.db` has no column for.
    directory = tmp_path / "workspace"
    workspace = open_workspace(directory)
    try:
        assert add_postgres_source(workspace.engine, FIXTURE)
    finally:
        workspace.close()

    workspace = open_workspace(directory)
    try:
        assert list_sources(workspace.engine) == [FIXTURE]
        assert find_source(workspace.engine, FIXTURE.id) == FIXTURE
    finally:
        workspace.close()
    connection = sqlite3.connect(directory / "workspace.db")
    columns = [row[1] for row in connection.execute("PRAGMA table_info(source)")]
    connection.close()
    assert not [column for column in columns if "password" in column or "secret" in column]


def test_the_same_connection_saved_twice_is_kept_once(tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-02: as connecting a CSV path again gives the source it already is.
    workspace = open_workspace(tmp_path / "workspace")
    try:
        assert add_postgres_source(workspace.engine, FIXTURE)
        again = StoredPostgresSource(**{**FIXTURE.__dict__, "id": "f" * 32, "tls_mode": "require"})

        assert not add_postgres_source(workspace.engine, again)
        assert list_sources(workspace.engine) == [FIXTURE]
        assert find_source_by_connection(
            workspace.engine, "127.0.0.1", 54329, "drivnx_fixture", "drivnx_fixture"
        ) == FIXTURE
        assert find_source_by_connection(
            workspace.engine, "127.0.0.1", 54329, "drivnx_fixture", "someone_else"
        ) is None
    finally:
        workspace.close()


@pytest.mark.parametrize(
    "values",
    [
        # Each kind has its own columns, and none of the other kind's.
        "('x', 'postgres', '/data/c.csv', NULL, NULL, NULL, NULL, NULL,"
        " 'h', 5432, 'db', 'u', 'prefer', NULL)",
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " NULL, 5432, 'db', 'u', 'prefer', NULL)",
        "('x', 'csv', '/data/c.csv', ',', 'UTF-8', 1, 1, 't',"
        " 'h', 5432, 'db', 'u', 'prefer', NULL)",
        "('x', 'csv', '/data/c.csv', NULL, 'UTF-8', 1, 1, 't',"
        " NULL, NULL, NULL, NULL, NULL, NULL)",
        "('x', 'mysql', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 5432, 'db', 'u', 'prefer', NULL)",
        # The TLS modes the form offers, and a CA certificate exactly when verify-full needs one.
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 5432, 'db', 'u', 'disable', NULL)",
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 5432, 'db', 'u', 'verify-full', NULL)",
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 5432, 'db', 'u', 'prefer', '/ca.pem')",
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 0, 'db', 'u', 'prefer', NULL)",
        "('x', 'postgres', NULL, NULL, NULL, NULL, NULL, NULL,"
        " 'h', 65536, 'db', 'u', 'prefer', NULL)",
    ],
)
def test_a_source_row_that_mixes_kinds_or_settings_is_refused(
    tmp_path: Path, values: str
) -> None:
    # CFX-APP-postgres/AC-02: the database refuses what the code must never write.
    directory = tmp_path / "workspace"
    open_workspace(directory).close()
    connection = sqlite3.connect(directory / "workspace.db")
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO source (id, kind, path, delimiter, encoding, has_header,"
                " size_bytes, modified_at, host, port, database_name, user_name, tls_mode,"
                f" ca_path) VALUES {values}"
            )
    finally:
        connection.close()


def test_a_verify_full_source_keeps_its_ca_certificate_path(tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-01: the CA certificate file is saved as a path, not a secret.
    verified = StoredPostgresSource(
        **{**FIXTURE.__dict__, "tls_mode": "verify-full", "ca_path": "/certs/ca.pem"}
    )
    workspace = open_workspace(tmp_path / "workspace")
    try:
        assert add_postgres_source(workspace.engine, verified)
        assert find_source(workspace.engine, verified.id) == verified
    finally:
        workspace.close()
