"""Corrected read settings, and one current dataset per source (CFX-APP-csv-profile)."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NULL means the lines above the table are worked out on every read.
    op.execute(
        "ALTER TABLE source ADD COLUMN skip_lines INTEGER "
        "CHECK (skip_lines BETWEEN 0 AND 1000)"
    )
    op.execute(
        "ALTER TABLE source ADD COLUMN corrected INTEGER NOT NULL DEFAULT 0 "
        "CHECK (corrected IN (0, 1))"
    )
    op.execute("ALTER TABLE dataset ADD COLUMN replaced_at TEXT")
    op.execute(
        "CREATE UNIQUE INDEX one_current_dataset ON dataset (source_id) "
        "WHERE replaced_at IS NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX one_current_dataset")
    op.execute("ALTER TABLE dataset DROP COLUMN replaced_at")
    op.execute("ALTER TABLE source DROP COLUMN corrected")
    op.execute("ALTER TABLE source DROP COLUMN skip_lines")
