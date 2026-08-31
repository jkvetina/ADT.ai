"""Whether the screen currently says what the process is about to do.

`#359` gave this rule its first measurement and drew the line in the wrong
place. That check read the runtime's pending-newline count and fired only on
`>= 2`, a blank line as the last thing the terminal received, because a count of
`1` cannot tell a section header from a finished data row and the module refused
to guess. So a command that blocked with the cursor sitting under its last
result row passed, which is the shape Jan reported next (`#360`, 2026-08-15):

    *"if there is code running, it is preceeding header must have been printed,
    otherwise it is a violation. At least the headers should be printed right
    away so user know what is happenning. Most of the time you stop on the last
    line of previous block, for example when you connect to database, instead of
    printing as much as possible to the user and filling up the gaps as you
    go."*

The fix is to stop classifying the text and read the cursor instead. Two shapes
announce, and between them they are the whole console contract:

* **An open line.** A label streamed with no newline after it is a label waiting
  for its own result, which is what `FixedWidthProgressPrinter.begin`, the
  dotted progress bar and a streamed table half-row each leave on screen.
  Nothing has to opt in, because leaving the line open IS the announcement.
  It announces the work that will **close** it and nothing after: a redrawable
  row that has reached its own end says so through `mark_finished()`, because
  the cursor cannot tell `12%` from `100%` and `ut` spent half a run behind
  the latter (`#379`).
* **A section header.** It ends its line like any finished row, so the cursor
  cannot see it and `print_adt_header` says so through `mark_announced()`. Jan:
  *"at least the headers"*.

Everything else is a result: a completed row, a rendered table, the blank line
that closes a section. The state itself lives on the runtime's `_StdoutTracker`
(`cli/constants.py`), which is the only thing that knows where the cursor got to.

Two limits, written here rather than discovered later:

* The guard is armed on the **gateway**, so it covers Oracle and SQLcl and
  nothing else. Git walks, file scans and subprocess work block just as visibly
  and are not seen; `rebuild` and `search_repo` are the commands that spend real
  time outside a gateway.
* It can only judge when the CLI's own `_StdoutTracker` is installed on
  `sys.stdout`. A unit test that drives a runner directly gets no verdict rather
  than a false one.
"""

from __future__ import annotations

import os
import sys

STRICT_ENV_FLAG = "ADT_STRICT_CONSOLE"


# Every violation seen since the last `reset_violations()`, in order. The guard
# records rather than raises, and `tests/conftest.py` fails the test on whatever
# it collected.
#
# Raising was tried first and is the wrong shape twice over. An `Exception` is
# caught by the CLI's own top-level handler and rendered as a friendly error
# screen, so the test reports the disguise instead of the defect; a
# `BaseException` escapes that, and then escapes `multiprocessing.pool.worker`
# too, which only catches `Exception`. `export_apex` runs each export action in
# a `ThreadPool` and polls `result.ready()`, so the worker died without ever
# posting a result and the whole command hung at 0% CPU with the progress bar
# on screen. A guard that can wedge the thing it is watching is worse than the
# defect it was written for. Recording also reports every violation in a run
# instead of stopping at the first.
_violations: list[str] = []


def reset_violations() -> None:
    _violations.clear()


def violations() -> list[str]:
    return list(_violations)


def strict_mode() -> bool:
    """Enforce rather than observe. Set for the whole test suite, off in a run.

    A live run gains nothing from crashing on a defect Jan can already see: the
    screen is the report. The suite is where the rule has to bite, so that is
    where it is armed, and `tests/conftest.py` arms it once for every test.
    """
    return os.environ.get(STRICT_ENV_FLAG) == "1"


def _tracker() -> object | None:
    """The runtime's stdout wrapper, or `None` when the CLI is not printing."""
    return sys.stdout if hasattr(sys.stdout, "announced") else None


def settle_screen_before_error() -> None:
    """Leave stdout one blank line short of an error banner (ADT #465).

    An error screen writes to **stderr** while the run's output went to stdout,
    and the runtime's stdout tracker holds trailing newlines back so the shared
    `TIMER` footer can still retract them. Its stderr counterpart already commits
    those before its first write, which covers a run that ended on a finished
    section; it cannot cover a run that ended MID-ROW, because a redrawable row
    carries no newline at all while it crawls. There is nothing pending to
    commit, so `print_adt_header`'s own leading blank is spent terminating that
    row and the banner lands flush against it. Jan, 2026-08-21, on a failed
    deploy: *"when deploy fails, there are no blank lines above the header"*.

    `#232` fixed the same defect for one caller by teaching the bar to complete
    itself with `FAILED`; a failure raised anywhere else never reaches that call,
    so this belongs on the error screen, where all four of them pass.

    Two trailing newlines is one blank line and `print_adt_header` adds the
    second, so the banner opens on exactly two whatever the screen was doing.
    `normalize_trailing_newlines` is the right tool rather than a bare `print()`
    because it **caps as well as pads**: a section that already closed itself
    gets nothing added, so this cannot turn two blanks into four, which is
    `#269`'s defect in the other direction.

    Lives here rather than beside the tracker because this module already owns
    the question "what is the screen currently saying", and reads the same
    duck-typed stdout through the same `getattr` guards.
    """
    stream = sys.stdout
    normalize = getattr(stream, "normalize_trailing_newlines", None)
    commit = getattr(stream, "commit_pending", None)
    if callable(normalize):
        normalize(2)
    if callable(commit):
        commit()


