from starlette.datastructures import Headers, MutableHeaders
from starlette.requests import cookie_parser
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from local_api.session import LaunchSession

SESSION_PATH = "/api/session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
# No page may frame the UI, and the page may load nothing from outside this origin.
CONTENT_SECURITY_POLICY = "default-src 'self'; frame-ancestors 'none'"


class SecurityHeaders:
    """Adds the browser protections to every response."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self._app(scope, receive, send_with_headers)


class RequestGuard:
    """Refuses `/api` requests that fail the checks in ADR 0001, before any route runs."""

    def __init__(
        self, app: ASGIApp, session: LaunchSession, host: str, origins: set[str]
    ) -> None:
        self._app = app
        self._session = session
        self._host = host
        self._origins = origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] == "http" and (path == "/api" or path.startswith("/api/")):
            refusal = self._refusal(scope, path)
            if refusal is not None:
                await refusal(scope, receive, send)
                return
        await self._app(scope, receive, send)

    def _refusal(self, scope: Scope, path: str) -> JSONResponse | None:
        headers = Headers(scope=scope)
        if headers.get("host") != self._host:
            return JSONResponse({"detail": "Host not allowed"}, status_code=403)

        origin = headers.get("origin")
        if origin is None:
            if scope["method"] not in SAFE_METHODS:
                return JSONResponse({"detail": "Origin required"}, status_code=403)
        elif origin not in self._origins:
            return JSONResponse({"detail": "Origin not allowed"}, status_code=403)

        if path != SESSION_PATH:
            cookies = cookie_parser(headers.get("cookie", ""))
            if not self._session.is_valid_token(cookies.get(self._session.cookie_name)):
                return JSONResponse({"detail": "Not signed in"}, status_code=401)
        return None
