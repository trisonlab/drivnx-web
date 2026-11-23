"""The PostgreSQL fixture tests run against, DRIVN-45 AC-34 and AC-07."""

import re
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path

import duckdb
import pytest
from connectors.csv_files import duckdb_connection
from connectors.postgres import ConnectionDetails, attach, load_postgres

from .. import postgres_fixture
from ..conftest import REPO_ROOT, find_postgres

COMPOSE = REPO_ROOT / "testdata" / "postgres" / "compose.yaml"

START = "uv run --frozen python tests/postgres_fixture.py start"


def test_a_test_needing_postgres_is_skipped_with_the_command_to_start_it(
    free_port: int,
) -> None:
    # DRIVN-45/AC-34: tests that need the fixture say so when it isn't running.
    with pytest.raises(pytest.skip.Exception, match=START):
        find_postgres(port=free_port, required=False)


def test_a_missing_fixture_fails_instead_where_it_is_required(free_port: int) -> None:
    # DRIVN-45/AC-34: in CI a missing server must not turn the PostgreSQL tests into skips.
    with pytest.raises(pytest.fail.Exception, match=START):
        find_postgres(port=free_port, required=True)


def test_the_compose_fixture_pins_its_image_and_listens_on_loopback_only() -> None:
    # DRIVN-45/AC-34: a tag can be moved to another image, and a digest can't.
    compose = COMPOSE.read_text(encoding="utf-8") if COMPOSE.exists() else ""

    assert re.search(r"image: postgres:\d+\.\d+@sha256:[0-9a-f]{64}\n", compose)
    assert re.findall(r'- "([^"]+:\d+)"', compose) == [
        f"{postgres_fixture.HOST}:{postgres_fixture.PORT}:5432"
    ]


def test_the_fixture_holds_exactly_what_its_sql_built(
    postgres_server: str, tmp_path: Path
) -> None:
    # DRIVN-45/AC-34: its database is built by a committed script from synthetic data.
    with duckdb_connection(tmp_path) as connection:
        load_postgres(connection)
        connection.execute(f"ATTACH '{postgres_server}' AS fixture (TYPE postgres, READ_ONLY)")
        people = connection.execute("SELECT * FROM fixture.public.people ORDER BY id").fetchall()

    assert people == [
        (1, "Ann", date(2024, 1, 5)),
        (2, "Bob", None),
        (3, "Zoé", date(2024, 3, 17)),
    ]


def encrypted(dsn: str, tmp_path: Path) -> bool:
    """Whether the server says the connection DuckDB made with `dsn` is encrypted."""
    with duckdb_connection(tmp_path) as connection:
        load_postgres(connection)
        quoted = dsn.replace("'", "''")
        connection.execute(f"ATTACH '{quoted}' AS fixture (TYPE postgres, READ_ONLY)")
        return connection.execute(
            "SELECT * FROM postgres_query('fixture',"
            " 'SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()')"
        ).fetchone()[0]


def test_the_fixture_encrypts_a_connection_its_own_ca_verifies(
    postgres_server: str, tmp_path: Path
) -> None:
    # DRIVN-45/AC-02 (DRIVN-47): "encrypted" and verify-full need a server with TLS.
    ca = postgres_fixture.CA_CERTIFICATE.as_posix()

    assert encrypted(f"{postgres_server} sslmode=verify-full sslrootcert='{ca}'", tmp_path)


def test_the_fixture_signs_in_its_plain_account_only_without_tls(
    postgres_server: str, tmp_path: Path
) -> None:
    # DRIVN-45/AC-02 and AC-03 (DRIVN-47): with it, "prefer" falls back to a connection
    # that isn't encrypted, and TLS required is refused.
    plain = postgres_fixture.dsn(postgres_fixture.PLAIN_USER, postgres_fixture.PLAIN_PASSWORD)

    assert encrypted(f"{plain} sslmode=prefer", tmp_path) is False
    with pytest.raises(duckdb.IOException, match="SSL"):
        encrypted(f"{plain} sslmode=require", tmp_path)


