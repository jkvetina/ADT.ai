"""The App Builder's own application lock, held across a `-app` deploy (ADT #1056).

`apex_lock` holds a deploy shut with build status because, measured on APEX
26.1.0, an import deletes the application lock (bug 39557252). APEX 26.2 fixed
that. Measured on 26.2.0 (2026-10-07): an APEXlang import and a classic
`f<id>.sql` import both KEEP the application lock, and
`APEX_APPLICATION_ADMIN.LOCK_APPLICATION` / `UNLOCK_APPLICATION` are callable
by the parsing schema with no extra grant once a workspace is set. So on 26.2
and newer the deploy locks the application as the developer running it. Below
26.2 it locks the application's pages instead where a DBA has granted the
undocumented page lock package (ADT #1059, `apex_page_lock`); without that grant
nothing changes.

**The lock is the developer's own APEX account**, resolved the way every `-my`
resolves it (`IDENTITY.yaml` `apex_account`, then git `user.name`); there is no
config key of its own. A name that is no workspace developer, or any other
refusal, falls back to `RUN_ONLY` for that application and says why, never
raising: the signature gate is the guard, this is the courtesy in front of it.

**Somebody else's lock refuses the deploy** (Jan, 2026-10-07): the import would
land over work that developer has deliberately held, and the refusal names
them. `-force` deploys anyway, leaves their lock where it is, and falls back to
build status. A lock the deploying developer ALREADY held is re-taken with the
deploy's comment and handed back with the original comment rather than removed.

**Page locks are a separate fact on every release.** An APEXlang import on 26.2
deletes every page lock of the application it lands on, so the deploy reads them
beforehand and the console warns. The read never raises; a view the schema
cannot read is a warning nobody gets, not a deploy that stops.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch.apex_page_lock import lock_pages, page_lock_owner, unlock_pages
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.row_values import row_value

#: The first release whose import keeps the application lock.
NATIVE_FROM = (26, 2)

#: What the deploy writes as the lock comment, naming the patch folder.
LOCK_COMMENT = "ADT deploy {patch}"

#: How the release left a lock the deploy held, for the timeline.
UNLOCKED = "unlocked"
RESTORED = "comment restored"

#: APEX's own words for a lock another developer holds (fact 3).
_ANOTHER_USER = "already locked by another user"

#: Where the developer's name comes from, for a reason that names the fix.
_IDENTITY = "config/IDENTITY.yaml apex_account, else git user.name"


@dataclass(frozen=True)
class AppLock:
    """The application lock across one deploy. Empty is "never in play"."""

    #: Who the deploy holds the lock as; "" when the native lock is not the lock.
    user          : str = ""
    comment       : str = ""
    #: The lock as it stood before the deploy, whoever held it.
    prior_by      : str = ""
    prior_comment : str = ""
    prior_on      : str = ""
    #: Another developer holds it and `-force` was not set: the deploy refuses.
    refused       : bool = False
    #: Why build status is the lock on a release that has the native one.
    fallback      : str = ""
    #: `UNLOCKED` or `RESTORED` once the release ran; "" until then.
    released      : str = ""
    #: The pages a deploy below 26.2 locked instead (ADT #1059); empty when the
    #: application lock is the lock, or nothing is.
    pages         : tuple[int, ...] = ()

    @property
    def held(self) -> bool:
        return bool(self.user)

    @property
    def was_mine(self) -> bool:
        return self.held and _same(self.prior_by, self.user)


@dataclass(frozen=True)
class PageLock:
    """One locked page an import is about to unlock."""

    app_id    : int
    page_id   : Any
    page_name : str
    locked_by : str
    locked_on : str


def native_supported(gateway: Any) -> bool:
    """Whether the target's APEX keeps the application lock across an import."""
    return release_at_least(gateway, NATIVE_FROM)


def release_at_least(gateway: Any, floor: tuple[int, int]) -> bool:
    """Whether the target's APEX is ``floor`` or later; an unknown release is not."""
    try:
        rows = gateway.fetch_all(queries.APEX_RELEASE_QUERY)
    except Exception:  # noqa: BLE001 - an unknown release takes the older path
        return False
    return any(
        _release(str(row_value(row, "VERSION_NO") or "")) >= floor for row in rows or []
    )


