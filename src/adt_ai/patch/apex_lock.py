"""Holding the application shut while a `-app` deploy reads it and writes it.

The gap this closes (ADT #726). `#592` made the deploy read the target's
signature before importing over it, so a target somebody has changed since the
export refuses. It reads, then it acts, and a developer who saves in the App
Builder between those two moments has their change imported over with no trace:
the check passed, and it passed against a state that no longer existed by the
time the write happened. That is the whole of the race, and nothing in the
deploy closed it.

**Below APEX 26.2, build status is the lock.** On 26.1.0 an import DELETES the
Builder's application lock (bug 39557252), and a lock the deploy itself drops
cannot guard the deploy. `RUN_ONLY` needs no version floor: it goes through
`APEX_UTIL.SET_APP_BUILD_STATUS`, the call `patch_apex_build_status` emits.
**On 26.2 and newer the application lock is** (ADT #1056): the import keeps it
there, so `apex_app_lock` takes it as the deploying developer and this module
writes no `RUN_ONLY`, falling back to it only when that lock cannot be taken.

**`RUN_ONLY` closes the door, not the room** (measured on 26.1.0): a Page
Designer session already open still saves; what it refuses is Builder ENTRY. It
narrows the window; the signature gate remains the guard.

**Every import resets build status, on 26.2 too**, and SQLcl's APEXlang
importer ignores `apex_application_install.set_build_status`. So the deploy
reads the status and the signature, locks, lets the import reset it, and sets
the final status as the last step, recording each moment in its timeline.

**A task sandbox is never locked** (Jan, 2026-09-07: *"If task created its own
copy, we dont want locks there."*): a retargeted import lands on a throwaway id
nobody is editing, and locking it would strand every prototype.

**Nothing here raises.** A status that could not be read and a lock that could
not be set are outcomes carrying their reason, as in `apex_backup` and
`apex_scan`: a deploy must not die because a courtesy lock was refused. The one
refusal is somebody else's application lock, raised by `hold_application`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.apex_app_lock import (
    LOCK_COMMENT,
    AppLock,
    PageLock,
    read_page_locks,
    refusal_message,
    release_app_lock,
    take_app_lock,
)
from adt_ai.patch.apex_lock_report import (
    RUN_ONLY,
    build_status_line,
    build_status_log_text,
    build_status_timeline,
)
from adt_ai.patch.apex_signature import LastChange, read_last_change, read_target_signature
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared import text_files
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.row_values import row_value

#: The application was read, locked, and the deploy set its final status.
LOCK_HELD = "HELD"
#: Nothing was locked, and the reason says which of the ordinary cases it was:
#: the key is off, the import is retargeted, or the target holds no application.
LOCK_SKIPPED = "SKIPPED"
#: The lock was wanted and the database refused it. The deploy carries on --
#: the signature gate is the guard, this is the courtesy on top of it.
LOCK_FAILED = "FAILED"

#: What `APEX_UTIL.SET_APP_BUILD_STATUS` accepts, keyed by the display text
#: `APEX_APPLICATIONS.BUILD_STATUS` answers. The two vocabularies differ by
#: design and the view is the older surface, so the map lives here rather than
#: the read being swapped for `APEX_APPLICATION_ADMIN.GET_BUILD_STATUS`, which
#: would answer the API value and cost the version floor.
_API_VALUE = {
    "RUN AND DEVELOP": "RUN_AND_BUILD",
    "RUN AND BUILD"  : "RUN_AND_BUILD",
    "RUN ONLY"       : "RUN_ONLY",
}


@dataclass(frozen=True)
class BuildStatusLock:
    """One application's build status across one deploy, as the log reads it."""

    app_id    : int
    outcome   : str = LOCK_SKIPPED
    #: The display text the target carried before anything was set, e.g.
    #: `Run and Develop`. Empty when it was never read.
    before    : str = ""
    #: The display text read back after the import, which is APEX's own reset
    #: rather than anything ADT set. Empty until the release step reads it.
    after     : str = ""
    #: The display text the deploy leaves the application on. Empty until the
    #: release step has run.
    final     : str = ""
    #: `restore`, `run_only` or `off`, recorded so the log says which rule
    #: produced `final` rather than leaving a reader to infer it.
    mode      : str = settings.BUILD_STATUS_OFF
    #: Whether a lock actually went on: `RUN_ONLY`, or the application lock when
    #: ``app_lock.held``. Its own field rather than a reading of ``outcome``,
    #: because the release can fail AFTER a lock that was really taken, and a
    #: timeline saying `(not locked)` about a held application misleads.
    locked    : bool = False
    #: The target's export checksum as it stood BEFORE the status was written
    #: (ADT #745). Setting build status moves that checksum, so this is the only
    #: reading of the target the `#592` drift gate can honestly compare against
    #: what the export recorded. Empty when nothing was locked or the read failed,
    #: and the gate then reads the target itself as it did before ADT #726.
    signature : str = ""
    #: Who last moved the target, read beside ``signature`` because the status
    #: write stamps the application with the deploy's own user (ADT #925).
    last_change : LastChange = LastChange()
    #: The Builder's application lock on APEX 26.2+ (ADT #1056); empty below.
    app_lock  : AppLock = AppLock()
    reason    : str = ""
    log_path  : str = ""

    @property
    def held(self) -> bool:
        return self.outcome == LOCK_HELD


