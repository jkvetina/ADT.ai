"""What the deploy lock writes: the import log row, the console row, the timeline.

Split out of `apex_lock` when ADT #1056 pushed that module past the 24 KB
context guard: the lock decides, this renders, and none of it opens a database.
`apex_lock` re-exports every name here, so no caller learns a new import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from adt_ai.patch.apex_app_lock import timeline_rows

if TYPE_CHECKING:
    from adt_ai.patch.apex_lock import BuildStatusLock

#: The status the build-status lock puts on. Both edges name it, so it is one
#: constant, and it lives with the text that spells it.
RUN_ONLY = "RUN_ONLY"

#: The column this block's rows line up on, shared with the signature and
#: `BACKUP` rows so a reader meets one table rather than three that nearly agree.
_ROW_WIDTH = 16

#: What the import log's `BUILD STATUS` row says when nothing was locked. Never
#: blank, for the reason `apex_backup._NO_BACKUP` is never blank: an empty value
#: reads as a row the log failed to write.
_NO_LOCK = "(not locked)"


def build_status_line(lock: BuildStatusLock) -> str:
    """The import log's `BUILD STATUS` row: the lock standing over this import.

    Written for every application the deploy TRIED to lock, the ones it then
    skipped included, because a reader asking whether the application was held
    needs to be told it was not rather than left to notice a missing row. An
    application the deploy never considered -- a retargeted sandbox, or any
    application at all under `off` -- has no entry and gets no row, which is what
    keeps `off` byte for byte the deploy it was before ADT #726.

    A 26.2+ target that fell back to `RUN_ONLY` says why after the row's value
    (ADT #1056); below 26.2 there is no reason, so that row is unchanged.
    """
    if not lock.locked:
        return _row("BUILD STATUS", f"{_NO_LOCK} {lock.reason}".strip())
    if lock.app_lock.held:
        return _row("BUILD STATUS", f"{held_by(lock)} (build status {lock.before})")
    why = f"; {lock.reason}" if lock.reason else ""
    return _row("BUILD STATUS", f"{RUN_ONLY} (was {lock.before}){why}")


def build_status_timeline(lock: BuildStatusLock) -> str:
    """The deploy console's `BUILD STATUS:` value: where the application went.

    Three moments rather than the log's four (ADT #720): what it was, the lock
    this deploy put on, and what it is left on. `AFTER IMPORT` is APEX resetting
    the status on its own, a fact about APEX rather than about this deploy, and
    it is in the timeline file for a reader who wants it.

    An application nothing locked answers "" and prints no row at all. Jan,
    2026-09-09, on a `(not locked)` row: *"dont show, it is a noise"* -- that
    row would be a line per unlocked application saying nothing happened.

    The vocabulary is the timeline file's own, display text either side of the
    API value `RUN_ONLY`, because the row summarises that file and a second
    spelling of the same three values is how the two start disagreeing. The
    application lock (ADT #1056) reads `APP LOCK <user>` in the middle.
    """
    if not lock.locked:
        return ""
    return " -> ".join((
        lock.before or "(no application)",
        held_by(lock),
        lock.final or "(not set)",
    ))


def build_status_log_text(lock: BuildStatusLock) -> str:
    """The lock's own report: the four moments, in the order they happened.

    The timeline is the point. `BEFORE DEPLOY` and `LOCKED` are what the deploy
    did to hold the application shut, `AFTER IMPORT` is APEX resetting it on its
    own, and `FINAL` is where the deploy left it -- so a reader can tell a lock
    that never went on from one that did and was reset from one that was
    restored, without reading the source to find out which is which. Where the
    application lock was in play (ADT #1056), who held it before the deploy and
    how the release left it follow as two more rows.
    """
    lines = [
        f"-- APEX application {lock.app_id} and its build status across this deploy.",
        "--",
        "-- The signature is read, then the lock set, so the window between the check",
        "-- and the import is covered. RUN_ONLY refuses Builder ENTRY, not a save from",
        "-- a session that is already open, and the APEXlang import resets the status",
        "-- on its own, which is why FINAL is set as the last step.",
        "",
        _row("APPLICATION", str(lock.app_id)),
        _row("MODE", lock.mode),
        _row("STATUS", lock.outcome),
        _row("BEFORE DEPLOY", lock.before or "(no application)"),
        _row("LOCKED", held_by(lock) if lock.locked else _NO_LOCK),
        _row("AFTER IMPORT", lock.after or "(not read)"),
        _row("FINAL", lock.final or "(not set)"),
        *(_row(name, value) for name, value in timeline_rows(lock.app_lock)),
    ]
    if lock.reason:
        lines.append(f"{lock.outcome}: {lock.reason}")
    return "\n".join(lines) + "\n"


def held_by(lock: BuildStatusLock) -> str:
    """`RUN_ONLY`, `APP LOCK <user>`, or `PAGE LOCKS <user>` below 26.2 (ADT #1059)."""
    if not lock.app_lock.held:
        return RUN_ONLY
    kind = "PAGE LOCKS" if lock.app_lock.pages else "APP LOCK"
    return f"{kind} {lock.app_lock.user}"


def _row(name: str, value: str) -> str:
    return f"--   {name.ljust(_ROW_WIDTH)} | {value}"


__all__ = [
    "RUN_ONLY",
    "build_status_line",
    "build_status_log_text",
    "build_status_timeline",
    "held_by",
]
