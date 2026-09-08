"""What `patch -deploy` writes beside an APEX application, and the keys behind it.

Split out of ``settings.py`` when ADT #726 pushed that module past the 24 KB
context guard, the same seam ADT #735 drew when it handed the stage names to
``stages.py``. What moved is one cohesive group rather than an arbitrary tail:
the four artifacts an APEX deploy leaves in ``logs_<TARGET_ENV>/``, the one
substitution they share, and the three keys that decide whether each of them
happens at all.

``settings`` re-exports every name here, so no caller and no test learns a new
one: `settings.apex_scan_log_name`, `settings.verify_deploy_scan` and the rest
keep working exactly as they read before the split.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

#: The scan log's name, deliberately NOT a config key and deliberately not
#: `.log`. `shared/deploy_status.DEPLOY_LOG_RE` reads every
#: `<stamp>_<stem>_<SUCCESS|ERROR>.log` for the latest script outcome. A scan
#: report is separate from that display; whole-run completion also requires
#: the verification result recorded in the target's deployment receipt.
APEX_SCAN_LOG_FILE = "{$TIMESTAMP}_apex_scan_{$APP}.txt"

#: The revert's own report, `.txt` for exactly the reason above: a `.log` here
#: would be read by `DEPLOY_LOG_RE` as a script outcome, and a revert is not a
#: script this patch installed.
APEX_REVERT_LOG_FILE = "{$TIMESTAMP}_apex_revert_{$APP}.txt"

#: The folder the pre-import export lands in, beside the deploy's own logs
#: (ADT #727). A folder rather than a file because an APEXlang export is a
#: tree, and it is the tree `apex import -input` reads back on a revert.
APEX_BACKUP_FOLDER = "{$TIMESTAMP}_apex_backup_{$APP}"

#: The build-status timeline's own report (ADT #726), `.txt` for the same reason
#: the two above are: a `.log` here would be read by `DEPLOY_LOG_RE` as a script
#: outcome, and a lock is not a script this patch installed.
APEX_BUILD_STATUS_LOG_FILE = "{$TIMESTAMP}_apex_build_status_{$APP}.txt"

#: `deploy_build_status`, the three values it takes (ADT #726). `restore` locks
#: the application for the deploy and puts its status back afterwards,
#: `run_only` locks it and leaves it locked, `off` leaves build status alone.
BUILD_STATUS_RESTORE = "restore"
BUILD_STATUS_RUN_ONLY = "run_only"
BUILD_STATUS_OFF = "off"

#: Every accepted value, so an unknown one falls back rather than deploying
#: under a mode nobody wrote.
BUILD_STATUS_MODES = (BUILD_STATUS_RESTORE, BUILD_STATUS_RUN_ONLY, BUILD_STATUS_OFF)


def apex_scan_log_name(config: dict[str, Any], *, moment: datetime, app_id: int) -> str:
    """One application's post-deploy scan log, beside that deploy's own logs.

    Shares `today_deploy` with `deploy_log_name` so the scan and the deploy it
    verifies sort together in the folder; the rest of the name is fixed, see
    `APEX_SCAN_LOG_FILE` for why it is not configurable and not `.log`.
    """
    return apex_deploy_artifact(APEX_SCAN_LOG_FILE, config, moment=moment, app_id=app_id)


def apex_revert_log_name(config: dict[str, Any], *, moment: datetime, app_id: int) -> str:
    """One application's revert report, beside the scan that asked for it."""
    return apex_deploy_artifact(APEX_REVERT_LOG_FILE, config, moment=moment, app_id=app_id)


def apex_build_status_log_name(config: dict[str, Any], *, moment: datetime, app_id: int) -> str:
    """One application's build-status timeline, beside the deploy that held it."""
    return apex_deploy_artifact(
        APEX_BUILD_STATUS_LOG_FILE, config, moment=moment, app_id=app_id
    )


def apex_backup_folder_name(config: dict[str, Any], *, moment: datetime, app_id: int) -> str:
    """The folder one pre-import export lands in (ADT #727).

    Same `today_deploy` stamp as the deploy log and the scan report, so the
    backup, the import that overwrote it and the scan that judged the import all
    sort together in `logs_<TARGET_ENV>/`.
    """
    return apex_deploy_artifact(APEX_BACKUP_FOLDER, config, moment=moment, app_id=app_id)


