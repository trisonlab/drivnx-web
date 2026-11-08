import secrets
import time
from collections.abc import Callable

LAUNCH_CODE_LIFETIME_SECONDS = 120


class LaunchSession:
    """The per-launch secrets from ADR 0001. They live in memory only, and die with the process."""

    def __init__(self, port: int, now: Callable[[], float] = time.monotonic) -> None:
        self.cookie_name = f"drivnx_session_{port}"
        self.launch_code: str = secrets.token_urlsafe(32)
        self._token = secrets.token_urlsafe(32)
        self._now = now
        self._code_expires_at = now() + LAUNCH_CODE_LIFETIME_SECONDS
        self._code_used = False

    def redeem(self, code: str) -> str | None:
        """Return the session token if the code is valid and unused, and burn the code."""
        if (
            self._code_used
            or self._now() >= self._code_expires_at
            or not secrets.compare_digest(
                code.encode(errors="replace"), self.launch_code.encode()
            )
        ):
            return None
        self._code_used = True
        return self._token

    def is_valid_token(self, token: str | None) -> bool:
        return token is not None and secrets.compare_digest(
            token.encode(), self._token.encode()
        )
