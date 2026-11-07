import threading

import pytest


@pytest.fixture
def escaped_errors(monkeypatch: pytest.MonkeyPatch) -> list[BaseException | None]:
    """Errors that left a job's thread. Python prints these, and pytest would otherwise take them."""
    escaped: list[BaseException | None] = []
    monkeypatch.setattr(
        threading, "excepthook", lambda args: escaped.append(args.exc_value)
    )
    return escaped
