"""The studio for the browser tests, behind the loopback-only guard. It binds a free loopback
port itself and prints the number, so no other process can take the port in between.

Page scripts are answered after a short delay, as on a slow machine: a test that acts on a page
before its script has run then fails here too, not only in CI.
usage: python -m tests.e2e.serve"""

import asyncio
import socket

import uvicorn

from tests.e2e.guard import install

SCRIPT_DELAY = 0.25  # seconds


def slow_scripts(app):
    async def delayed(scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] == "http" and path.startswith("/static/") and path.endswith(".js"):
            await asyncio.sleep(SCRIPT_DELAY)
        await app(scope, receive, send)

    return delayed


if __name__ == "__main__":
    install()
    from app import app

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1], flush=True)
    config = uvicorn.Config(slow_scripts(app), log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[listener])
