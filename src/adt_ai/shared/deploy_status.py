"""What a patch folder's deploy logs say about where the patch has been.

Split out of ``commit_discovery`` when that file crossed the 20 KB context
budget (ADT #268). The seam is the question each half answers:
``commit_discovery`` reads a patch folder's *contents*, which commits and
files it carries, while this module reads only its *logs*, and the two share
nothing but the folder path.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

# `<timestamp>_<script stem>_<STATUS>.log`, the shape `_write_deployment_log`
# writes. A hand-run's bare SPOOL file (`<SCHEMA>.log`) carries no outcome and is
# deliberately not counted as a deploy.
DEPLOY_LOG_RE = re.compile(r"^\d{8}-\d{6}_.+_(SUCCESS|ERROR)\.log$", re.IGNORECASE)

# The legacy marker's outcome word, matched whole. A bare `"SUCCESS" in text`
# pair read the marker as a bag of substrings, so a folder whose marker had
# collected the deploy transcript flipped to `ERROR` on an object called
# `ERROR_LOG` or a `0 ERRORS` summary line, and `target_status` then re-deployed
# a patch that had already landed (`#658`). Underscores and digits count as word
# characters here because the names that caused it are Oracle identifiers.
LEGACY_MARKER_RE = re.compile(r"(?<![A-Z0-9_])(SUCCESS|ERROR)(?![A-Z0-9_])")

DEPLOY_RECEIPT = "deployment.json"


def read_deploy_receipt(path: Path) -> dict[str, str]:
    """A complete-run receipt, with absent/corrupt/unknown formats failing closed."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return {}
    if (
        not isinstance(value, dict)
        or value.get("version") != 1
        or value.get("status") not in ("INCOMPLETE", "SUCCESS", "ERROR")
        or not isinstance(value.get("target"), str)
        or not isinstance(value.get("fingerprint"), str)
    ):
        return {}
    return {key: value[key] for key in ("target", "fingerprint", "status")}


def deploy_log_records(path: Path) -> list[tuple[int, str, str, str]]:
    """Every deploy log in the folder as ``(stamped, when, target, outcome)``, oldest first.

    Old ADT stamped the outcome into the log name it wrote
    (``<file> <stamp> [<STATUS>].log``, patch.py:579) and read it back from
    there; ADT.ai's extra one-word ``deploy_<TARGET>.log`` marker in the folder
    root was a second source of truth for the same fact, and one more stray file
    beside the install script (ADT #260).

    ``when`` is the log's own ``YYYYMMDD-HHMMSS`` filename stamp, which sorts
    lexicographically in time order. A pre-#260 marker carries no stamp, so its
    mtime is formatted into the same shape and it is flagged unstamped (``0``),
    which sorts it below every real log: a folder deployed both before and after
    #260 must not have its marker outrank a log whose time is actually known.
    """
    records: list[tuple[int, str, str, str]] = []
    stamped_targets: set[str] = set()
    for log_folder in sorted(path.glob("logs_*")):
        if not log_folder.is_dir():
            continue
        target = log_folder.name.removeprefix("logs_").upper()
        for log_path in sorted(log_folder.glob("*.log")):
            if not DEPLOY_LOG_RE.match(log_path.name):
                continue
            outcome = "SUCCESS" if log_path.stem.upper().endswith("_SUCCESS") else "ERROR"
            records.append((1, log_path.name.split("_", 1)[0], target, outcome))
            stamped_targets.add(target)
    for log_path in sorted(path.glob("deploy_*.log")):
        # Folders built before ADT #260 carry the marker file and no status in
        # the log names; keep reading them so an old patch still reports.
        target = log_path.stem.removeprefix("deploy_").upper()
        if target in stamped_targets:
            continue
        text = log_path.read_text(encoding="utf-8", errors="replace").upper()
        markers = set(LEGACY_MARKER_RE.findall(text))
        outcome = "SUCCESS" if markers == {"SUCCESS"} else "ERROR"
        when = datetime.fromtimestamp(log_path.stat().st_mtime).strftime("%Y%m%d-%H%M%S")
        records.append((0, when, target, outcome))
    return sorted(records)


def target_status(path: Path) -> dict[str, str]:
    """Whole-run outcome where available, otherwise the legacy newest log.

    Individual log history remains readable, but is never proof that the current
    artifact and required verification completed. The runner separately matches
    the successful receipt against its current payload fingerprint.
    """
    status: dict[str, str] = {}
    for _stamped, _when, target, outcome in deploy_log_records(path):
        status[target] = outcome
    for receipt_path in sorted(path.glob(f"logs_*/{DEPLOY_RECEIPT}")):
        receipt = read_deploy_receipt(receipt_path)
        if receipt:
            status[receipt["target"]] = receipt["status"]
        elif receipt_path.parent.name.startswith("logs_"):
            status[receipt_path.parent.name.removeprefix("logs_").upper()] = "INCOMPLETE"
    return status


def latest_deploy_status(path: Path) -> str | None:
    """The single newest deploy log, as ``<OUTCOME>/<TARGET>``.

    Jan, 2026-08-10: "status will be reflecting the latest deployed log, so if
    the latest file is log_DEV_<result> I want to see the DEV:<result> as a
    value", one value, the latest, not one per target. That reading is unchanged;
    only the spelling moved, on 2026-08-24 (ADT #510): *"I would like the status
    to be printed as: "<STATUS>/<ENV>" and not as "<ENV>:<STATUS>""*, so
    ``DEV:SUCCESS`` reads ``SUCCESS/DEV``. The outcome leads because it is what a
    reader scans the column for; the target answers "where" second.

    **Composed here rather than at the column that shows it.** Two user-facing
    surfaces read this one value, the ``RECENT PATCH FOLDERS:`` ``STATUS`` cell
    and the already-deployed refusal in ``patch/staleness.py``, so formatting it
    at either one would put two spellings of one fact on two screens. That is
    the `#240` ``schema_label()`` failure, and the display-layer rule it produced
    binds a value with one meaning, not a value with one call site.
    """
    records = deploy_log_records(path)
    if not records:
        return None
    _stamped, _when, target, outcome = records[-1]
    return f"{outcome}/{target}"


__all__ = ["DEPLOY_LOG_RE", "deploy_log_records", "latest_deploy_status", "target_status"]
