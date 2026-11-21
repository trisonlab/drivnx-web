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
_NOT_READ = (
    "DrivnX couldn't read this object. It may have changed since it was listed: refresh the list."
)
# The longest any one statement may run, as the spec assumes for discovery, preview and values.
STATEMENT_TIMEOUT_SECONDS = 30
_TIMED_OUT = (
    f"The database took longer than {STATEMENT_TIMEOUT_SECONDS} seconds to read this page."
    " Try again."
)
_OBJECT_TIMED_OUT = (
    "The database took too long to read this object, and stopped. Try again when it's less busy."
)
_NOT_ALLOWED_TO_READ = (
    "This connection's user isn't allowed to read this object any more. Ask the database's"
    " administrator for SELECT on it, or leave it out."
)
_LOST = (
    "DrivnX lost its connection to the database, as when the server restarts or an"
    " administrator ends the session. Profile again once it's back."
)
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
    connection: duckdb.DuckDBPyConnection,
    details: ConnectionDetails,
    password: str,
    statement_timeout_seconds: int | None = None,
) -> None:
    """Attach the database read-only as `source`, each statement limited to
    `statement_timeout_seconds`, by default `STATEMENT_TIMEOUT_SECONDS`.

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
    if statement_timeout_seconds is None:
        statement_timeout_seconds = STATEMENT_TIMEOUT_SECONDS
    settings = {
        "sslmode": details.tls_mode,
        "connect_timeout": str(_PER_ATTEMPT_SECONDS),
        "application_name": "DrivnX",
        # Read-only on the server's side too, under DuckDB's READ_ONLY (AC-14), and a limit for
        # any one statement.
        "options": "-c default_transaction_read_only=on"
        f" -c statement_timeout={int(statement_timeout_seconds)}s",
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
    # What the role could change it with, of `WRITE_PRIVILEGES` (AC-16). DrivnX itself never does.
    write_privileges: tuple[str, ...]


@dataclass(frozen=True)
class Discovery:
    schemas: tuple[str, ...]
    objects: tuple[DiscoveredObject, ...]
    # The role DrivnX signed in as, and whether it's a superuser, which can change anything.
    role: str
    superuser: bool


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
    -- Each listed object with its partitions and the tables that inherit from it, at any depth:
    -- rows changed in any of them are the object's rows.
    WITH RECURSIVE family (object, member) AS (
        SELECT c.oid, c.oid FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE {_LISTED_OBJECT}
        UNION
        SELECT family.object, i.inhrelid
        FROM family JOIN pg_inherits i ON i.inhparent = family.member
    ),
    -- What the role could change each object with, in `WRITE_PRIVILEGES`' order, granted on it or
    -- on any of its family. INSERT or UPDATE on any one column is enough to change its rows.
    can AS (
        SELECT object,
            bool_or(has_any_column_privilege(member, 'INSERT')) AS can_insert,
            bool_or(has_any_column_privilege(member, 'UPDATE')) AS can_update,
            bool_or(has_table_privilege(member, 'DELETE')) AS can_delete,
            bool_or(has_table_privilege(member, 'TRUNCATE')) AS can_truncate
        FROM family GROUP BY object
    )
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
        can.can_insert, can.can_update, can.can_delete, can.can_truncate,
        a.attname, format_type(a.atttypid, a.atttypmod) AS physical_type,
        coalesce(b.typname, t.typname) AS base_type, NOT a.attnotnull AS allows_nulls
    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN can ON can.object = c.oid
    LEFT JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
    LEFT JOIN pg_type t ON t.oid = a.atttypid
    LEFT JOIN pg_type b ON b.oid = t.typbasetype
    WHERE {_LISTED_OBJECT}
    ORDER BY n.nspname, c.relname, a.attnum
"""
# The privileges with which a role could change an object's rows (CFX-APP-postgres AC-16).
WRITE_PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE")
# The role DrivnX is signed in as, and whether it's a superuser.
_ROLE = "SELECT current_user::text, rolsuper FROM pg_roles WHERE rolname = current_user"
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


def _on_server(connection: duckdb.DuckDBPyConnection, sql: str) -> list[tuple]:
    """Run one query on the server, in its own SQL, through the attached database."""
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
    schema, name, kind, estimate, readable, populated, *can = rows[0][:10]
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
        write_privileges=tuple(
            privilege for privilege, granted in zip(WRITE_PRIVILEGES, can, strict=True) if granted
        ),
    )