def apex_deploy_artifact(
    template: str,
    config  : dict[str, Any],
    *,
    moment  : datetime,
    app_id  : int,
) -> str:
    """The `{$TIMESTAMP}`/`{$APP}` substitution the APEX deploy artifacts share.

    Four names now render from one pair of tokens, so the stamp they sort on is
    read from `today_deploy` in one place rather than in four copies that can
    drift on the next format added.
    """
    from adt_ai.patch.settings import text_value

    stamp = moment.strftime(text_value(config, "today_deploy"))
    name = template
    for token, value in (("TIMESTAMP", stamp), ("APP", str(app_id))):
        name = name.replace(f"{{${token}}}", value).replace(f"#{token}#", value)
    return name


def verify_deploy_scan(config: dict[str, Any]) -> bool:
    """`deploy_verify_scan`: ask the application whether its own SQL still parses.

    On by default. After a deploy lands an APEX application, as a per-app install
    script or as an APEXlang import, ADT runs the APEX dependency scan against it
    and reads back every component property the scan could not compile. Findings
    are written to a log beside the deploy's own and mark the deploy ERROR,
    because an application that imported cleanly and cannot run a region query
    has not been deployed, it has been installed.

    False skips the scan entirely: no scan, no log, no effect on the status. For
    a target where the extra minute per application is not wanted, or an APEX
    older than 24.2, where the dictionary cannot answer.
    """
    from adt_ai.patch.settings import flag_value

    return flag_value(config, "deploy_verify_scan")


def revert_on_scan_failure(config: dict[str, Any]) -> bool:
    """`deploy_revert_on_scan_failure`: put the target back when the scan fails.

    On by default, and it only ever acts on a `patch -deploy -app` run. Before
    the APEXlang import writes, the live target is exported into
    `logs_<TARGET_ENV>/<timestamp>_apex_backup_<app>/` and the import log names
    it on a `BACKUP` row; when `deploy_verify_scan` then reports a failing
    outcome for that application, the backup is imported back and a `REVERTED`
    row records the checksum the target carries afterwards.

    False keeps the backup step too: no export, no folder, and a failed scan
    leaves the imported tree in place, which is what every release before ADT
    #727 did. It also does nothing at all when `deploy_verify_scan` is off,
    because there is then no verdict for it to act on.
    """
    from adt_ai.patch.settings import flag_value

    return flag_value(config, "deploy_revert_on_scan_failure")


def deploy_build_status(config: dict[str, Any]) -> str:
    """`deploy_build_status`: hold the application shut while the deploy runs.

    `restore` by default, and it only ever acts on a `patch -deploy -app` run
    landing on an application's OWN id. Before the target's signature is read the
    application is set to `RUN_ONLY`, which refuses Builder ENTRY for the length
    of the window between that read and the import; afterwards the status the
    application carried is put back. `run_only` leaves it locked instead, for a
    target nobody develops on, and `off` leaves build status untouched, which is
    what every release before ADT #726 did.

    A retargeted import is never locked whatever this says: a task sandbox is a
    throwaway id nobody is editing (Jan, 2026-09-07), and `apex_deploy` is where
    that rule is applied because `retargeted` is a property of the item.

    **A bare `off` in YAML is the boolean False, not the string.** YAML 1.1
    spells booleans `off`/`on`/`no`/`yes` as well as `false`/`true`, so
    `deploy_build_status: off` arrives here as `False` and a plain string read
    would answer `restore`, locking every application for a project that had
    written the word for "do not". Measured on SANDBOX, 2026-09-07: the run wrote
    `MODE | restore` under exactly that config line. `False` is therefore `off`
    and `True` is `restore`, which is what a reader writing either word means,
    and no project has to learn to quote it.

    An unknown value falls back to `restore` rather than raising, the rule
    `archive_format` already follows: a typo in config must not stop a deploy
    that built correctly, and falling back to the shipped default is the reading
    that surprises nobody.
    """
    raw = config.get("deploy_build_status")
    if isinstance(raw, bool):
        return BUILD_STATUS_OFF if not raw else BUILD_STATUS_RESTORE
    value = str(raw or "").strip().lower()
    return value if value in BUILD_STATUS_MODES else BUILD_STATUS_RESTORE


__all__ = [
    "APEX_BACKUP_FOLDER",
    "APEX_BUILD_STATUS_LOG_FILE",
    "APEX_REVERT_LOG_FILE",
    "APEX_SCAN_LOG_FILE",
    "BUILD_STATUS_MODES",
    "BUILD_STATUS_OFF",
    "BUILD_STATUS_RESTORE",
    "BUILD_STATUS_RUN_ONLY",
    "apex_backup_folder_name",
    "apex_build_status_log_name",
    "apex_deploy_artifact",
    "apex_revert_log_name",
    "apex_scan_log_name",
    "deploy_build_status",
    "revert_on_scan_failure",
    "verify_deploy_scan",
]
