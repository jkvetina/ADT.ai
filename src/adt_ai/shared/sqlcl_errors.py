"""The ways a SQLcl script run fails, as their own classes.

Split out of ``shared.sqlcl_script`` (ADT #457) so ``shared.sqlcl_stream`` can
raise a timeout without importing the module that imports it. Every name here
is re-exported from ``shared.sqlcl_script``, which is where callers have always
imported them from and where they stay.
"""

from __future__ import annotations


class SqlclScriptError(RuntimeError):
    """SQLcl failed; the message carries its whole captured transcript.

    Named so the CLI can tell it apart from an internal surprise. As a bare
    ``RuntimeError`` it landed under the ``UNEXPECTED ERROR:`` catch-all, which
    renders ``<type>: <message>`` on one line and so promoted the transcript's
    FIRST line to the diagnosis, reporting `Connection <name> has been deleted`
    (SQLcl echoing the script's own `CONNMGR DELETE` preamble) for a deploy that
    actually died on `SP2-0556` several lines later (ADT #271).

    Two shapes reach it: a non-zero exit, and a run that exited **0** without
    ever getting past the JVM (ADT #457). The two classes below are kinds of it.
    """


class SqlclNotConnectedError(SqlclScriptError):
    """SQLcl ran the whole script without ever holding a session.

    A kind of script failure, so the CLI reports it the way it reports any other
    (ADT #923). As a sibling it fell through to ``UNEXPECTED ERROR:`` and printed
    the class name above the transcript. It stays a class of its own because the
    named-connection retry answers this failure and no other.
    """


class SqlclTimeoutError(SqlclScriptError):
    """SQLcl outlived the deadline the caller gave it and was killed.

    A kind of script failure for the same reason (ADT #923): the message carries
    what SQLcl printed before it was killed, and the CLI reports it as such.
    """
