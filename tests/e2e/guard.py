"""Loopback-only sockets, for the browser tests' own process and the server process they start."""

import socket
from collections.abc import Callable

LOOPBACK = ("127.0.0.1", "::1", "localhost")


def loopback_only(real: Callable, at: int) -> Callable:
    """Wraps a connect function so any address but loopback fails; `at` is the address argument."""

    def guarded(*args: object, **kwargs: object) -> object:
        target = args[at]
        host = target[0] if isinstance(target, tuple) else target
        if host not in LOOPBACK:
            raise AssertionError("outbound network access attempted during a test")
        return real(*args, **kwargs)

    return guarded


def install(patch: Callable = setattr) -> None:
    patch(socket, "create_connection", loopback_only(socket.create_connection, 0))
    patch(socket.socket, "connect", loopback_only(socket.socket.connect, 1))
    patch(socket.socket, "connect_ex", loopback_only(socket.socket.connect_ex, 1))
