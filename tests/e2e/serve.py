"""The studio for the browser tests, behind the loopback-only guard.
usage: python -m tests.e2e.serve PORT"""

import sys

import uvicorn

from tests.e2e.guard import install

if __name__ == "__main__":
    install()
    uvicorn.run("app:app", host="127.0.0.1", port=int(sys.argv[1]), log_level="warning")
