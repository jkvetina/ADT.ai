"""A flow that needs two calls to reach one database session says so (ADT #449).

Most of ADT asks a gateway one question at a time, and a transport is free to
answer each one however it likes. Two flows are not like that. `export_apex`
creates an `APEX_COLLECTION` in one call and reads `apex_collections` in the
next; `ut` starts a coverage profiler, runs the tests, and reads the profile
across three. Both are correct only if every call lands in one database session.

`sqlcl_only` on Windows cannot promise that. SQLcl draws no prompt inside a
Windows console, measured 2026-08-22 across six settings, so there is no process
to hold open and each request runs as its own script and therefore its own
session. Everything else survives that unchanged; these two do not.

**The refusal is the point.** Without it the collection query returns no rows and
the profiler returns no blocks, both at exit `0`, and `export_apex` writes that
emptiness to disk as the export. A transport that loses data quietly is worse
than one that cannot run, so the flows ask before they rely on it.

It sits here rather than inside the transport because only the caller knows that
its NEXT call depends on this one, and it is a plain function over a gateway
rather than a method so a fake gateway in a test needs nothing but an attribute.
"""

from __future__ import annotations

from typing import Any


class SessionScopeError(RuntimeError):
    """A session-scoped flow was asked of a transport that holds no session."""


def require_database_session(gateway: Any, feature: str) -> None:
    """Refuse `feature` when `gateway` cannot keep two calls in one session.

    A gateway that does not answer the question is taken to hold a session, which
    is the right default in both directions: python-oracledb holds one, and every
    gateway written before this attribute existed is a driver gateway.
    """
    if getattr(gateway, "holds_a_session", True):
        return
    raise SessionScopeError(
        f"{feature} needs two statements to reach one database session, and this "
        "run cannot give it one. `sqlcl_only` on Windows runs each statement as "
        "its own SQLcl script, because SQLcl does not open a driveable console "
        "there, so a collection or a profiler started by one statement is gone "
        "before the next one reads it. Turn `sqlcl_only` off for this command, "
        "or run it from macOS or Linux. Every other command is unaffected."
    )