def lock_target(
    gateway   : Any,
    app_id    : int,
    *,
    workspace : str,
    mode      : str,
    account   : str = "",
    comment   : str = "",
    force     : bool = False,
) -> BuildStatusLock:
    """Read ``app_id``'s build status and signature, then lock it.

    On APEX 26.2+ the lock is the application lock, taken as ``account`` with
    ``comment`` (`apex_app_lock.take_app_lock`); below that, or when it cannot
    be taken, it is `RUN_ONLY`. Somebody else's application lock answers an
    entry whose ``app_lock.refused`` is set, and no lock at all.

    An empty ``workspace`` is an application `export_apex` never recorded, and
    the setter refuses without one, so the lock is skipped rather than attempted
    and failed -- the same judgement `templates._apex_environment_payload` makes
    about a workspace it does not know.

    A target holding no application is skipped too. There is nothing to lock and
    nothing anybody can have open in the Builder, which is what a fresh sandbox
    id looks like on its first deploy.
    """
    if mode == settings.BUILD_STATUS_OFF:
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_SKIPPED,
            mode    = mode,
            reason  = "deploy_build_status is off, so the deploy leaves build status alone",
        )
    if not workspace:
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_SKIPPED,
            mode    = mode,
            reason  = "no workspace is recorded for this application, and the build "
            "status setter refuses without one",
        )
    try:
        before = _live_status(gateway, app_id)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_FAILED,
            mode    = mode,
            reason  = f"the build status could not be read, so nothing was locked: {error}",
        )
    if not before:
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_SKIPPED,
            mode    = mode,
            reason  = "the target holds no application yet, so there is nothing to lock",
        )
    # ADT #745: the `#592` gate's reading of the target, taken here because the
    # write on the next line moves it. A failure is the same shrug the rest of
    # this module makes -- the deploy then reads the target itself, which is a
    # post-lock value and refuses, and a refused deploy is the safe half.
    try:
        signature = read_target_signature(gateway, app_id)
    except Exception:  # noqa: BLE001 - the gate re-reads and reports its own failure
        signature = ""
    last_change = read_last_change(gateway, app_id)
    app_lock = take_app_lock(
        gateway, app_id, workspace=workspace, account=account, comment=comment, force=force,
    )
    if app_lock.refused:
        return BuildStatusLock(app_id=app_id, before=before, mode=mode, app_lock=app_lock)
    try:
        if not app_lock.held:
            _set_status(gateway, app_id, workspace=workspace, status=RUN_ONLY)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_FAILED,
            before  = before,
            mode    = mode,
            app_lock = app_lock,
            reason  = "; ".join(filter(None, (
                app_lock.fallback, f"the application could not be set to RUN_ONLY: {error}",
            ))),
        )
    return BuildStatusLock(
        app_id    = app_id,
        outcome   = LOCK_HELD,
        before    = before,
        mode      = mode,
        locked    = True,
        signature = signature,
        last_change = last_change,
        app_lock  = app_lock,
        reason    = app_lock.fallback,
    )


