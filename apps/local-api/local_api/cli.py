import argparse
import os
import re
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn
from artifacts.profiles import mark_interrupted_runs
from artifacts.store import WorkspaceBusy
from artifacts.workspace import (
    WorkspaceError,
    default_workspace_directory,
    open_workspace,
    sync_folder_in,
)

from local_api.app import UI_DIR, MissingUiBundleError, create_app
from local_api.binding import LOOPBACK_ADDRESS, bind_listener
from local_api.session import LaunchSession


def _port_number(value: str) -> int:
    try:
        port = int(value)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            f"{value!r} is not a port number from 0 to 65535"
        )
    return port


def _dev_origin(value: str) -> str:
    if not re.fullmatch(rf"http://{re.escape(LOOPBACK_ADDRESS)}:\d+", value):
        raise argparse.ArgumentTypeError(
            f"{value!r} is not an origin like http://{LOOPBACK_ADDRESS}:5173"
        )
    return value


def _print_url(url: str) -> None:
    print(url, flush=True)


def _browser_can_run_the_app() -> bool:
    """Without a display, Python would fall back to a text browser, which can't run the UI."""
    return (
        sys.platform in ("win32", "darwin")
        or bool(os.environ.get("BROWSER"))
        or bool(os.environ.get("DISPLAY"))
        or bool(os.environ.get("WAYLAND_DISPLAY"))
    )


def _open_browser_or_print_url(url: str) -> None:
    if not webbrowser.open(url):
        _print_url(url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="drivnx")
    parser.add_argument(
        "workspace",
        nargs="?",
        type=Path,
        metavar="WORKSPACE_DIR",
        help="the workspace to open (default: ~/DrivnX/default)",
    )
    parser.add_argument(
        "--log-level",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="warning",
        help="how much the runtime logs (default: warning)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="print the URL instead of opening a browser",
    )
    parser.add_argument(
        "--port",
        type=_port_number,
        default=0,
        help="port to listen on (default: a free port)",
    )
    parser.add_argument(
        "--dev-origin",
        type=_dev_origin,
        help="development only: the Vite dev server that serves the UI and proxies /api",
    )
    args = parser.parse_args()

    directory = args.workspace or default_workspace_directory()
    try:
        # Holds the lock until the process ends, so nothing else can open it (ADR 0001).
        workspace = open_workspace(directory)
        mark_interrupted_runs(workspace.engine)
    except (WorkspaceError, WorkspaceBusy) as error:
        raise SystemExit(str(error)) from None
    if folder := sync_folder_in(directory):
        print(
            f"Warning: this workspace is in a sync folder ({folder}). "
            "Sync services can copy the database mid-write, so keep workspaces on a local disk.",
            file=sys.stderr,
            flush=True,
        )

    try:
        listener = bind_listener(port=args.port)
    except OSError as error:
        raise SystemExit(
            f"Port {args.port} is not available: {error.strerror}"
        ) from None
    port = listener.getsockname()[1]

    session = LaunchSession(port)
    try:
        app = create_app(
            session,
            port,
            workspace,
            ui_dir=None if args.dev_origin else UI_DIR,
            dev_origin=args.dev_origin,
        )
    except MissingUiBundleError as error:
        raise SystemExit(str(error)) from None

    ui_origin = args.dev_origin or f"http://{LOOPBACK_ADDRESS}:{port}"
    url = f"{ui_origin}/#launch={session.launch_code}"
    print("DrivnX is running. Press Ctrl+C to stop.", flush=True)
    if args.no_browser or not _browser_can_run_the_app():
        _print_url(url)
    else:
        print("If no browser opened, run it again with --no-browser.", flush=True)
        # Some browser commands only return when the browser exits, so this can't hold up the server.
        threading.Thread(
            target=_open_browser_or_print_url, args=(url,), daemon=True
        ).start()

    server = uvicorn.Server(uvicorn.Config(app, log_level=args.log_level))
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        # Uvicorn shuts down cleanly, then raises the signal again. Ctrl+C is a normal way to stop.
        pass
