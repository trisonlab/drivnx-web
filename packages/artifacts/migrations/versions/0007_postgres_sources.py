"""A source can be a PostgreSQL database as well as a CSV file (CFX-APP-postgres).

Everything about the connection is saved except its password, which is a secret: there is no
column for it.
"""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

CSV_COLUMNS = (
    "path",
    "delimiter",
    "encoding",
    "has_header",
    "size_bytes",
    "modified_at",
)
POSTGRES_COLUMNS = ("host", "port", "database_name", "user_name", "tls_mode")
KEPT = "id, kind, path, delimiter, encoding, has_header, size_bytes, modified_at, skip_lines, corrected"


def _all(columns: tuple[str, ...], test: str) -> str:
    return " AND ".join(f"{column} {test}" for column in columns)


def upgrade() -> None:
    # SQLite can't change a column's constraints in place, so the table is rebuilt, the way its
    # documentation describes. The upgrade runs with foreign keys off (`artifacts.schema`), and
    # `dataset` refers to `source` by name, so it points at the new table once it's renamed.
    op.execute(
        f"""
        CREATE TABLE source_new (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('csv', 'postgres')),
            path TEXT UNIQUE,
            delimiter TEXT,
            encoding TEXT,
            has_header INTEGER CHECK (has_header IN (0, 1)),
            size_bytes INTEGER,
            modified_at TEXT,
            skip_lines INTEGER CHECK (skip_lines BETWEEN 0 AND 1000),
            corrected INTEGER NOT NULL DEFAULT 0 CHECK (corrected IN (0, 1)),
            host TEXT,
            port INTEGER CHECK (port BETWEEN 1 AND 65535),
            database_name TEXT,
            user_name TEXT,
            tls_mode TEXT CHECK (tls_mode IN ('prefer', 'require', 'verify-full')),
            ca_path TEXT,
            CHECK (
                CASE kind
                    WHEN 'csv' THEN {_all(CSV_COLUMNS, "IS NOT NULL")}
                        AND {_all(POSTGRES_COLUMNS, "IS NULL")} AND ca_path IS NULL
                    ELSE {_all(POSTGRES_COLUMNS, "IS NOT NULL")}
                        AND {_all(CSV_COLUMNS, "IS NULL")}
                        AND skip_lines IS NULL AND corrected = 0
                        AND (tls_mode = 'verify-full') = (ca_path IS NOT NULL)
                END
            ),
            UNIQUE (host, port, database_name, user_name)
        )
        """
    )
    # In the old order, which is the order sources are listed in.
    op.execute(f"INSERT INTO source_new ({KEPT}) SELECT {KEPT} FROM source ORDER BY rowid")
    op.execute("DROP TABLE source")
    op.execute("ALTER TABLE source_new RENAME TO source")


def downgrade() -> None:
    # A PostgreSQL source has nowhere to go in the old table, so it is refused, not dropped.
    op.execute(
        """
        CREATE TABLE source_old (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('csv')),
            path TEXT NOT NULL UNIQUE,
            delimiter TEXT NOT NULL,
            encoding TEXT NOT NULL,
            has_header INTEGER NOT NULL CHECK (has_header IN (0, 1)),
            size_bytes INTEGER NOT NULL,
            modified_at TEXT NOT NULL,
            skip_lines INTEGER CHECK (skip_lines BETWEEN 0 AND 1000),
            corrected INTEGER NOT NULL DEFAULT 0 CHECK (corrected IN (0, 1))
        )
        """
    )
    op.execute(f"INSERT INTO source_old ({KEPT}) SELECT {KEPT} FROM source ORDER BY rowid")
    op.execute("DROP TABLE source")
    op.execute("ALTER TABLE source_old RENAME TO source")
