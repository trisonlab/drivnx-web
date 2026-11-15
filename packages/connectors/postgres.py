"""PostgreSQL through DuckDB's extension, which the wheel carries (ADR 0006)."""

from dataclasses import dataclass
from itertools import groupby
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
_NOT_LISTED = "DrivnX couldn't list this database's schemas and tables. Try again."
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
        # Read-only on the server's side too, under DuckDB's READ_ONLY (AC-14). 30 s for any one
        # statement, as the spec assumes for discovery, preview and values.
        "options": "-c default_transaction_read_only=on -c statement_timeout=30s",
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


@dataclass(frozen=True)
class DiscoveredColumn:
    name: str
    physical_type: str
    normalised_type: str
    allows_nulls: bool


@dataclass(frozen=True)
class DeclaredKey:
    kind: str
    name: str
    columns: tuple[str, ...]
    referenced_schema: str | None = None
    referenced_object: str | None = None
    referenced_columns: tuple[str, ...] | None = None


@dataclass(frozen=True)
class DiscoveredObject:
    schema: str
    name: str
    kind: str
    # PostgreSQL's estimate, None where it has none: never analysed, or a view.
    estimated_rows: int | None
    readable: bool
    # False for a materialized view never refreshed, which no one can read.
    populated: bool
    columns: tuple[DiscoveredColumn, ...]
    keys: tuple[DeclaredKey, ...]


@dataclass(frozen=True)
class Discovery:
    schemas: tuple[str, ...]
    objects: tuple[DiscoveredObject, ...]


_KINDS = {"r": "table", "p": "partitioned_table", "v": "view", "m": "materialized_view"}
# By the name of the column's base type in pg_type, in ADR 0003's vocabulary. Anything else, as
# text, uuid, json, arrays and enums, is a string.
_NORMALISED_TYPES = {
    "int2": "integer",
    "int4": "integer",
    "int8": "integer",
    "float4": "number",
    "float8": "number",
    "numeric": "number",
    "bool": "boolean",
    "date": "date",
    "time": "time",
    "timetz": "time",
    "timestamp": "timestamp",
    "timestamptz": "timestamp",
}
# The catalog queries are fixed text: nothing from the user or the server is put into them.
# Not the system schemas, nor the temporary ones (pg_temp_N, pg_toast_temp_N): no one can create
# another schema whose name starts with pg_.
_LISTED_SCHEMA = r"n.nspname NOT LIKE 'pg\_%' AND n.nspname <> 'information_schema'"
_SCHEMAS = f"SELECT n.nspname FROM pg_namespace n WHERE {_LISTED_SCHEMA}"
# Tables, partitioned tables, views and materialized views; not partitions, nor foreign tables.
_LISTED_OBJECT = (
    f"c.relkind IN ('r', 'p', 'v', 'm') AND NOT c.relispartition AND {_LISTED_SCHEMA}"
)
# One row per column, in each object's order, or one with no column for an object without any.
# A domain's base type is one level down. Every column has a name, as the extension wraps the
# query and selects its columns by name.
_OBJECTS = f"""
    SELECT n.nspname, c.relname, c.relkind,
        -- Autovacuum analyses a partitioned table's partitions, never the table, whose own
        -- estimate stays at -1: its rows are those of its partitions that have an estimate.
        CASE WHEN c.relkind = 'p' THEN (
            SELECT sum(p.reltuples) FROM pg_partition_tree(c.oid) AS tree
            JOIN pg_class p ON p.oid = tree.relid
            WHERE tree.isleaf AND p.reltuples >= 0
        ) ELSE c.reltuples END AS estimate,
        has_schema_privilege(n.oid, 'USAGE') AND has_table_privilege(c.oid, 'SELECT')
            AS readable,
        c.relispopulated AS populated,
        a.attname, format_type(a.atttypid, a.atttypmod) AS physical_type,
        coalesce(b.typname, t.typname) AS base_type, NOT a.attnotnull AS allows_nulls
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_type t ON t.oid = a.atttypid
    LEFT JOIN pg_type b ON b.oid = t.typbasetype
    WHERE {_LISTED_OBJECT}
    ORDER BY n.nspname, c.relname, a.attnum
"""
_KEY_KINDS = {"p": "primary_key", "u": "unique", "f": "foreign_key"}
# Each key's column names in the key's order, as arrays: a name can hold a comma.
_KEY_COLUMNS = """
    (SELECT array_agg(a.attname::text ORDER BY o.position)
     FROM unnest(k.{keys}) WITH ORDINALITY AS o (number, position)
     JOIN pg_attribute a ON a.attrelid = k.{table} AND a.attnum = o.number)
"""
_KEYS = f"""
    SELECT n.nspname, c.relname, k.contype, k.conname,
        {_KEY_COLUMNS.format(keys="conkey", table="conrelid")} AS key_columns,
        rn.nspname AS referenced_schema, rc.relname AS referenced_object,
        {_KEY_COLUMNS.format(keys="confkey", table="confrelid")} AS referenced_columns
    FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_class rc ON rc.oid = k.confrelid
    LEFT JOIN pg_namespace rn ON rn.oid = rc.relnamespace
    -- Not the copies PostgreSQL keeps of a key for each partition it references.
    WHERE k.contype IN ('p', 'u', 'f') AND k.conparentid = 0 AND {_LISTED_OBJECT}
"""


