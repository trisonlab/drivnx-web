"""CFX-APP-first-slice/AC-07: the runtime refuses to bind to a non-loopback address."""

import socket

import pytest
from local_api.binding import NonLoopbackBindError, bind_listener


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "::",
        "192.168.1.10",
        "10.0.0.1",
        "example.com",
        "",
        # Names can resolve anywhere, and ADR 0001's Host check expects exactly 127.0.0.1.
        "localhost",
    ],
)
def test_refuses_anything_but_the_loopback_address(host: str) -> None:
    with pytest.raises(NonLoopbackBindError):
        bind_listener(host)


def test_binds_to_the_loopback_address_and_accepts_connections_straight_away() -> None:
    # The launch URL goes out before the server loop starts, so connections must already queue.
    with bind_listener() as listener:
        host, port = listener.getsockname()
        assert host == "127.0.0.1"
        assert port != 0
        with socket.create_connection((host, port), timeout=1):
            pass
