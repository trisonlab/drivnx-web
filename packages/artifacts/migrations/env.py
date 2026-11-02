"""Run by Alembic. The connection and its transaction come from `artifacts.schema`."""

from alembic import context

context.configure(
    connection=context.config.attributes["connection"],
    # SQLite can't alter most column and constraint definitions in place (ADR 0002 point 6).
    render_as_batch=True,
)
context.run_migrations()
