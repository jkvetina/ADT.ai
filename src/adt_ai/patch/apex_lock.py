"""Holding the application shut while a `-app` deploy reads it and writes it.

The gap this closes (ADT #726). `#592` made the deploy read the target's
signature before importing over it, so a target somebody has changed since the
export refuses. It reads, then it acts, and a developer who saves in the App
Builder between those two moments has their change imported over with no trace:
the check passed, and it passed against a state that no longer existed by the
time the write happened. That is the whole of the race, and nothing in the
deploy closed it.

**Build status is the lock, because APEX offers no other.** The Builder's own
application lock has no public API -- `WWV_FLOW_LOCK.LOCK_APPLICATION` and its
siblings carry no grant to any user -- and an import DELETES the lock row anyway
(bug 39557252, reproduced on APEX 26.1.0 as fact 4 of the Brain's APEXlang lock
facts). A lock the deploy itself drops cannot guard the deploy. Setting the
application to `RUN_ONLY` is what is left, and it needs no version floor: it goes
through `APEX_UTIL.SET_APP_BUILD_STATUS`, the same call ADT already emits into a
generated install script for `patch_apex_build_status`.

**It closes the door, not the room, and that is measured rather than assumed.**
Fact 1: a Page Designer session that is ALREADY open saves successfully under
`RUN_ONLY`, and the save lands. What `RUN_ONLY` refuses is Builder ENTRY -- a
reload of the same page answers `Application not available for edit`. So the
lock stops a new editing session starting mid-deploy and does not evict one
already running. It narrows the window; the signature gate remains the guard.

**The import drops the lock on its own, so the deploy re-applies rather than
carries.** Fact 2: an APEXlang import resets build status to `Run and Develop`
every time, and `apex_application_install.set_build_status` -- which does pin the
classic `f<id>.sql` path -- is ignored by SQLcl's APEXlang importer. There is
nothing to pin with. The deploy therefore locks before the signature read, lets
the import reset it, and sets the final status as the last step, recording all
four moments so the log shows what happened rather than what was intended.

**A task sandbox is never locked** (Jan, 2026-09-07: *"We need it for the
shared/main apps (1000), not for the tasks prototypes (1000 + ###). If task
created its own copy, we dont want locks there."*). A retargeted import lands on
a throwaway id nobody is editing, so locking it protects nothing and leaves a
stranded `RUN_ONLY` behind on every prototype. `ApexImportItem.retargeted`
already names the distinction, so the rule reads off what the deploy knows.

**Nothing here raises.** A status that could not be read and a lock that could
not be set are outcomes carrying their reason, the rule `apex_backup` and
`apex_scan` already follow beside it: a deploy must not die because a courtesy
lock was refused, and a lock that never went on is exactly the state every
release before this one deployed in.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.apex_signature import read_target_signature
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

#: The status the lock puts on. Both edges name it, so it is one constant.
RUN_ONLY = "RUN_ONLY"

#: The column this block's rows line up on, shared with the signature and
#: `BACKUP` rows so a reader meets one table rather than three that nearly agree.
_ROW_WIDTH = 16

#: What the import log's `BUILD STATUS` row says when nothing was locked. Never
#: blank, for the reason `apex_backup._NO_BACKUP` is never blank: an empty value
#: reads as a row the log failed to write.
_NO_LOCK = "(not locked)"


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
    #: Whether `RUN_ONLY` actually went on. Its own field rather than a reading
    #: of ``outcome``, because the release can fail AFTER a lock that was really
    #: taken, and a timeline saying `LOCKED | (not locked)` about an application
    #: sitting at `Run Only` is the one thing a reader cannot recover from.
    locked    : bool = False
    #: The target's export checksum as it stood BEFORE the status was written
    #: (ADT #745). Setting build status moves that checksum, so this is the only
    #: reading of the target the `#592` drift gate can honestly compare against
    #: what the export recorded. Empty when nothing was locked, and then the gate
    #: reads the target itself exactly as it did before ADT #726.
    signature : str = ""
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
) -> BuildStatusLock:
    """Read ``app_id``'s build status and set it to `RUN_ONLY`, before the read.

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
    try:
        _set_status(gateway, app_id, workspace=workspace, status=RUN_ONLY)
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return BuildStatusLock(
            app_id  = app_id,
            outcome = LOCK_FAILED,
            before  = before,
            mode    = mode,
            reason  = f"the application could not be set to RUN_ONLY: {error}",
        )
    return BuildStatusLock(
        app_id    = app_id,
        outcome   = LOCK_HELD,
        before    = before,
        mode      = mode,
        locked    = True,
        signature = signature,
    )


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

    A lock that was never held still reads the status back, because a reader of
    the timeline wants to know where the application ended up either way.
    """
    if not lock.held:
        return lock
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
    """
    locks: dict[int, BuildStatusLock] = {}
    try:
        yield locks
    finally:
        release_targets(
            locks,
            lambda schema: gateways.get(schema) or gateway_factory(schema),
            root       = root,
            config     = config,
            schemas    = schemas,
            target_env = target_env,
            log_folder = log_folder,
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
    terminal = _terminal_status(config, target_env)
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


def build_status_line(lock: BuildStatusLock) -> str:
    """The import log's `BUILD STATUS` row: the lock standing over this import.

    Written for every application the deploy TRIED to lock, the ones it then
    skipped included, because a reader asking whether the application was held
    needs to be told it was not rather than left to notice a missing row. An
    application the deploy never considered -- a retargeted sandbox, or any
    application at all under `off` -- has no entry and gets no row, which is what
    keeps `off` byte for byte the deploy it was before ADT #726.
    """
    if not lock.locked:
        return _row("BUILD STATUS", f"{_NO_LOCK} {lock.reason}".strip())
    return _row("BUILD STATUS", f"{RUN_ONLY} (was {lock.before})")


def build_status_log_text(lock: BuildStatusLock) -> str:
    """The lock's own report: the four moments, in the order they happened.

    The timeline is the point. `BEFORE DEPLOY` and `LOCKED` are what the deploy
    did to hold the application shut, `AFTER IMPORT` is APEX resetting it on its
    own, and `FINAL` is where the deploy left it -- so a reader can tell a lock
    that never went on from one that did and was reset from one that was
    restored, without reading the source to find out which is which.
    """
    lines = [
        f"-- APEX application {lock.app_id} and its build status across this deploy.",
        "--",
        "-- The lock is set BEFORE the signature is read, so the window between the",
        "-- check and the import is covered. RUN_ONLY refuses Builder ENTRY, not a",
        "-- save from a session that is already open, and the APEXlang import resets",
        "-- the status on its own, which is why FINAL is set as the last step rather",
        "-- than carried through the import.",
        "",
        _row("APPLICATION", str(lock.app_id)),
        _row("MODE", lock.mode),
        _row("STATUS", lock.outcome),
        _row("BEFORE DEPLOY", lock.before or "(no application)"),
        _row("LOCKED", RUN_ONLY if lock.locked else _NO_LOCK),
        _row("AFTER IMPORT", lock.after or "(not read)"),
        _row("FINAL", lock.final or "(not set)"),
    ]
    if lock.reason:
        lines.append(f"{lock.outcome}: {lock.reason}")
    return "\n".join(lines) + "\n"


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
            workspace    = workspace.replace("'", "''"),
            app_id       = int(app_id),
            build_status = status.replace("'", "''"),
        )
    )


def _terminal_status(config: dict[str, Any], target_env: str) -> str:
    """`patch_apex_build_status` for this environment, read the way the template reads it."""
    statuses = config.get("patch_apex_build_status") or {}
    if not isinstance(statuses, dict):
        return ""
    return str(statuses.get(target_env or "") or "")


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


def _row(name: str, value: str) -> str:
    return f"--   {name.ljust(_ROW_WIDTH)} | {value}"


__all__ = [
    "LOCK_FAILED",
    "LOCK_HELD",
    "LOCK_SKIPPED",
    "RUN_ONLY",
    "BuildStatusLock",
    "build_status_line",
    "build_status_lock",
    "build_status_log_text",
    "lock_target",
    "release_target",
    "release_targets",
]