def discover(details: ConnectionDetails, password: str, temp_directory: Path) -> Discovery:
    """List the database's schemas and objects from its catalog, reading no row of them."""
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        try:
            # One transaction, so every query reads the same snapshot of the catalog.
            connection.execute("BEGIN")
            schemas = _on_server(connection, _SCHEMAS)
            objects = _on_server(connection, _OBJECTS)
            keys = _on_server(connection, _KEYS)
            ((role, superuser),) = _on_server(connection, _ROLE)
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
        role=role,
        superuser=superuser,
    )


def check(details: ConnectionDetails, password: str, temp_directory: Path) -> ServerFacts:
    """Connect once, and say what the server is and whether the connection is encrypted."""
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        return server_facts(connection)


def _identifier(name: str) -> str:
    """A schema, object or column name as PostgreSQL quotes it, whatever it holds."""
    return '"' + name.replace('"', '""') + '"'


def _as_text(object_name: tuple[str, str], columns: tuple[str, ...]) -> str:
    """Select an object's columns as PostgreSQL's text for each value, named `c0`, `c1`, …"""
    schema, name = object_name
    # Every column named, as the extension wraps the query and selects its columns by name.
    shown = ", ".join(f"{_identifier(column)}::text AS c{n}" for n, column in enumerate(columns))
    # A table can have no columns, and its rows are still rows: one constant each, dropped later.
    shown = shown or "1 AS c0"
    return f"SELECT {shown} FROM {_identifier(schema)}.{_identifier(name)}"


def _not_read(error: duckdb.Error, timed_out: str = _TIMED_OUT) -> ConnectFailure:
    """Why an object's rows couldn't be read, with `timed_out` for a statement timeout. Never
    DuckDB's own text, which repeats the query and the server's error: only which of these the
    server said, in its English text."""
    text = str(error)
    if "canceling statement due to statement timeout" in text:
        return ConnectFailure("timeout", timed_out)
    # Ended by an administrator, or by a restart's fast shutdown, which says the same.
    if "terminating connection due to administrator command" in text:
        return ConnectFailure("lost", _LOST)
    if "permission denied for" in text:
        return ConnectFailure("not_allowed", _NOT_ALLOWED_TO_READ)
    return ConnectFailure("changed", _NOT_READ)


def read_rows(
    details: ConnectionDetails,
    password: str,
    temp_directory: Path,
    object_name: tuple[str, str],
    columns: tuple[str, ...],
    key: tuple[str, ...],
    offset: int,
    limit: int,
) -> list[tuple[str | None, ...]]:
    """`limit` rows of an object from `offset`, each value as PostgreSQL's text for it, read on
    the server so only these rows reach DrivnX. In the order of the primary key's columns `key`
    when there is one, else in the server's own (the order agreed for DRIVN-50)."""
    sql = _as_text(object_name, columns)
    if key:
        sql += " ORDER BY " + ", ".join(_identifier(column) for column in key)
    sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        try:
            rows = _on_server(connection, sql)
        except duckdb.Error as error:
            raise _not_read(error) from None
    return rows if columns else [() for _ in rows]


# About how many rows a fast profile reads from each object (ADR 0005).
SAMPLE_ROWS = 100_000


@dataclass(frozen=True)
class Reading:
    """How a fast profile reads an object: a page sample at `percent` of its pages, the whole
    object, or a view's first rows (ADR 0005)."""

    method: str
    percent: float | None = None


def reading_for(kind: str, estimated_rows: int | None) -> Reading:
    """How to read an object of this kind and estimated size for a fast profile (AC-18).

    PostgreSQL refuses `TABLESAMPLE` on a view, so it reads its first rows. Anything else larger
    than a sample is sampled by page; a smaller one, or one never analysed, is read whole, with a
    guard for an estimate that was wrong.
    """
    if kind == "view":
        return Reading("view_first_rows")
    if estimated_rows is not None and estimated_rows > SAMPLE_ROWS:
        return Reading("page_sample", 100 * SAMPLE_ROWS / estimated_rows)
    return Reading("whole")


