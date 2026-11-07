"""What kind each finding is, so findings are listed in a fixed order (CFX-APP-csv-profile)."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLite only adds a NOT NULL column with a default, and no kind is right for every finding.
    # No workspace had a finding before this, and the code always gives one.
    op.execute(
        "ALTER TABLE finding ADD COLUMN kind TEXT CHECK (kind IN ("
        "'possibly_sensitive', 'not_fitting', 'duplicate_rows', 'missing_values', "
        "'possible_identifier', 'single_value'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE finding DROP COLUMN kind")
