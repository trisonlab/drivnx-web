"""PostgreSQL through DuckDB's extension, which the wheel carries (ADR 0006)."""

from pathlib import Path

import duckdb

# Filled by the wheel build for each tested platform, and by `uv sync` for this machine.
EXTENSIONS = Path(__file__).parent / "postgres_extension"


class PostgresUnavailable(Exception):
    """No extension is bundled for this machine's platform, so PostgreSQL can't be used here."""


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
        raise PostgresUnavailable(f"DrivnX has no PostgreSQL extension for {platform}.")
    quoted = str(found).replace("'", "''")
    connection.execute(f"LOAD '{quoted}'")