def load_object_rows(
    connection: duckdb.DuckDBPyConnection,
    object_name: tuple[str, str],
    columns: tuple[str, ...],
    reading: Reading,
    seed: int,
    table: str,
) -> str:
    """Load an object's rows for a fast profile into `table`, as text columns `c0`, `c1`, …, and
    say how they were read: as `reading` says, or `first_rows` when an object read whole turns
    out larger than its estimate (ADR 0005)."""
    sql = _as_text(object_name, columns)
    if reading.method == "page_sample":
        sql += f" TABLESAMPLE SYSTEM ({float(reading.percent or 0)}) REPEATABLE ({int(seed)})"
    elif reading.method == "view_first_rows":
        sql += f" LIMIT {SAMPLE_ROWS}"
    else:
        # One more than a sample, to know whether that was all of it.
        sql += f" LIMIT {SAMPLE_ROWS + 1}"
    try:
        connection.execute(
            f"CREATE TABLE {table} AS SELECT * FROM postgres_query('{NAME}', {_sql(sql)})"
        )
    except duckdb.Error as error:
        raise _not_read(error, _OBJECT_TIMED_OUT) from None
    if reading.method == "whole":
        (loaded,) = connection.execute(f"SELECT count(*) FROM {table}").fetchone()
        if loaded > SAMPLE_ROWS:
            return "first_rows"
    return reading.method


def load_whole_object(
    connection: duckdb.DuckDBPyConnection,
    object_name: tuple[str, str],
    columns: tuple[str, ...],
    table: str,
) -> None:
    """Load every row of an object for a full profile into `table`, as text columns `c0`, `c1`,
    …, through DuckDB's scanner, each value as PostgreSQL's text (ADR 0009)."""
    if not columns:
        # DuckDB's catalog leaves out a table with no columns, whose rows the server still reads.
        sql = _as_text(object_name, columns)
        try:
            connection.execute(
                f"CREATE TABLE {table} AS SELECT * FROM postgres_query('{NAME}', {_sql(sql)})"
            )
        except duckdb.Error as error:
            raise _not_read(error, _OBJECT_TIMED_OUT) from None
        return
    schema, name = object_name
    source = f"{NAME}.{_identifier(schema)}.{_identifier(name)}"
    # Only for this read: an unscaled or wide numeric and an array keep PostgreSQL's text, not a
    # double's or DuckDB's list's. Elsewhere `postgres_query` must still return arrays as lists.
    connection.execute("SET pg_numeric_as_varchar = true")
    connection.execute("SET pg_array_as_varchar = true")
    # One thread, so the scanner reads over one session, not one per thread (AC-23, ADR 0005).
    # Measuring the rows afterwards is local and keeps every thread.
    connection.execute("SET threads = 1")
    try:
        # By position: DuckDB's names don't tell `"A"` from `a`, which it calls `a_1`.
        blobs = {
            n
            for n, (_, column_type, *_) in enumerate(
                connection.execute(f"DESCRIBE {source}").fetchall()
            )
            if column_type == "BLOB"
        }

        def as_text(n: int) -> str:
            # `bytea` as PostgreSQL writes it, `\x` and lowercase hex, not DuckDB's `\xDE\xAD`.
            if n in blobs:
                return f"'\\x' || lower(hex(c{n}))"
            return f"CAST(c{n} AS VARCHAR)"

        positions = range(len(columns))
        shown = ", ".join(f"{as_text(n)} AS c{n}" for n in positions)
        named = ", ".join(f"c{n}" for n in positions)
        connection.execute(
            f"CREATE TABLE {table} AS SELECT {shown} FROM {source} AS listed({named})"
        )
    except duckdb.Error as error:
        raise _not_read(error, _OBJECT_TIMED_OUT) from None
    finally:
        reset_whole_read(connection)


def reset_whole_read(connection: duckdb.DuckDBPyConnection) -> None:
    """Put back the settings `load_whole_object` changes for its read. Again after a read that
    was stopped, as the interrupt that stopped it can also stop these."""
    connection.execute("RESET pg_numeric_as_varchar")
    connection.execute("RESET pg_array_as_varchar")
    connection.execute("RESET threads")


def session_pid(connection: duckdb.DuckDBPyConnection) -> int:
    """The server's process for the session this connection reads over (ADR 0009)."""
    ((pid,),) = _on_server(connection, "SELECT pg_backend_pid()")
    return pid


def cancel_read(
    details: ConnectionDetails, password: str, pid: int, temp_directory: Path
) -> None:
    """Cancel the query a session `pid` is running, from a second connection closed at once.

    DuckDB's interrupt stops a table's scan, not a view's read nor a `postgres_query`, which return
    only once the server has sent every row (ADR 0009). The server lets a role cancel its own
    sessions. If it refuses, the read still ends at its statement timeout.
    """
    with duckdb_connection(temp_directory) as connection:
        attach(connection, details, password)
        _on_server(connection, f"SELECT pg_cancel_backend({int(pid)})")
