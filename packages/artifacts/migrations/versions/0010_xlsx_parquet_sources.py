"""A source can be an XLSX workbook or a Parquet file as well (DRIVN-60, DRIVN-61).

A Parquet file keeps what its metadata says about it: its row count, its row groups and the
program that wrote it. Never a minimum or maximum from its statistics, which are values from the
data (ADR 0004).
"""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

FILE_COLUMNS = ("path", "size_bytes", "modified_at")
CSV_COLUMNS = ("delimiter", "encoding", "has_header")
POSTGRES_COLUMNS = ("host", "port", "database_name", "user_name", "tls_mode")
PARQUET_COLUMNS = ("row_count", "row_group_count")
KEPT = (
    "id, kind, path, delimiter, encoding, has_header, size_bytes, modified_at, skip_lines, "
    "corrected, host, port, database_name, user_name, tls_mode, ca_path, discovered_at, "
    "write_confirmed"
)


def _all(columns: tuple[str, ...], test: str) -> str:
    return " AND ".join(f"{column} {test}" for column in columns)


def upgrade() -> None:
    # Rebuilt as 0007 rebuilt it: SQLite can't change a CHECK in place. The upgrade runs with
    # foreign keys off (`artifacts.schema`), and `dataset` refers to `source` by name.
    not_a_database = f"{_all(POSTGRES_COLUMNS, 'IS NULL')} AND ca_path IS NULL"
    not_parquet = f"{_all(PARQUET_COLUMNS, 'IS NULL')} AND created_by IS NULL"
    no_csv_settings = (
        f"{_all(CSV_COLUMNS, 'IS NULL')} AND skip_lines IS NULL AND corrected = 0"
    )
    op.execute(
        f"""
        CREATE TABLE source_new (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('csv', 'postgres', 'xlsx', 'parquet')),
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
            discovered_at TEXT,
            write_confirmed TEXT CHECK (
                write_confirmed IS NULL OR (kind = 'postgres' AND json_valid(write_confirmed))
            ),
            row_count INTEGER CHECK (row_count >= 0),
            row_group_count INTEGER CHECK (row_group_count >= 0),
            created_by TEXT,
            CHECK (
                CASE kind
                    WHEN 'csv' THEN {_all(FILE_COLUMNS + CSV_COLUMNS, "IS NOT NULL")}
                        AND {not_a_database} AND {not_parquet}
                    WHEN 'postgres' THEN {_all(POSTGRES_COLUMNS, "IS NOT NULL")}
                        AND {_all(FILE_COLUMNS, "IS NULL")} AND {no_csv_settings}
                        AND {not_parquet}
                        AND (tls_mode = 'verify-full') = (ca_path IS NOT NULL)
                    WHEN 'parquet' THEN {_all(FILE_COLUMNS + PARQUET_COLUMNS, "IS NOT NULL")}
                        AND {no_csv_settings} AND {not_a_database}
                    ELSE {_all(FILE_COLUMNS, "IS NOT NULL")}
                        AND {no_csv_settings} AND {not_a_database} AND {not_parquet}
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
    # A workbook or a Parquet file has nowhere to go in the old table, and runs may refer to it,
    # so a workspace with one is refused, not emptied, as 0008 refuses a database's objects.
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM source WHERE kind IN ('xlsx', 'parquet') LIMIT 1")
        .first()
    ):
        raise RuntimeError(
            "This workspace has an XLSX or Parquet source, so it stays at 0010."
        )
    # The table as 0007 built it, with the columns 0008 and 0009 added.
    op.execute(
        f"""
        CREATE TABLE source_old (
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
            discovered_at TEXT,
            write_confirmed TEXT CHECK (
                write_confirmed IS NULL OR (kind = 'postgres' AND json_valid(write_confirmed))
            ),
            CHECK (
                CASE kind
                    WHEN 'csv' THEN {_all(FILE_COLUMNS + CSV_COLUMNS, "IS NOT NULL")}
                        AND {_all(POSTGRES_COLUMNS, "IS NULL")} AND ca_path IS NULL
                    ELSE {_all(POSTGRES_COLUMNS, "IS NOT NULL")}
                        AND {_all(FILE_COLUMNS + CSV_COLUMNS, "IS NULL")}
                        AND skip_lines IS NULL AND corrected = 0
                        AND (tls_mode = 'verify-full') = (ca_path IS NOT NULL)
                END
            ),
            UNIQUE (host, port, database_name, user_name)
        )
        """
    )
    op.execute(f"INSERT INTO source_old ({KEPT}) SELECT {KEPT} FROM source ORDER BY rowid")
    op.execute("DROP TABLE source")
    op.execute("ALTER TABLE source_old RENAME TO source")
