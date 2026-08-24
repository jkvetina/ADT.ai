"""The ways a SQLcl script run fails, as their own classes.

Split out of ``shared.sqlcl_script`` (ADT #457) so ``shared.sqlcl_stream`` can
raise a timeout without importing the module that imports it. Every name here
is re-exported from ``shared.sqlcl_script``, which is where callers have always
imported them from and where they stay.
"""

from __future__ import annotations


class SqlclNotConnectedError(RuntimeError):
    """SQLcl ran the whole script without ever holding a session."""


class SqlclTimeoutError(RuntimeError):
    """SQLcl outlived the deadline the caller gave it and was killed."""


class SqlclScriptError(RuntimeError):
    """SQLcl failed; the message carries its whole captured transcript.

    Named so the CLI can tell it apart from an internal surprise. As a bare
    ``RuntimeError`` it landed under the ``UNEXPECTED ERROR:`` catch-all, which
    renders ``<type>: <message>`` on one line and so promoted the transcript's
    FIRST line to the diagnosis, reporting `Connection <name> has been deleted`
    (SQLcl echoing the script's own `CONNMGR DELETE` preamble) for a deploy that
    actually died on `SP2-0556` several lines later (ADT #271).

    Two shapes reach it: a non-zero exit, and a run that exited **0** without
    ever getting past the JVM (ADT #457).
    """
