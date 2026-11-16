"""A database's objects, their columns and declared keys, and which are chosen (CFX-APP-postgres).

Each object discovery finds is a dataset and its columns are fields, as a CSV file's are. Only
names are saved, never a value from the database (ADR 0004).
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added columns, not a rebuild: a CSV dataset or field has NULL in each of them.
    op.execute("ALTER TABLE source ADD COLUMN discovered_at TEXT")
    op.execute("ALTER TABLE dataset ADD COLUMN schema_name TEXT")
    op.execute("ALTER TABLE dataset ADD COLUMN object_name TEXT")
    # An object has a schema, a name, a kind and its read access, or a dataset has none of them.
    op.execute(
        """
        ALTER TABLE dataset ADD COLUMN object_kind TEXT CHECK (
            (object_kind IS NULL
                OR object_kind IN ('table', 'partitioned_table', 'view', 'materialized_view'))
            AND (object_kind IS NULL) = (schema_name IS NULL)
            AND (object_kind IS NULL) = (object_name IS NULL)
        )
        """
    )
    op.execute(
        "ALTER TABLE dataset ADD COLUMN estimated_rows INTEGER CHECK (estimated_rows >= 0)"
    )
    op.execute(
        """
        ALTER TABLE dataset ADD COLUMN readable INTEGER CHECK (
            (readable IS NULL OR readable IN (0, 1))
            AND (readable IS NULL) = (object_kind IS NULL)
        )
        """
    )
    # 0 for a materialized view never refreshed, which no one can read.
    op.execute(
        """
        ALTER TABLE dataset ADD COLUMN populated INTEGER CHECK (
            (populated IS NULL OR populated IN (0, 1))
            AND (populated IS NULL) = (object_kind IS NULL)
        )
        """
    )
    # Only an object the user can read can be chosen (AC-10).
    op.execute(
        "ALTER TABLE dataset ADD COLUMN chosen INTEGER NOT NULL DEFAULT 0 "
        "CHECK (chosen = 0 OR (chosen = 1 AND readable IS 1 AND populated IS 1))"
    )
    op.execute("ALTER TABLE field ADD COLUMN allows_nulls INTEGER CHECK (allows_nulls IN (0, 1))")
    # A CSV source keeps one current dataset; a database has one per object it lists.
    op.execute("DROP INDEX one_current_dataset")
    op.execute(
        "CREATE UNIQUE INDEX one_current_dataset ON dataset (source_id) "
        "WHERE replaced_at IS NULL AND object_name IS NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX one_current_object ON dataset (source_id, schema_name, object_name) "
        "WHERE replaced_at IS NULL AND object_name IS NOT NULL"
    )
    op.execute(
        """
        CREATE TABLE declared_key (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES dataset (id),
            kind TEXT NOT NULL CHECK (kind IN ('primary_key', 'unique', 'foreign_key')),
            name TEXT NOT NULL,
            columns TEXT NOT NULL CHECK (json_valid(columns)),
            referenced_schema TEXT,
            referenced_object TEXT,
            referenced_columns TEXT
                CHECK (referenced_columns IS NULL OR json_valid(referenced_columns)),
            -- Only a foreign key references something, and always a table and its columns.
            CHECK (
                (kind = 'foreign_key') = (referenced_object IS NOT NULL)
                AND (referenced_object IS NULL) = (referenced_schema IS NULL)
                AND (referenced_object IS NULL) = (referenced_columns IS NULL)
            )
        )
        """
    )
    op.execute("CREATE INDEX declared_key_dataset ON declared_key (dataset_id)")
    op.execute(
        """
        CREATE TABLE database_schema (
            source_id TEXT NOT NULL REFERENCES source (id),
            name TEXT NOT NULL,
            PRIMARY KEY (source_id, name)
        )
        """
    )


def downgrade() -> None:
    # An object's dataset has nowhere to go in the old schema, and runs may refer to it, so a
    # workspace with any is refused, not emptied, as 0007 refuses a PostgreSQL source.
    if (
        op.get_bind()
        .exec_driver_sql("SELECT 1 FROM dataset WHERE object_name IS NOT NULL LIMIT 1")
        .first()
    ):
        raise RuntimeError("This workspace has a database's tables and views, so it stays at 0008.")
    op.execute("DROP TABLE database_schema")
    op.execute("DROP TABLE declared_key")
    op.execute("DROP INDEX one_current_object")
    op.execute("DROP INDEX one_current_dataset")
    op.execute(
        "CREATE UNIQUE INDEX one_current_dataset ON dataset (source_id) "
        "WHERE replaced_at IS NULL"
    )
    op.execute("ALTER TABLE field DROP COLUMN allows_nulls")
    for column in (
        "chosen",
        "populated",
        "readable",
        "estimated_rows",
        "object_kind",
        "object_name",
        "schema_name",
    ):
        op.execute(f"ALTER TABLE dataset DROP COLUMN {column}")
    op.execute("ALTER TABLE source DROP COLUMN discovered_at")
