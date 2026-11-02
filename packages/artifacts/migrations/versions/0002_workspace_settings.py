"""The workspace's name and AI mode. One row, which exists once setup is complete."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE workspace_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            name TEXT NOT NULL CHECK (length(name) > 0),
            ai_mode TEXT NOT NULL CHECK (ai_mode IN ('off', 'external', 'local'))
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE workspace_settings")
