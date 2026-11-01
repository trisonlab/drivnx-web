from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from local_api.binding import LOOPBACK_ADDRESS
from local_api.guard import SESSION_PATH, RequestGuard, SecurityHeaders
from local_api.session import LaunchSession

UI_DIR = Path(__file__).parent / "ui"


class MissingUiBundleError(RuntimeError):
    pass


class SessionRequest(BaseModel):
    code: str


def create_app(
    session: LaunchSession,
    port: int,
    ui_dir: Path | None = UI_DIR,
    dev_origin: str | None = None,
) -> FastAPI:
    """Build the app. In development Vite serves the UI, so `ui_dir` is None."""
    if ui_dir is not None and not (ui_dir / "index.html").is_file():
        raise MissingUiBundleError(
            f"The UI bundle is missing from {ui_dir}. Reinstall Conflux."
        )
    # The generated API docs are off: they load scripts from a CDN, and nothing may leave the machine.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    host = f"{LOOPBACK_ADDRESS}:{port}"
    origins = {f"http://{host}"}
    if dev_origin is not None:
        origins.add(dev_origin)
    app.add_middleware(RequestGuard, session=session, host=host, origins=origins)
    # Added last, so it runs first and also covers the guard's refusals.
    app.add_middleware(SecurityHeaders)

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

    if ui_dir is not None:
        app.mount("/", StaticFiles(directory=ui_dir, html=True))
    return app
