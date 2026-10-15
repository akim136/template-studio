"""The studio for the browser tests, behind the loopback-only guard. It binds a free loopback
port itself and prints the number, so no other process can take the port in between.
usage: python -m tests.e2e.serve"""

import socket

import uvicorn

from tests.e2e.guard import install

if __name__ == "__main__":
    install()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    print(listener.getsockname()[1], flush=True)
    config = uvicorn.Config("app:app", log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[listener])
