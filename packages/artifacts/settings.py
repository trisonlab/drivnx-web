"""The workspace's own settings. No row means first-launch setup hasn't happened yet."""

from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Engine, text

AiMode = Literal["off", "external", "local"]


@dataclass(frozen=True)
class WorkspaceSettings:
    name: str
    ai_mode: AiMode


def read_settings(engine: Engine) -> WorkspaceSettings | None:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT name, ai_mode FROM workspace_settings WHERE id = 1")
        ).first()
    return WorkspaceSettings(row.name, row.ai_mode) if row else None


def save_settings(engine: Engine, settings: WorkspaceSettings) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO workspace_settings (id, name, ai_mode)
                VALUES (1, :name, :ai_mode)
                ON CONFLICT (id) DO UPDATE SET name = :name, ai_mode = :ai_mode
                """
            ),
            {"name": settings.name, "ai_mode": settings.ai_mode},
        )
