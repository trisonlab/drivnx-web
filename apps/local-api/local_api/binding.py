import os
import socket

LOOPBACK_ADDRESS = "127.0.0.1"


class NonLoopbackBindError(ValueError):
    pass


def bind_listener(host: str = LOOPBACK_ADDRESS, port: int = 0) -> socket.socket:
    """Return a listening socket on the loopback address. Port 0 lets the OS pick a free port."""
    if host != LOOPBACK_ADDRESS:
        raise NonLoopbackBindError(
            f"DrivnX only binds to {LOOPBACK_ADDRESS}, not {host!r}"
        )
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Lets a restart reuse a port in TIME_WAIT. On Windows this option would instead let
        # another process take a port that is in use, and Windows doesn't need it.
        if os.name != "nt":
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, port))
        listener.listen()
    except OSError:
        listener.close()
        raise
    return listener