def hold_application(
    gateway    : Any,
    app_id     : int,
    landing    : int,
    *,
    workspace  : str,
    config     : dict[str, Any],
    locks      : dict[int, BuildStatusLock] | None,
    account    : str = "",
    patch_name : str = "",
    force      : bool = False,
    page_locks : list[PageLock] | None = None,
) -> list[str]:
    """Everything `-app` does to the target before an import; the notes it owes.

    Reads the page locks the import is about to delete into ``page_locks`` on
    every release and in every mode, then locks ``app_id`` into ``locks`` --
    only where the import lands on its own id and the mode is not `off`, which
    keeps both rules exactly as ADT #726 drew them. Raises `PatchError` when
    another developer holds the application lock and ``force`` is not set; a
    fallback to `RUN_ONLY` on 26.2+ answers the note saying why.
    """
    if page_locks is not None:
        page_locks.extend(read_page_locks(gateway, landing))
    mode = settings.deploy_build_status(config)
    if locks is None or landing != app_id or mode == settings.BUILD_STATUS_OFF:
        return []
    lock = lock_target(
        gateway, app_id, workspace=workspace, mode=mode, account=account,
        comment=LOCK_COMMENT.format(patch=patch_name), force=force,
    )
    if lock.app_lock.refused:
        from adt_ai.patch.runner import PatchError

        raise PatchError(refusal_message(app_id, lock.app_lock))
    locks[app_id] = lock
    return [f"APP {app_id}: {lock.app_lock.fallback}"] if lock.app_lock.fallback else []


def release_target(
    gateway   : Any,
    lock      : BuildStatusLock,
    *,
    workspace : str,
    terminal  : str = "",
) -> BuildStatusLock:
    """Read what the import left, then set the status this deploy ends on.

    ``terminal`` is the status `patch_apex_build_status` already named for this
    target environment, and it is the final word when it names one: that key is
    a project's deliberate statement about how an application is left on an
    environment, and a lock that restored over it would silently unlock PROD.
    The generated install script has already applied it by the time this runs, so
    honouring it means reading the status back and setting nothing.

    The application lock (ADT #1056) is released last, after the final status
    is set and whether or not setting it worked.
    """
    if not lock.held:
        return lock
    result = _release_status(gateway, lock, workspace=workspace, terminal=terminal)
    app_lock, error = release_app_lock(gateway, lock.app_id, lock.app_lock, workspace=workspace)
    if not error:
        return _replace(result, app_lock=app_lock)
    return _replace(result, app_lock=app_lock, outcome=LOCK_FAILED,
                    reason="; ".join(filter(None, (result.reason, error))))


def _release_status(
    gateway   : Any,
    lock      : BuildStatusLock,
    *,
    workspace : str,
    terminal  : str,
) -> BuildStatusLock:
    """Read what the import left, then set the status this deploy ends on."""
    try:
        after = _live_status(gateway, lock.app_id)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return _replace(
            lock,
            outcome = LOCK_FAILED,
            reason  = f"the build status could not be read back after the import, so "
            f"the application is left where the import put it: {error}",
        )
    wanted = _final_status(lock, after=after, terminal=terminal)
    if not wanted or wanted == _api_value(after):
        return _replace(lock, after=after, final=after)
    try:
        _set_status(gateway, lock.app_id, workspace=workspace, status=wanted)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return _replace(
            lock,
            outcome = LOCK_FAILED,
            after   = after,
            final   = after,
            reason  = f"the final build status could not be set, so the application is "
            f"left on {after!r}: {error}",
        )
    try:
        final = _live_status(gateway, lock.app_id)
    except Exception:  # noqa: BLE001 - the set succeeded; the read-back is a nicety
        final = wanted
    return _replace(lock, after=after, final=final)


