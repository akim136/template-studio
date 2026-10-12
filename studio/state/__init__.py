"""Saved state: sealed sources, template revisions, review and approval, and signing operations.

Every change goes through a conditional write to an object store, so a stale writer is refused
rather than silently overwriting newer work. The only backend is local disk; the R2 backend waits
on Alex's account and bucket decision (guide §10).
"""
