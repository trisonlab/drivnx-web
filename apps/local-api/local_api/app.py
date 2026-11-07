import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from artifacts.settings import AiMode, WorkspaceSettings, read_settings, save_settings
from artifacts.store import WorkspaceBusy
from artifacts.workspace import Workspace
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, StringConstraints

from local_api.binding import LOOPBACK_ADDRESS
from local_api.findings_api import findings_router
from local_api.guard import SESSION_PATH, RequestGuard, SecurityHeaders
from local_api.profile_jobs import stop_jobs
from local_api.profile_runs_api import profile_runs_router
from local_api.session import LaunchSession
from local_api.sources_api import sources_router

UI_DIR = Path(__file__).parent / "ui"


class MissingUiBundleError(RuntimeError):
    pass


class SessionRequest(BaseModel):
    code: str


def _visible_text(value: str) -> str:
    """Refuse control characters, and names with nothing to see.

    Format characters stay allowed inside a name, because emoji sequences need zero-width joiners.
    """
    categories = {unicodedata.category(character) for character in value}
    if categories & {"Cc", "Cs", "Zl", "Zp"}:
        raise ValueError("must not contain control characters")
    if all(category[0] in "CZ" for category in categories):
        raise ValueError("must contain something visible")
    return value


class WorkspaceRequest(BaseModel):
    name: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=100),
        AfterValidator(_visible_text),
    ]
    ai_mode: AiMode


class WorkspaceResponse(BaseModel):
    location: str
    name: str | None
    ai_mode: AiMode
    setup_complete: bool


def create_app(
    session: LaunchSession,
    port: int,
    workspace: Workspace,
    ui_dir: Path | None = UI_DIR,
    dev_origin: str | None = None,
) -> FastAPI:
    """Build the app. In development Vite serves the UI, so `ui_dir` is None."""
    if ui_dir is not None and not (ui_dir / "index.html").is_file():
        raise MissingUiBundleError(
            f"The UI bundle is missing from {ui_dir}. Reinstall Conflux."
        )
    # The generated API docs are off: they load scripts from a CDN, and nothing may leave the machine.
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        # A job still reading as the process exits would hold DuckDB, whose binding then warns.
        stop_jobs()

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    host = f"{LOOPBACK_ADDRESS}:{port}"
    origins = {f"http://{host}"}
    if dev_origin is not None:
        origins.add(dev_origin)
    app.add_middleware(RequestGuard, session=session, host=host, origins=origins)
    # Added last, so it runs first and also covers the guard's refusals.
    app.add_middleware(SecurityHeaders)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Say what is wrong without copying the input, which may be huge, unencodable or a path."""
        problems = [
            {
                "field": ".".join(str(part) for part in problem["loc"]),
                "reason": problem["msg"],
            }
            for problem in error.errors()
        ]
        return JSONResponse({"detail": problems}, status_code=422)

    @app.exception_handler(WorkspaceBusy)
    async def busy(_request: Request, error: WorkspaceBusy) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=503)

    # `async` keeps checking and burning the code on the event loop, so two requests can't both pass.
    @app.post(SESSION_PATH, status_code=204)
    async def start_session(body: SessionRequest, response: Response) -> None:
        token = session.redeem(body.code)
        if token is None:
            raise HTTPException(status_code=401, detail="Launch code not valid")
        response.set_cookie(
            session.cookie_name, token, httponly=True, samesite="strict", path="/api"
        )

    @app.get(SESSION_PATH)
    async def session_status(request: Request) -> dict[str, bool]:
        token = request.cookies.get(session.cookie_name)
        return {"authenticated": session.is_valid_token(token)}

    def workspace_response(settings: WorkspaceSettings | None) -> WorkspaceResponse:
        return WorkspaceResponse(
            location=str(workspace.directory),
            name=settings.name if settings else None,
            ai_mode=settings.ai_mode if settings else "off",
            setup_complete=settings is not None,
        )

    @app.get("/api/workspace")
    def get_workspace() -> WorkspaceResponse:
        return workspace_response(read_settings(workspace.engine))

    @app.put("/api/workspace")
    def put_workspace(body: WorkspaceRequest) -> WorkspaceResponse:
        settings = WorkspaceSettings(body.name, body.ai_mode)
        save_settings(workspace.engine, settings)
        return workspace_response(settings)

    app.state.workspace = workspace
    app.include_router(sources_router(workspace))
    app.include_router(profile_runs_router(workspace))
    app.include_router(findings_router(workspace))

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True))
    return app
