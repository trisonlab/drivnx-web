import argparse
import threading
import webbrowser

import uvicorn

from local_api.app import MissingUiBundleError, create_app
from local_api.binding import LOOPBACK_ADDRESS, bind_listener


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


def _print_url(url: str) -> None:
    print(url, flush=True)


def _open_browser_or_print_url(url: str) -> None:
    if not webbrowser.open(url):
        _print_url(url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="conflux")
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
    args = parser.parse_args()

    try:
        app = create_app()
    except MissingUiBundleError as error:
        raise SystemExit(str(error)) from None

    try:
        listener = bind_listener(port=args.port)
    except OSError as error:
        raise SystemExit(
            f"Port {args.port} is not available: {error.strerror}"
        ) from None

    url = f"http://{LOOPBACK_ADDRESS}:{listener.getsockname()[1]}/"
    if args.no_browser:
        _print_url(url)
    else:
        # Some browser commands only return when the browser exits, so this can't hold up the server.
        threading.Thread(
            target=_open_browser_or_print_url, args=(url,), daemon=True
        ).start()

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    server.run(sockets=[listener])
