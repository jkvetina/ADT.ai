"""The three signatures a `-app` deploy reads before it writes anything.

A whole-application import replaces every component of the target, so the one
question worth asking first is whether anybody moved that application since the
tree being deployed was exported from it. Three values answer it, and the log
prints all three so a refusal and an override are both auditable afterwards
(ADT #592, Jan 2026-08-29).

**The first two are APEX's own checksum and are comparable; the third is not,
and says so in its own value.** `apex export -exptype CHECKSUM-SH256` is
documented as "independent of IDs and can be compared across instances and
workspaces", which is the property the comparison rests on: a sandbox import
carries a different application id from the application the tree came from, so
anything id-bearing would report every retarget as a difference.

  * ``on_target``  the live checksum of the application about to be written,
    read now, through the deploy's own gateway.
  * ``based_on``   the checksum `export_apex` recorded in `config/internal/apex.db`
    when it wrote the tree. That is the state the change was made against.
  * ``deploying``  a content hash of the tree on disk.

**The third one is a tree hash rather than the recorded export checksum, and
that is the pin ADT #592 row 55 asked for.** The recorded checksum IS
``based_on``, so spending it twice would leave the log with two rows saying one
thing and nothing at all saying what is on disk. The whole reason a deploy
happens is that the tree was edited after it was exported, and an APEX checksum
cannot see a `.apx` file an editor touched five minutes ago: only a hash of the
bytes can. `shared/git_files.file_payload_hash` is that hash, the same canonical
form (CRLF collapsed, payload trimmed once) `patch`'s baselines already use, so a
tree exported on Windows and deployed from macOS reads as one tree.

It carries a `TREE:` prefix for the same reason APEX carries `SH256:`: the two
answer different questions, and a reader comparing them line by line in a log
must be able to see at a glance that they were never meant to match.

**A target with no signature at all has not moved.** `apex import -id` onto a
fresh sandbox id is the ordinary case, and there is nothing there to clobber, so
the gate passes rather than refusing on an absence. A missing ``based_on`` is the
opposite: the run cannot say what the change was made against, so it refuses and
names the export that would fix it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.shared.apex_store import ApexStore
from adt_ai.shared.git_files import file_payload_hash
from adt_ai.shared.row_values import row_value

# What the tree hash announces itself as, so no reader mistakes it for a value
# APEX computed. APEX's own values open `SH256:`.
TREE_PREFIX = "TREE:"

# How APEX says an application id holds no application. Captured rather than
# remembered (the rule `#473` was filed on): asked for app 999 on SANDBOX,
# 2026-08-30, `APEX_EXPORT.GET_APPLICATION` does NOT come back empty, it raises
#
#   ORA-20987: APEX - Application 999 not found logged in as database user
#   SANDBOX. - Contact your application administrator.
#
# out of `WWV_FLOW_ERROR`. Reading an absent target as an empty answer was this
# module's first draft and would have turned every fresh sandbox id into a
# database error on the screen instead of the ordinary first import.
#
# BOTH halves are matched. `ORA-20987` is APEX's one generic application error
# and covers authorization failures too, which are emphatically not "no
# application here", so the wording is what separates them.
_NO_APPLICATION_CODE   = "ORA-20987"
_NO_APPLICATION_MARKER = "not found"

OK       = "OK"
DRIFTED  = "DRIFTED"
UNKNOWN  = "UNKNOWN"

EXPORT_COMMAND = "adtai export_apex -apexlang -app"


@dataclass(frozen=True)
class ApexSignatures:
    """What the three log rows carry, and the verdict they add up to."""

    app_id    : int
    target_id : int
    on_target : str
    based_on  : str
    deploying : str

    @property
    def verdict(self) -> str:
        if not self.based_on:
            return UNKNOWN
        if not self.on_target:
            # The application does not exist yet, which is what a fresh sandbox
            # id looks like. Nothing is there to be overwritten.
            return OK
        return OK if self.on_target == self.based_on else DRIFTED

    @property
    def refused(self) -> bool:
        return self.verdict != OK


def read_target_signature(gateway: Any, app_id: int) -> str:
    """The live APEX checksum of ``app_id``, or empty when it holds no application.

    Absent is not drifted, so an id nothing is installed on comes back as an
    empty string. APEX reports that by raising rather than by answering no rows
    (see `_NO_APPLICATION_CODE`), so the empty answer is made here; every other
    database error is somebody else's to see and re-raises untouched.
    """
    try:
        rows = gateway.fetch_all(queries.APEX_CHECKSUM_QUERY, {"app_id": app_id})
    except Exception as error:  # noqa: BLE001 - re-raised unless it is the one case
        if not _is_missing_application(error):
            raise
        return ""
    for row in rows:
        value = str(row_value(row, "CHECKSUM") or "").strip()
        if value:
            return value
    return ""


def _is_missing_application(error: BaseException) -> bool:
    text = str(error)
    return _NO_APPLICATION_CODE in text and _NO_APPLICATION_MARKER in text


def recorded_signature(root: Path, app_id: int) -> str:
    """The checksum `export_apex` stored for ``app_id`` when it wrote the tree."""
    with ApexStore.load(root) as store:
        entry = store.application(app_id)
    if not entry:
        return ""
    return str(entry.get("checksum") or "").strip()


def tree_signature(tree_root: Path) -> str:
    """A content hash of every file under ``tree_root``, path included.

    The path is hashed beside the payload so a file MOVED inside the tree
    changes the answer: a renamed page is a different application, and a hash
    over payloads alone would call the two trees identical.

    An empty or missing tree hashes to nothing rather than to the hash of an
    empty string, so a caller can tell "no tree" from "a tree of empty files".
    """
    if not tree_root.is_dir():
        return ""
    lines: list[str] = []
    for path in sorted(tree_root.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(tree_root).as_posix()
        lines.append(f"{relative} {file_payload_hash(path.read_bytes())}")
    if not lines:
        return ""
    return f"{TREE_PREFIX}{file_payload_hash(chr(10).join(lines))}"


def collect_signatures(
    gateway   : Any,
    root      : Path,
    app_id    : int,
    target_id : int,
    tree_root : Path,
) -> ApexSignatures:
    """Read all three before anything is written, which is the whole point.

    The live read is of the application being WRITTEN (``target_id``) and the
    recorded one is of the application the tree came FROM (``app_id``). On a
    deploy in place the two ids are the same and the comparison is the freshness
    gate; on a retarget they differ and the live read asks about the sandbox.
    """
    return ApexSignatures(
        app_id    = app_id,
        target_id = target_id,
        on_target = read_target_signature(gateway, target_id),
        based_on  = recorded_signature(root, app_id),
        deploying = tree_signature(tree_root),
    )


def signature_lines(signatures: ApexSignatures, *, forced: bool = False) -> list[str]:
    """The log's APEX section: a header, the three rows, and any override note.

    Comment lines, because this block is prepended to a SQLcl transcript and a
    transcript is read beside install-script output that opens the same way. The
    leading double hyphen is SQL's own comment marker rather than punctuation, so
    the m-dash ban does not reach it.
    """
    heading = f"APEX APPLICATION {signatures.app_id}"
    if signatures.target_id != signatures.app_id:
        heading += f" IMPORTED AS {signatures.target_id}"
    lines = [
        f"-- {heading}",
        f"--   LATEST ON TARGET | {signatures.on_target or '(no application)'}",
        f"--   CHANGE BASED ON  | {signatures.based_on or '(never exported)'}",
        f"--   DEPLOYING        | {signatures.deploying or '(empty tree)'}",
    ]
    if forced:
        # Recorded whenever the flag was SET, not only when it changed the
        # outcome. `-force` also overrides the full-export refusal, which is not
        # a signature question at all, so a log that only spoke up when the
        # signature verdict was overridden would leave an overridden deploy
        # looking like an ordinary one. What the flag did is the auditable fact.
        lines.append(
            f"--   OVERRIDDEN       | -force deployed over a {signatures.verdict} "
            "signature check"
            if signatures.refused
            else "--   OVERRIDDEN       | -force was set; the signature check passed "
            "on its own"
        )
    return lines


def drift_message(signatures: ApexSignatures) -> str:
    """The refusal, in the shape `patch`'s other build gates already print.

    A lead line, the rows that show the disagreement, and a `Run:` line naming
    what clears it, the way `stale_full_app_message` and
    `GraphFreshness.failure_message` read.
    """
    if signatures.verdict == UNKNOWN:
        lines = [
            f"APP {signatures.app_id} has no recorded signature, so this deploy "
            "cannot tell what the change was based on."
        ]
        lines.append(f"Run: {EXPORT_COMMAND} {signatures.app_id}, then commit the export")
        return "\n".join(lines)
    return "\n".join(
        [
            f"APP {signatures.target_id} moved since the tree was exported, so an "
            "import would overwrite work this patch never saw.",
            f"  LATEST ON TARGET: {signatures.on_target}",
            f"  CHANGE BASED ON:  {signatures.based_on}",
            f"Run: {EXPORT_COMMAND} {signatures.app_id}, reconcile the tree, then "
            "deploy again (or -force to overwrite)",
        ]
    )


__all__ = [name for name in globals() if not name.startswith("_")]