def take_app_lock(
    gateway   : Any,
    app_id    : int,
    *,
    workspace : str,
    account   : str,
    comment   : str,
    force     : bool = False,
) -> AppLock:
    """Lock ``app_id`` as ``account``, or say why build status has to do it.

    An `AppLock()` with no ``fallback`` is a release below 26.2 whose target
    grants no page lock package, the ordinary case there, so nothing reports it.
    """
    if not native_supported(gateway):
        return _take_page_locks(
            gateway, app_id, workspace=workspace, account=account, comment=comment,
        )
    if not account:
        return AppLock(fallback=f"no developer is named ({_IDENTITY}), so build status "
                       "is the lock")
    try:
        prior = _read(gateway, app_id)
    except Exception as error:  # noqa: BLE001 - reported as a fallback, never raised
        return AppLock(fallback=f"the application lock could not be read, so build status "
                       f"is the lock: {_first_line(error)}")
    if prior.prior_by and not _same(prior.prior_by, account):
        return _another(prior, force)
    try:
        _lock(gateway, app_id, workspace=workspace, user=account, comment=comment)
    except Exception as error:  # noqa: BLE001 - reported as a fallback, never raised
        if _ANOTHER_USER in str(error):
            try:
                return _another(_read(gateway, app_id), force)
            except Exception:  # noqa: BLE001 - the error already names the case
                return _another(prior, force)
        return replace(prior, fallback=f"the application lock could not be taken as "
                       f"{account}, so build status is the lock: {_first_line(error)}")
    return replace(prior, user=account, comment=comment)


def release_app_lock(
    gateway   : Any,
    app_id    : int,
    lock      : AppLock,
    *,
    workspace : str,
) -> tuple[AppLock, str]:
    """Unlock, or hand a developer's own lock back with its own comment.

    Answers the lock and an error, "" when it worked: the caller folds the
    error into its outcome rather than this raising out of the deploy's
    `finally`.
    """
    if not lock.held:
        return lock, ""
    if lock.pages:
        error = unlock_pages(gateway, app_id, workspace=workspace, user=lock.user,
                             comment=lock.comment)
        if error:
            return lock, f"the page locks could not be released as {lock.user}: {error}"
        return replace(lock, released=UNLOCKED), ""
    try:
        if lock.was_mine:
            _lock(gateway, app_id, workspace=workspace, user=lock.user,
                  comment=lock.prior_comment)
            return replace(lock, released=RESTORED), ""
        gateway.execute(queries.APEX_UNLOCK_APPLICATION_BLOCK.format(
            workspace = escape_literal(workspace),
            app_id    = int(app_id),
            user      = escape_literal(lock.user),
        ))
    except Exception as error:  # noqa: BLE001 - reported as an outcome, never raised
        return lock, (f"the application lock could not be released as {lock.user}: "
                      f"{_first_line(error)}")
    return replace(lock, released=UNLOCKED), ""


def read_page_locks(gateway: Any, app_id: int) -> tuple[PageLock, ...]:
    """The pages locked on ``app_id``; empty when none are, or the read failed."""
    try:
        rows = gateway.fetch_all(queries.APEX_LOCKED_PAGES_QUERY, {"app_id": app_id}) or []
    except Exception:  # noqa: BLE001 - a warning nobody gets, never a stopped deploy
        return ()
    return tuple(
        PageLock(
            app_id    = int(app_id),
            page_id   = row_value(row, "PAGE_ID"),
            page_name = _text(row, "PAGE_NAME"),
            locked_by = _text(row, "LOCKED_BY"),
            locked_on = _text(row, "LOCKED_ON"),
        )
        for row in rows
    )


def refusal_message(app_id: int, lock: AppLock) -> str:
    """The refusal, in the drift refusal's shape: headline, rows, ways out.

    A holder whose name could not be re-read after APEX refused the lock is
    still a refusal, said without the name rather than with a blank.
    """
    who = lock.prior_by or "ANOTHER DEVELOPER"
    return "\n".join([
        f"  APP {app_id} IS LOCKED BY {who}",
        "",
        "  Deploying now would import over work another developer is holding.",
        "",
        f"  LOCKED BY   | {lock.prior_by or '(not recorded)'}",
        f"  LOCKED ON   | {lock.prior_on or '(not recorded)'}",
        f"  COMMENT     | {lock.prior_comment or '(none)'}",
        "",
        f"  1) ask {lock.prior_by or 'its holder'} to unlock it in the App Builder",
        "  2) deploy again",
        "  3) or -force to deploy anyway, leaving their lock in place",
    ])


