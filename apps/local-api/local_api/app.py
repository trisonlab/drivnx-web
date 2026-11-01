from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

UI_DIR = Path(__file__).parent / "ui"


class MissingUiBundleError(RuntimeError):
    pass


def create_app(ui_dir: Path = UI_DIR) -> FastAPI:
    if not (ui_dir / "index.html").is_file():
        raise MissingUiBundleError(
            f"The UI bundle is missing from {ui_dir}. Reinstall Conflux."
        )
    # The generated API docs are off: they load scripts from a CDN, and nothing may leave the machine.
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.mount("/", StaticFiles(directory=ui_dir, html=True))
    return app
