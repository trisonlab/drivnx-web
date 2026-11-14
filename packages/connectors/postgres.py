"""PostgreSQL through DuckDB's extension, which the wheel carries (ADR 0006)."""

from dataclasses import dataclass
from pathlib import Path

import duckdb

from connectors.csv_files import duckdb_connection

# Filled by the wheel build for each tested platform, and by `uv sync` for this machine.
EXTENSIONS = Path(__file__).parent / "postgres_extension"


class PostgresUnavailable(Exception):
    """No extension is bundled for this machine's platform, so PostgreSQL can't be used here."""

    def __init__(self, platform: str) -> None:
        super().__init__(f"DrivnX has no PostgreSQL extension for {platform}.")
        self.platform = platform


def extension_file(platform: str) -> Path | None:
    """The bundled extension for a DuckDB platform name, or None where none is bundled."""
    found = EXTENSIONS / platform / "postgres_scanner.duckdb_extension"
    return found if found.is_file() else None


def load_postgres(connection: duckdb.DuckDBPyConnection) -> None:
    """Load the bundled extension into a connection. DuckDB still checks its signature."""
    platform = connection.execute("PRAGMA platform").fetchone()[0]
    found = extension_file(platform)
    # Never a name, which DuckDB would look up in its own folder and repository.
    if found is None:
        raise PostgresUnavailable(platform)
    quoted = str(found).replace("'", "''")
    connection.execute(f"LOAD '{quoted}'")


@dataclass(frozen=True)
class ConnectionDetails:
    """Everything needed to connect except the password, which is a secret."""

    host: str
    port: int
    database: str
    user: str
    tls_mode: str
    ca_path: str | None = None


@dataclass(frozen=True)
class ServerFacts:
    version: str
    encrypted: bool


class ConnectFailure(Exception):
    """DrivnX couldn't connect. The message is written for the user; `reason` says which case."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


CONNECT_TIMEOUT_SECONDS = 10
# DuckDB 2.0.0's ATTACH connects a second time when the first attempt fails, whatever its pool
# settings, so libpq gets half the time the user is told, and a wrong password is two failed
# sign-ins on the server.
_PER_ATTEMPT_SECONDS = CONNECT_TIMEOUT_SECONDS // 2
NAME = "source"


def _sql(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _libpq(value: str) -> str:
    """A value in a libpq connection string."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


# What DrivnX tells the user.
_NO_CA_FILE = "DrivnX couldn't read the CA certificate file. Check its path."
_TLS_REFUSED = (
    "The server won't encrypt a connection for this user, and the TLS mode requires it."
)
_NO_TLS = "The server doesn't offer encrypted connections, and the TLS mode requires one."
_UNVERIFIED = (
    "The server's certificate couldn't be verified with this CA certificate, or doesn't name"
    " this host."
)
_CREDENTIALS = "The server refused this user name and password."
_NOT_ALLOWED = "The server doesn't let this user connect to this database from here."
_NO_DATABASE = "The server has no database with this name."
_NO_SUCH_HOST = "DrivnX couldn't find a server with this host name."
_REFUSED = "Nothing answered at this host and port. Check them, and that the server is running."
_SILENT = (
    f"The server didn't answer within {CONNECT_TIMEOUT_SECONDS} seconds. Check the host and port."
)
_UNREACHABLE = "DrivnX couldn't reach this host. Check the host and port."
# What libpq says, in English, in the order to look for it: a TLS failure can also mention
# pg_hba.conf. A server set to another language gets the general message instead.
_FAILURES = (
    ("root certificate file", "ca_file", _NO_CA_FILE),
    ("SSL encryption", "tls", _TLS_REFUSED),
    ("does not support SSL", "tls", _NO_TLS),
    ("SSL error", "tls", _UNVERIFIED),
    ("does not match host name", "tls", _UNVERIFIED),
    ("password authentication failed", "credentials", _CREDENTIALS),
    ("pg_hba.conf", "credentials", _NOT_ALLOWED),
    ("does not exist", "no_database", _NO_DATABASE),
    ("could not translate host name", "unreachable", _NO_SUCH_HOST),
    ("Connection refused", "unreachable", _REFUSED),
    ("timeout expired", "unreachable", _SILENT),
    ("No route to host", "unreachable", _UNREACHABLE),
    ("Network is unreachable", "unreachable", _UNREACHABLE),
)
_ATTEMPT = "connection to server at"


def _failure(error: duckdb.Error) -> ConnectFailure:
    """Say why the connection failed, from libpq's last attempt only: under `prefer` it tries again
    without TLS, and the first attempt's reason is then not the one that counts."""
    text = str(error)
    last = text[text.rfind(_ATTEMPT):] if _ATTEMPT in text else text
    for found, reason, message in _FAILURES:
        if found in last:
            return ConnectFailure(reason, message)
    return ConnectFailure("other", "DrivnX couldn't connect to this database.")


def attach(
    connection: duckdb.DuckDBPyConnection, details: ConnectionDetails, password: str
) -> None:
    """Attach the database read-only as `source`.

    The password goes only into a temporary secret, which lives in this connection's memory:
    DuckDB repeats the connection string in its errors, so the password can't be in it.
    """
    try:
        load_postgres(connection)
    except PostgresUnavailable as error:
        raise ConnectFailure(
            "unavailable_platform",
            f"PostgreSQL isn't available on this platform ({error.platform}).",
        ) from None
    connection.execute(
        f"""
        CREATE TEMPORARY SECRET {NAME} (
            TYPE postgres, HOST {_sql(details.host)}, PORT {int(details.port)},
            DATABASE {_sql(details.database)}, USER {_sql(details.user)},
            PASSWORD {_sql(password)}
        )
        """
    )
    settings = {
        "sslmode": details.tls_mode,
        "connect_timeout": str(_PER_ATTEMPT_SECONDS),
        "application_name": "DrivnX",
        # Read-only on the server's side too, under DuckDB's READ_ONLY (AC-14).
        "options": "-c default_transaction_read_only=on",
    }
    if details.ca_path is not None:
        settings["sslrootcert"] = details.ca_path
    conninfo = " ".join(f"{key}={_libpq(value)}" for key, value in settings.items())
    try:
        connection.execute(
            f"ATTACH {_sql(conninfo)} AS {NAME} (TYPE postgres, READ_ONLY, SECRET {NAME})"
        )
    except duckdb.Error as error:
        # Never DuckDB's own text, which repeats the connection string.
        raise _failure(error) from None


def server_facts(connection: duckdb.DuckDBPyConnection) -> ServerFacts:
    version, encrypted = connection.execute(
        f"""
        SELECT * FROM postgres_query('{NAME}', 'SELECT current_setting(''server_version''),
            (SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid())')
        """
    ).fetchone()
    return ServerFacts(version=version, encrypted=bool(encrypted))


def check(details: ConnectionDetails, password: str, temp_directory: Path) -> ServerFacts:
    """Connect once, and say what the server is and whether the connection is encrypted."""
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        return server_facts(connection)