@contextmanager
def build_status_lock(
    gateways        : dict[str, Any],
    gateway_factory : Callable[[str], Any],
    *,
    root            : Path,
    config          : dict[str, Any],
    schemas         : dict[int, str],
    target_env      : str,
    log_folder      : Path,
    moment          : datetime | None = None,
) -> Iterator[dict[int, BuildStatusLock]]:
    """Hand out the deploy's lock ledger and release it whatever happens next.

    The `finally` is the point (ADT #726). A refused signature gate, a script
    that raised something the deploy loop does not catch, a failed receipt write:
    each of them would otherwise leave an application this run set to `RUN_ONLY`,
    and a stranded lock does not heal on the next deploy, because `restore` would
    faithfully put back the `Run Only` it found.

    The release runs after the scan and after any revert, which is what falling
    out of the `with` gives it for free: a revert is another import, and an import
    resets build status on its own, so a release that ran before it would set a
    final status the revert then threw away.

    ``gateways`` are the connections the deploy already opened, preferred over
    ``gateway_factory`` because `test_runner_deploy` pins that a deploy connects
    once per schema and then stops connecting.

    ``moment`` is the deploy's own reading of the clock (`#929`). The release
    runs last, so a timeline stamped where it is written sorted minutes away from
    the backup and the scan of the very run it reports on.
    """
    locks: dict[int, BuildStatusLock] = {}
    try:
        yield locks
    finally:
        # Written BACK into the ledger the caller was handed, rather than only
        # returned (ADT #720). `final` is set here and nowhere else, so a caller
        # holding the pre-release entries can report where an application ended
        # up only if this dict is the one it kept -- which is what lets the
        # deploy print `BUILD STATUS:` on the `VERIFYING APPLICATIONS:` row.
        locks.update(
            release_targets(
                locks,
                lambda schema: gateways.get(schema) or gateway_factory(schema),
                root       = root,
                config     = config,
                schemas    = schemas,
                target_env = target_env,
                log_folder = log_folder,
                moment     = moment,
            )
        )


def release_targets(
    locks           : dict[int, BuildStatusLock],
    gateway_factory : Callable[[str], Any],
    *,
    root            : Path,
    config          : dict[str, Any],
    schemas         : dict[int, str],
    target_env      : str,
    log_folder      : Path | None = None,
    moment          : datetime | None = None,
) -> dict[int, BuildStatusLock]:
    """Put every locked application on the status this deploy ends on."""
    terminal = settings.apex_terminal_status(config, target_env)
    stamp = moment or datetime.now()
    released: dict[int, BuildStatusLock] = {}
    for app_id, lock in locks.items():
        result = release_target(
            gateway_factory(schemas.get(app_id, "")),
            lock,
            workspace = _workspace(root, app_id),
            terminal  = terminal,
        )
        # A report for a lock that was never attempted would be a file per
        # deploy saying nothing happened. The import log's `BUILD STATUS` row
        # already carries that, with the reason on it.
        if log_folder is not None and result.outcome != LOCK_SKIPPED:
            result = _written(result, log_folder=log_folder, config=config, moment=stamp)
        released[app_id] = result
    return released


def _final_status(lock: BuildStatusLock, *, after: str, terminal: str) -> str:
    """The API value this deploy leaves the application on, or "" to leave it.

    `patch_apex_build_status` wins wherever it names one, so the two keys can
    never disagree about the same application. Otherwise `restore` puts back
    what was there and `run_only` keeps the lock on.
    """
    if terminal:
        return ""
    if lock.mode == settings.BUILD_STATUS_RUN_ONLY:
        return RUN_ONLY
    # `restore` is the only mode left: a held lock is never `off`, because
    # nothing is locked under it, and `settings.deploy_build_status` answers no
    # fourth value. So this is the restore arm rather than a default.
    return _api_value(lock.before)


