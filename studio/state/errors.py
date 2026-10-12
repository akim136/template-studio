"""Outcomes the API maps to HTTP statuses. `StorageError` (503) lives with the store."""

from __future__ import annotations


class NotFound(LookupError):
    """404."""


class Invalid(ValueError):
    """422: the request can never succeed as sent."""

    def __init__(self, errors: list[str]) -> None:
        super().__init__("; ".join(errors))
        self.errors = errors


class Conflict(Exception):
    """409: the request was based on state that is no longer current. Carries the current state."""

    def __init__(self, reason: str, current: dict) -> None:
        super().__init__(reason)
        self.reason = reason
        self.current = current
