"""Connecting to PostgreSQL read-only, CFX-APP-postgres AC-02, AC-03 and AC-14 (DRIVN-47)."""

import socket
import subprocess
import time
import traceback
from dataclasses import replace
from pathlib import Path

import duckdb
import pytest
from connectors import postgres
from connectors.csv_files import duckdb_connection
from connectors.postgres import (
    CONNECT_TIMEOUT_SECONDS,
    ConnectFailure,
    ConnectionDetails,
    attach,
    check,
    load_postgres,
)

from .. import postgres_fixture

FIXTURE = ConnectionDetails(
    host=postgres_fixture.HOST,
    port=postgres_fixture.PORT,
    database=postgres_fixture.DATABASE,
    user=postgres_fixture.USER,
    tls_mode="prefer",
)
PASSWORD = postgres_fixture.PASSWORD


def version_text(dsn: str, tmp_path: Path) -> str:
    """`version()` as the server answers a connection the test makes itself."""
    with duckdb_connection(tmp_path) as connection:
        load_postgres(connection)
        connection.execute(f"ATTACH '{dsn}' AS server (TYPE postgres, READ_ONLY)")
        return connection.execute(
            "SELECT * FROM postgres_query('server', 'SELECT version()')"
        ).fetchone()[0]


def test_details_that_work_give_the_servers_version_and_that_it_is_encrypted(
    postgres_server: str, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-02: the fixture has TLS, so "prefer" encrypts.
    facts = check(FIXTURE, PASSWORD, tmp_path)

    assert f"PostgreSQL {facts.version} " in version_text(postgres_server, tmp_path)
    assert facts.encrypted is True


def test_a_connection_the_server_wont_encrypt_says_so(
    postgres_server: str, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-02: the fixture refuses TLS to this account, so "prefer" connects
    # without it.
    plain = replace(FIXTURE, user=postgres_fixture.PLAIN_USER)

    assert check(plain, postgres_fixture.PLAIN_PASSWORD, tmp_path).encrypted is False


def test_verify_full_connects_with_the_ca_that_signed_the_servers_certificate(
    postgres_server: str, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-01: a CA certificate file, for verify-full.
    verified = replace(
        FIXTURE, tls_mode="verify-full", ca_path=str(postgres_fixture.CA_CERTIFICATE)
    )

    assert check(verified, PASSWORD, tmp_path).encrypted is True


def another_ca(directory: Path) -> Path:
    """A CA that didn't sign the fixture's certificate."""
    (directory / "ca.cnf").write_text(postgres_fixture.CA_CONFIG, encoding="utf-8")
    subprocess.run(
        ["openssl", "req", "-x509", "-new", "-newkey", "rsa:2048", "-nodes", "-days", "1",
         "-config", "ca.cnf", "-extensions", "ca", "-keyout", "ca.key", "-out", "ca.crt"],
        cwd=directory, capture_output=True, check=True,
    )
    return directory / "ca.crt"


@pytest.mark.parametrize(
    ("change", "password", "reason", "says"),
    [
        ({}, "not the password", "credentials", "password"),
        # Under prefer, libpq tries again without TLS, and the last attempt is the one to report.
        ({"user": postgres_fixture.PLAIN_USER}, "not the password", "credentials", "password"),
        ({"database": "no_such_database"}, PASSWORD, "no_database", "database"),
        ({"host": "no-such-host.invalid"}, PASSWORD, "unreachable", "host"),
        ({"port": "free"}, PASSWORD, "unreachable", "port"),
        (
            {"user": postgres_fixture.PLAIN_USER, "tls_mode": "require"},
            postgres_fixture.PLAIN_PASSWORD,
            "tls",
            "encrypt",
        ),
        ({"tls_mode": "verify-full", "ca_path": "another"}, PASSWORD, "tls", "certificate"),
        (
            {"tls_mode": "verify-full", "ca_path": "missing"},
            PASSWORD,
            "ca_file",
            "CA certificate",
        ),
    ],
)
def test_each_way_of_not_connecting_says_which(
    postgres_server: str,
    tmp_path: Path,
    free_port: int,
    change: dict[str, object],
    password: str,
    reason: str,
    says: str,
) -> None:
    # CFX-APP-postgres/AC-03: a host that can't be reached, refused credentials, a database that
    # doesn't exist, or TLS that is required and fails.
    paths = {"another": str(another_ca(tmp_path)), "missing": str(tmp_path / "no-ca.crt")}
    if change.get("port") == "free":
        change = {**change, "port": free_port}
    if "ca_path" in change:
        change = {**change, "ca_path": paths[change["ca_path"]]}

    with pytest.raises(ConnectFailure) as failed:
        check(replace(FIXTURE, **change), password, tmp_path)

    assert failed.value.reason == reason
    assert says in str(failed.value)


def test_a_server_that_never_answers_fails_within_the_connect_timeout(
    tmp_path: Path,
) -> None:
    # CFX-APP-postgres/AC-03: "within the connect timeout", for a host that accepts the
    # connection and then says nothing, as a firewall or a hung server can.
    silent = socket.socket()
    silent.bind(("127.0.0.1", 0))
    silent.listen()
    started = time.monotonic()
    try:
        with pytest.raises(ConnectFailure) as failed:
            check(replace(FIXTURE, port=silent.getsockname()[1]), PASSWORD, tmp_path)
    finally:
        silent.close()

    assert time.monotonic() - started < CONNECT_TIMEOUT_SECONDS + 2
    assert failed.value.reason == "unreachable"
    assert f"{CONNECT_TIMEOUT_SECONDS} seconds" in str(failed.value)


def test_a_password_with_quotes_a_backslash_and_an_accent_reaches_the_server_as_typed(
    postgres_server: str, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-02: details that work, whatever the password holds.
    quotes = replace(FIXTURE, user=postgres_fixture.QUOTES_USER)

    assert check(quotes, postgres_fixture.QUOTES_PASSWORD, tmp_path).version


PLANTED = "Pl4nted'pass\\word é"  # pragma: allowlist secret, ai-skills: allow


@pytest.mark.parametrize(
    "change",
    [{}, {"database": "no_such_database"}, {"tls_mode": "verify-full", "ca_path": "/no/ca.crt"}],
)
def test_no_failure_repeats_the_password(
    postgres_server: str, tmp_path: Path, change: dict[str, str]
) -> None:
    # CFX-APP-postgres/AC-30 starts here: DuckDB's errors repeat its connection string, so the
    # password must never be in it, and the failure must carry nothing that is.
    with pytest.raises(ConnectFailure) as failed:
        check(replace(FIXTURE, **change), PLANTED, tmp_path)

    shown = "".join(traceback.format_exception(failed.value))
    underneath = str(failed.value.__context__)
    for text in (shown, underneath, repr(failed.value)):
        assert "Pl4nted" not in text


def test_the_password_is_only_in_duckdbs_secret_and_shown_redacted(
    postgres_server: str, tmp_path: Path
) -> None:
    # CFX-APP-postgres/AC-30 starts here.
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, PASSWORD)
        secrets = connection.execute("SELECT secret_string FROM duckdb_secrets()").fetchall()
        attached = connection.execute(
            "SELECT path FROM duckdb_databases() WHERE database_name = 'source'"
        ).fetchall()

    assert secrets and all(PASSWORD not in secret for (secret,) in secrets)
    assert attached and all(PASSWORD not in path for (path,) in attached)


def test_a_password_duckdb_cant_take_fails_without_repeating_it(tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-30 starts here: a statement DuckDB refuses is printed in its error.
    with pytest.raises(ConnectFailure) as failed:
        check(FIXTURE, f"{PLANTED}\x00rest", tmp_path)

    assert "Pl4nted" not in "".join(traceback.format_exception(failed.value))
    assert "Pl4nted" not in str(failed.value.__context__)


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE drivnx_wrote_this (id integer)",
        "INSERT INTO people VALUES (4, 'Dee', NULL)",
        "UPDATE people SET name = 'changed'",
    ],
)
def test_a_write_through_drivnxs_connection_is_refused_by_the_server(
    postgres_server: str, tmp_path: Path, statement: str
) -> None:
    # CFX-APP-postgres/AC-14: `postgres_execute` sends a statement as it is. The fixture's role
    # can't write these anyway, so the test looks for the read-only refusal, which the server
    # gives first. Either of DrivnX's two layers alone makes it: DuckDB's READ_ONLY attach opens
    # read-only transactions, and the session's default is read-only.
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, PASSWORD)
        quoted = statement.replace("'", "''")
        with pytest.raises(duckdb.Error, match="read-only transaction"):
            connection.execute(f"CALL postgres_execute('source', '{quoted}')")


def test_drivnx_names_itself_to_the_server(postgres_server: str, tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-14, and AC-15 to come: a database administrator can see DrivnX's
    # sessions, and they're read-only by default.
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, PASSWORD)
        settings = connection.execute(
            "SELECT * FROM postgres_query('source', 'SELECT current_setting(''application_name''),"
            " current_setting(''default_transaction_read_only'')')"
        ).fetchone()

    assert settings == ("DrivnX", "on")


def test_duckdb_attaches_the_database_read_only(postgres_server: str, tmp_path: Path) -> None:
    # CFX-APP-postgres/AC-14: the other of the two layers.
    with duckdb_connection(tmp_path) as connection:
        attach(connection, FIXTURE, PASSWORD)
        read_only = connection.execute(
            "SELECT readonly FROM duckdb_databases() WHERE database_name = 'source'"
        ).fetchone()

    assert read_only == (True,)


def test_a_platform_with_no_bundled_extension_is_told_postgresql_isnt_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # CFX-APP-postgres/AC-07: an empty folder is what a platform the wheel has nothing for sees.
    monkeypatch.setattr(postgres, "EXTENSIONS", tmp_path / "none")
    with duckdb_connection(tmp_path) as connection:
        platform = connection.execute("PRAGMA platform").fetchone()[0]

    with pytest.raises(ConnectFailure) as failed:
        check(FIXTURE, PASSWORD, tmp_path)

    assert failed.value.reason == "unavailable_platform"
    assert str(failed.value) == f"PostgreSQL isn't available on this platform ({platform})."
