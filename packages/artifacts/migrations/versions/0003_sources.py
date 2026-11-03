"""Connected sources and what a metadata scan found. Never any data rows (Section 5)."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE source (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('csv')),
            path TEXT NOT NULL UNIQUE,
            delimiter TEXT NOT NULL,
            encoding TEXT NOT NULL,
            has_header INTEGER NOT NULL CHECK (has_header IN (0, 1)),
            size_bytes INTEGER NOT NULL,
            modified_at TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE dataset (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES source (id),
            name TEXT NOT NULL
        )
        """
    )
    op.execute(
        """
        CREATE TABLE field (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL REFERENCES dataset (id),
            position INTEGER NOT NULL,
            name TEXT NOT NULL,
            physical_type TEXT NOT NULL,
            normalised_type TEXT NOT NULL,
            UNIQUE (dataset_id, position)
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE field")
    op.execute("DROP TABLE dataset")
    op.execute("DROP TABLE source")