def _api_value(display: str) -> str:
    """`Run and Develop` -> `RUN_AND_BUILD`, the value the setter accepts.

    An unrecognised display text answers "" rather than a guess: setting a
    status APEX did not name is a write nobody asked for, and leaving the
    application where the import put it is the safe half of that choice.
    """
    return _API_VALUE.get(" ".join(display.split()).upper(), "")


def _live_status(gateway: Any, app_id: int) -> str:
    """The display text `APEX_APPLICATIONS` carries for ``app_id`` right now."""
    rows = gateway.fetch_all(queries.APEX_BUILD_STATUS_QUERY, {"app_id": app_id})
    if not rows:
        return ""
    return str(row_value(rows[0], "BUILD_STATUS") or "").strip()


def _set_status(gateway: Any, app_id: int, *, workspace: str, status: str) -> None:
    """One `APEX_UTIL` call, on the connection the deploy already holds.

    `execute` rather than `sqlcl_request`: this is one PL/SQL block with no
    spool, no transcript to parse and nothing to install, and spawning SQLcl
    four times per application to set a flag would cost more than the deploy it
    is protecting.
    """
    gateway.execute(
        queries.APEX_SET_BUILD_STATUS_BLOCK.format(
            workspace    = escape_literal(workspace),
            app_id       = int(app_id),
            build_status = escape_literal(status),
        )
    )


def _workspace(root: Path, app_id: int) -> str:
    """The workspace `export_apex` recorded for this application, or "".

    The same offline read `templates._cached_apex_workspace` makes, and for the
    same reason: the workspace costs no round trip because the cache already
    holds it.

    An unreadable store answers "" rather than raising, because `release_targets`
    runs in the deploy's `finally`: an exception here would replace whatever the
    deploy was about to report or raise with a cache-read error, which is the one
    way this module could take the record of a deploy down with it.
    """
    try:
        with ApexStore.load(root) as store:
            application = store.application(app_id)
    except Exception:  # noqa: BLE001 - an unreadable cache is a lock we skip
        return ""
    if not isinstance(application, dict):
        return ""
    return str(application.get("workspace") or "")


def _written(
    lock       : BuildStatusLock,
    *,
    log_folder : Path,
    config     : dict[str, Any],
    moment     : datetime,
) -> BuildStatusLock:
    """Write the timeline beside the deploy's other reports, and record where.

    An unwritable folder never changes the outcome, the rule `apex_backup._written`
    and `apex_scan._written` already follow: where the application ended up is a
    fact about the application, and folding a failed write into it would report a
    held lock as a failed one.
    """
    try:
        log_folder.mkdir(parents=True, exist_ok=True)
        path = log_folder / settings.apex_build_status_log_name(
            config, moment=moment, app_id=lock.app_id
        )
        text_files.write_text(path, build_status_log_text(lock))
    except OSError as error:
        lost = f"the build status log could not be written: {error}"
        return _replace(lock, reason=f"{lock.reason}; {lost}" if lock.reason else lost)
    return _replace(lock, log_path=str(path))


def _replace(lock: BuildStatusLock, **changes: Any) -> BuildStatusLock:
    """`dataclasses.replace` under a name that says what it is used for here."""
    from dataclasses import replace

    return replace(lock, **changes)




__all__ = [
    "LOCK_FAILED",
    "LOCK_HELD",
    "LOCK_SKIPPED",
    "RUN_ONLY",
    "BuildStatusLock",
    "build_status_line",
    "build_status_lock",
    "build_status_log_text",
    "build_status_timeline",
    "hold_application",
    "lock_target",
    "release_target",
    "release_targets",
]