def _catalog(connection: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    return connection.execute(f"SELECT * FROM postgres_query('{NAME}', {_sql(sql)})").fetchall()


def _key(row: tuple) -> DeclaredKey:
    """A key from its row of `_KEYS`."""
    _, _, kind, name, columns, referenced_schema, referenced_object, referenced_columns = row
    return DeclaredKey(
        kind=_KEY_KINDS[kind],
        name=name,
        columns=tuple(columns),
        referenced_schema=referenced_schema,
        referenced_object=referenced_object,
        referenced_columns=None if referenced_columns is None else tuple(referenced_columns),
    )


def _object(rows: list[tuple], keys: tuple[DeclaredKey, ...]) -> DiscoveredObject:
    """An object from its rows of `_OBJECTS`."""
    schema, name, kind, estimate, readable, populated = rows[0][:6]
    return DiscoveredObject(
        schema=schema,
        name=name,
        kind=_KINDS[kind],
        # -1 until a table is first analysed, NULL for a partitioned table none of whose
        # partitions has been.
        estimated_rows=round(estimate) if estimate is not None and estimate >= 0 else None,
        readable=readable,
        populated=populated,
        columns=tuple(
            DiscoveredColumn(
                name=column,
                physical_type=physical_type,
                normalised_type=_NORMALISED_TYPES.get(base_type, "string"),
                allows_nulls=allows_nulls,
            )
            for *_, column, physical_type, base_type, allows_nulls in rows
            if column is not None
        ),
        keys=keys,
    )


def discover(details: ConnectionDetails, password: str, temp_directory: Path) -> Discovery:
    """List the database's schemas and objects from its catalog, reading no row of them."""
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        try:
            # One transaction, so every query reads the same snapshot of the catalog.
            connection.execute("BEGIN")
            schemas = _catalog(connection, _SCHEMAS)
            objects = _catalog(connection, _OBJECTS)
            keys = _catalog(connection, _KEYS)
            connection.execute("COMMIT")
        except duckdb.Error:
            # Never DuckDB's own text, which repeats the query and the server's error.
            raise ConnectFailure("other", _NOT_LISTED) from None
    # By schema and object name, the first two columns of both queries.
    keys_of: dict[tuple, list[DeclaredKey]] = {}
    for row in keys:
        keys_of.setdefault(row[:2], []).append(_key(row))
    return Discovery(
        schemas=tuple(schema for (schema,) in schemas),
        # `_OBJECTS` gives each object's rows one after another.
        objects=tuple(
            _object(list(rows), tuple(keys_of.get(found, ())))
            for found, rows in groupby(objects, key=lambda row: row[:2])
        ),
    )


def check(details: ConnectionDetails, password: str, temp_directory: Path) -> ServerFacts:
    """Connect once, and say what the server is and whether the connection is encrypted."""
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        return server_facts(connection)