def mark_announced() -> None:
    """What was just printed says what is about to happen.

    Only `print_adt_header` needs this. A streamed label announces itself by
    leaving its line open, so marking it would be a second telling of something
    the cursor already says.
    """
    output = _tracker()
    if output is not None:
        output.mark_announced()


def mark_finished() -> None:
    """The open line on screen has finished the work it was labelling.

    A redrawable row is the one open line that outlives its own wait: the
    carriage return keeps the cursor on it, so it reads as an announcement until
    something closes it. `ut` closed its bar only after the coverage read for
    exactly that reason and spent half the run parked on `100%  0:00:00`
    (`#379`). A bar draws 100% without closing only once its units are all
    accounted for, so `DottedProgressBar.print_line` calls this and no command
    has to opt in.
    """
    output = _tracker()
    if output is not None:
        output.mark_finished()


def is_announced() -> bool:
    """Is an announcement the newest thing on screen?

    `True` when the runtime is not driving the stream: no tracker means no
    verdict, and a guess would be a false failure rather than a finding.
    """
    output = _tracker()
    return True if output is None else bool(output.announced)


def guard(operation: object) -> None:
    """Record a blocking call the screen has not accounted for."""
    if is_announced() or not strict_mode():
        return
    _violations.append(f"{_caller()}  {first_line(operation)}")


def _caller() -> str:
    """The first frame outside this module, so a report says where to fix it.

    Only walked when a violation is being recorded. The statement alone names
    the symptom; the call site is what the next person actually needs, and
    hunting it through a pytest traceback is what made the first sweep slow.
    """
    frame = sys._getframe(1)
    while frame is not None:
        if frame.f_globals.get("__name__") != __name__:
            name = frame.f_globals.get("__name__", "?")
            return f"{name}:{frame.f_lineno}"
        frame = frame.f_back
    return "?"  # pragma: no cover, unreachable: guard() always has a caller outside this module


def first_line(operation: object) -> str:
    """A statement's first meaningful line, so a failure names it readably."""
    for line in str(operation).splitlines():
        if line.strip():
            return line.strip()[:70]
    return "<empty statement>"


class AnnouncedGateway:
    """A gateway that refuses to work behind the user's back.

    A wrapper rather than a change to `OracleGateway`, so the same guard covers
    the real gateway and every fake a command's own tests hand it, and so a
    normal run carries none of it: `build_gateway` and `cli.runtime.main` apply
    this only under `strict_mode()`.
    """

    def __init__(self, wrapped: object) -> None:
        self.wrapped = wrapped

    def connect(self, *args, **kwargs):
        guard("connect")
        return self.wrapped.connect(*args, **kwargs)

    def fetch_all(self, sql, params=None):
        guard(sql)
        return self.wrapped.fetch_all(sql, params)

    def read_only_fetch_all(self, sql, params=None):
        guard(sql)
        return self.wrapped.read_only_fetch_all(sql, params)

    def execute(self, sql, params=None):
        guard(sql)
        return self.wrapped.execute(sql, params)

    def sqlcl_request(self, request, *args, **kwargs):
        guard(request)
        return self.wrapped.sqlcl_request(request, *args, **kwargs)

    def close(self):
        return self.wrapped.close()

    def __reduce__(self):
        """Survive being sent to a worker process, wrapper and all.

        `export_apex` runs each export action in a `multiprocessing.Pool`, which
        pickles the gateway. Rebuilding through the constructor keeps `wrapped`
        set; the default path would have handed the *wrapped* object's state to
        an `AnnouncedGateway`, leaving an instance with no `wrapped` attribute
        whose every lookup recursed. The symptom is not an error: the worker
        dies during unpickling and the parent waits on a result that never
        arrives, so the command hangs at 0% CPU with the progress bar drawn.
        """
        return (self.__class__, (self.wrapped,))

    def __getattr__(self, name: str) -> object:
        # Dunders are never delegated. `__getattr__` runs only for attributes
        # normal lookup missed, and pickle probes exactly those (`__getstate__`,
        # `__setstate__`, `__deepcopy__`): answering for the wrapped object
        # makes a protocol the wrapper does not implement look implemented.
        if name.startswith("__") and name.endswith("__"):
            raise AttributeError(name)
        return getattr(self.wrapped, name)


def unwrap(gateway: object) -> object:
    """The gateway underneath the guard, for a test asserting on identity."""
    return gateway.wrapped if isinstance(gateway, AnnouncedGateway) else gateway


def announced_factory(factory):
    """Wrap whatever gateway a factory returns, keeping its call shape."""

    def build(*args, **kwargs):
        return AnnouncedGateway(factory(*args, **kwargs))

    return build