def timeline_rows(lock: AppLock) -> list[tuple[str, str]]:
    """The application-lock rows of the build-status timeline, if it was in play."""
    if lock.pages:
        count = len(lock.pages)
        return [
            ("PAGE LOCKS", f"{count} page{'s' if count != 1 else ''}"),
            ("PAGE LOCK RELEASE", lock.released or "(not released)"),
        ]
    if not (lock.held or lock.prior_by):
        return []
    before = f"{lock.prior_by} since {lock.prior_on or '?'}" if lock.prior_by else ""
    if before and lock.prior_comment:
        before += f" ({lock.prior_comment})"
    rows = [("APP LOCK BEFORE", before or "(not locked)")]
    if lock.held:
        rows.append(("APP LOCK RELEASE", lock.released or "(not released)"))
    return rows


def _take_page_locks(
    gateway   : Any,
    app_id    : int,
    *,
    workspace : str,
    account   : str,
    comment   : str,
) -> AppLock:
    """Below 26.2: the pages as ``account``, or why build status has to do it.

    The holder is upper-cased because APEX matches page lock holders
    case-sensitively and the App Builder records its users in capitals.
    """
    owner = page_lock_owner(gateway)
    if not owner:
        return AppLock()
    if not account:
        return AppLock(fallback=f"no developer is named ({_IDENTITY}), so build status "
                       "is the lock")
    user = account.strip().upper()
    pages, error = lock_pages(gateway, owner, app_id, workspace=workspace, user=user,
                              comment=comment)
    if error:
        return AppLock(fallback=f"the pages could not be locked as {user}, so build "
                       f"status is the lock: {error}")
    if not pages:
        return AppLock(fallback="every page is already locked, so build status is the lock")
    return AppLock(user=user, comment=comment, pages=pages)


def _another(prior: AppLock, force: bool) -> AppLock:
    """Somebody else holds it: refuse, or under `-force` leave it and fall back."""
    if not force:
        return replace(prior, refused=True)
    return replace(prior, fallback=f"{prior.prior_by} holds the application lock since "
                   f"{prior.prior_on or '?'}; -force deploys anyway, leaves it in place, "
                   "and build status is the lock")


def _read(gateway: Any, app_id: int) -> AppLock:
    rows = gateway.fetch_all(queries.APEX_APP_LOCK_QUERY, {"app_id": app_id}) or []
    if not rows:
        return AppLock()
    return AppLock(
        prior_by      = _text(rows[0], "LOCKED_BY"),
        prior_comment = _text(rows[0], "LOCK_COMMENT"),
        prior_on      = _text(rows[0], "LOCKED_ON"),
    )


def _lock(gateway: Any, app_id: int, *, workspace: str, user: str, comment: str) -> None:
    gateway.execute(queries.APEX_LOCK_APPLICATION_BLOCK.format(
        workspace = escape_literal(workspace),
        app_id    = int(app_id),
        user      = escape_literal(user),
        comment   = escape_literal(comment),
    ))


def _release(text: str) -> tuple[int, ...]:
    """`26.2.0` -> `(26, 2)`; anything unparseable -> `()`, below every floor."""
    parts = text.strip().split(".")[:2]
    if len(parts) < 2 or not all(part.isdigit() for part in parts):
        return ()
    return tuple(int(part) for part in parts)


def _first_line(error: object) -> str:
    """The ORA line itself: the driver appends the ORA-06512 stack under it, and
    a NOTES row is one line (measured live on DEV262)."""
    return (str(error).strip().splitlines() or [""])[0]


def _same(left: str, right: str) -> bool:
    """APEX matches the lock user case-insensitively (fact 2), so this does too."""
    return left.strip().upper() == right.strip().upper()


def _text(row: Any, column: str) -> str:
    return str(row_value(row, column) or "").strip()


__all__ = [
    "LOCK_COMMENT",
    "NATIVE_FROM",
    "RESTORED",
    "UNLOCKED",
    "AppLock",
    "PageLock",
    "native_supported",
    "read_page_locks",
    "refusal_message",
    "release_app_lock",
    "take_app_lock",
    "timeline_rows",
]
