"""The write access a database's user last confirmed profiling with (CFX-APP-postgres AC-16).

A database whose role can change the chosen objects is profiled only once the user confirms, and
asked again only when the chosen objects or the role's privileges change (DRIVN-52). What was
confirmed is kept with the source: names and privileges, never a value from the database.
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Added, not a rebuild: every source a workspace already has is NULL, never confirmed. Only a
    # database's user is asked, so a CSV source never has one.
    op.execute(
        """
        ALTER TABLE source ADD COLUMN write_confirmed TEXT CHECK (
            write_confirmed IS NULL OR (kind = 'postgres' AND json_valid(write_confirmed))
        )
        """
    )


def downgrade() -> None:
    # Only a confirmation is lost, and its user is asked again.
    op.execute("ALTER TABLE source DROP COLUMN write_confirmed")