def test_a_folder_left_by_a_start_that_died_is_removed_and_started_again(
    tmp_path: Path, free_port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The DRIVN-46 review: a folder with nothing listening used to be reported as running.
    stale = tmp_path / "drivnx-postgres-fixture"
    (stale / "data").mkdir(parents=True)
    started: list[bool] = []
    monkeypatch.setattr(postgres_fixture, "OWN_SERVER", stale)
    monkeypatch.setattr(postgres_fixture, "PORT", free_port)
    # Not the running fixture's certificates, which the server would no longer match.
    monkeypatch.setattr(postgres_fixture, "TLS", tmp_path / "tls")
    # Docker and the PostgreSQL install are outside DrivnX: as on GitHub's macOS runner.
    monkeypatch.setattr(postgres_fixture, "_compose_runs_linux", lambda: False)
    monkeypatch.setattr(
        postgres_fixture, "_start_own_server", lambda: started.append(stale.exists())
    )

    postgres_fixture.start()

    assert started == [False]


def test_a_start_that_fails_partway_leaves_no_folder_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The DRIVN-46 review: the next start would otherwise take the folder for a running server.
    monkeypatch.setattr(postgres_fixture, "OWN_SERVER", tmp_path / "drivnx-postgres-fixture")
    monkeypatch.setattr(postgres_fixture, "_binaries", lambda: tmp_path / "no PostgreSQL here")

    with pytest.raises(OSError):
        postgres_fixture._start_own_server()

    assert not (tmp_path / "drivnx-postgres-fixture").exists()


def test_with_no_way_to_start_postgres_the_message_says_what_would_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The DRIVN-46 review: on a machine with Docker but no Compose, and PostgreSQL off the path,
    # "No Docker ... and no PostgreSQL found" was wrong on both counts.
    monkeypatch.delenv("PGBIN", raising=False)
    monkeypatch.setattr(postgres_fixture.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit, match="PGBIN") as stopped:
        postgres_fixture._binaries()

    assert "Compose" in str(stopped.value)


# A role of its own, whose statements the server logs while a test asks it to (DRIVN-58).
LOGGED_ROLE = "drivnx_logged"
LOGGED_PASSWORD = "drivnx-logged"  # pragma: allowlist secret, ai-skills: allow


@pytest.fixture
def logged_role(
    postgres_admin: Callable[[str], None], logging_statements: Callable[[str], None]
) -> Iterator[str]:
    for statement in (
        f"DROP ROLE IF EXISTS {LOGGED_ROLE}",
        f"CREATE ROLE {LOGGED_ROLE} LOGIN PASSWORD '{LOGGED_PASSWORD}'",
    ):
        postgres_admin(statement)
    logging_statements(LOGGED_ROLE)
    try:
        yield LOGGED_ROLE
    finally:
        postgres_admin(f"DROP ROLE {LOGGED_ROLE}")


def first_row(tmp_path: Path, attached: Callable[[duckdb.DuckDBPyConnection], None], sql: str):
    with duckdb_connection(tmp_path) as connection:
        attached(connection)
        return connection.execute(
            "SELECT * FROM postgres_query('source', ?)", [sql]
        ).fetchone()


def test_a_logged_roles_statements_are_in_the_servers_log_with_their_application(
    logged_role: str, tmp_path: Path
) -> None:
    # DRIVN-45/AC-15 needs "the fixture's statement log", each statement with its application.
    read = postgres_fixture.server_log_from_now()
    details = ConnectionDetails(
        host=postgres_fixture.HOST,
        port=postgres_fixture.PORT,
        database=postgres_fixture.DATABASE,
        user=logged_role,
        tls_mode="prefer",
    )

    def as_drivnx(connection: duckdb.DuckDBPyConnection) -> None:
        attach(connection, details, LOGGED_PASSWORD)

    def as_another_application(connection: duckdb.DuckDBPyConnection) -> None:
        load_postgres(connection)
        dsn = postgres_fixture.dsn(logged_role, LOGGED_PASSWORD) + " application_name=elsewhere"
        connection.execute(f"ATTACH '{dsn}' AS source (TYPE postgres, READ_ONLY)")

    first_row(tmp_path / "drivnx", as_drivnx, "SELECT 1 AS from_drivnx")
    first_row(tmp_path / "elsewhere", as_another_application, "SELECT 2 AS from_elsewhere")
    logged = read()

    assert re.search(
        rf"app=DrivnX user={logged_role} .*statement: .*SELECT 1 AS from_drivnx", logged
    )
    assert re.search(
        rf"app=elsewhere user={logged_role} .*statement: .*SELECT 2 AS from_elsewhere", logged
    )


def test_a_role_that_isnt_logged_leaves_no_statement_in_the_servers_log(
    logged_role: str, tmp_path: Path
) -> None:
    # Only the roles a test names, so other tests' sessions aren't logged. The logged role's
    # statement after it shows the log was read (the DRIVN-58 review).
    read = postgres_fixture.server_log_from_now()

    def as_(user: str, password: str) -> Callable[[duckdb.DuckDBPyConnection], None]:
        details = ConnectionDetails(
            host=postgres_fixture.HOST,
            port=postgres_fixture.PORT,
            database=postgres_fixture.DATABASE,
            user=user,
            tls_mode="prefer",
        )
        return lambda connection: attach(connection, details, password)

    first_row(
        tmp_path / "unlogged",
        as_(postgres_fixture.USER, postgres_fixture.PASSWORD),
        "SELECT 3 AS not_logged",
    )
    first_row(tmp_path / "logged", as_(logged_role, LOGGED_PASSWORD), "SELECT 4 AS logged_after")
    logged = read()

    assert "logged_after" in logged
    assert "not_logged" not in logged
