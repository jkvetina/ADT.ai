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

**A fourth value is recorded and never compared: the MERGE BASE** (ADT #725).
The three above can say that the target moved and nothing more, so the only
recovery a refusal could name was "export again and reconcile by hand". That is
compare-and-swap; a three-way merge needs base, ours and theirs, and ADT recorded
the base's IDENTITY (the checksum) without recording the base. `export_apex` now
writes the commit its tree was exported at, and `-mirror db/<ENV>` puts that
commit on a ref the whole team shares, which is what turns the refusal's last
line into `git rebase`. It is not a signature: it moves no verdict, and a tree
with no recorded commit refuses and passes exactly as it did before.
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
class RecordedExport:
    """What `export_apex` wrote about an application when it last exported it.

    One read of the store answering every question the deploy has about the
    recorded side, rather than one open per column.
    """

    checksum    : str = ""
    base_commit : str = ""
    mirror_ref  : str = ""


@dataclass(frozen=True)
class ApexSignatures:
    """What the three log rows carry, and the verdict they add up to."""

    app_id    : int
    target_id : int
    on_target : str
    based_on  : str
    deploying : str
    # Recorded rather than compared: the commit the tree was exported at, and
    # the ref `-mirror` shares it on. Neither reaches `verdict`; they are what a
    # refusal names so the way out is a rebase (ADT #725).
    base_commit : str = ""
    mirror_ref  : str = ""

    @property
    def base(self) -> str:
        """What the change was made against, as a refusal names it.

        The commit when the export recorded one, and the checksum always: the
        checksum is what the comparison actually used, so a reader can still see
        why the two sides disagree on a tree exported before `#725`.
        """
        return f"{self.base_commit} {self.based_on}".strip()

    @property
    def rebase_command(self) -> str:
        """`git rebase <ref>`, or "" when this export shares no base.

        Both halves are required. A commit with no mirror is a base only this
        checkout has, and a mirror ref with no commit names a ref carrying
        nothing this tree descends from; neither is something to rebase onto.
        """
        if not self.base_commit or not self.mirror_ref:
            return ""
        return f"git rebase {self.mirror_ref}"

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


def recorded_export(root: Path, app_id: int) -> RecordedExport:
    """Everything the export store holds about ``app_id``, in one read."""
    with ApexStore.load(root) as store:
        entry = store.application(app_id)
    if not entry:
        return RecordedExport()
    return RecordedExport(
        checksum    = str(entry.get("checksum") or "").strip(),
        base_commit = str(entry.get("base_commit") or "").strip(),
        mirror_ref  = str(entry.get("mirror_ref") or "").strip(),
    )


def recorded_signature(root: Path, app_id: int) -> str:
    """The checksum `export_apex` stored for ``app_id`` when it wrote the tree."""
    return recorded_export(root, app_id).checksum


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
    on_target : str | None = None,
) -> ApexSignatures:
    """Read all three before anything is written, which is the whole point.

    The live read is of the application being WRITTEN (``target_id``) and the
    recorded one is of the application the tree came FROM (``app_id``). On a
    deploy in place the two ids are the same and the comparison is the freshness
    gate; on a retarget they differ and the live read asks about the sandbox.

    ``on_target`` is that live read taken ALREADY, and the deploy passes it
    whenever the ADT #726 build-status lock went on: setting build status moves
    the application's export checksum, so a read taken after the lock would
    compare a value ADT itself had just written against the one the export
    recorded, and refuse every guarded deploy (ADT #745). The lock captures the
    target as it stood one statement before it wrote to it, which is the only
    reading of "what is on the target" that means anything here.
    """
    recorded = recorded_export(root, app_id)
    return ApexSignatures(
        app_id      = app_id,
        target_id   = target_id,
        on_target   = (
            on_target if on_target is not None
            else read_target_signature(gateway, target_id)
        ),
        based_on    = recorded.checksum,
        deploying   = tree_signature(tree_root),
        base_commit = recorded.base_commit,
        mirror_ref  = recorded.mirror_ref,
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
    ]
    if signatures.base_commit:
        # Only when there is one. An export with no recorded commit is still the
        # ordinary case, and a row reading `(none)` would be noise in every log a
        # project not using `-mirror` writes.
        mirror = f" ({signatures.mirror_ref})" if signatures.mirror_ref else ""
        lines.append(f"--   MERGE BASE       | {signatures.base_commit}{mirror}")
    lines.append(f"--   DEPLOYING        | {signatures.deploying or '(empty tree)'}")
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

    A lead line, the two states that disagree, and a `Run:` line naming what
    clears it, the way `stale_full_app_message` and
    `GraphFreshness.failure_message` read.

    The two states are BASE and CURRENT rather than the checksum pair they used
    to be, because a checksum pair is a diagnosis with no cure: it says the two
    sides differ and gives the reader nothing to act on but a re-export. BASE
    carries the commit when the export recorded one, so the `Run:` line can be a
    rebase (ADT #725).
    """
    if signatures.verdict == UNKNOWN:
        lines = [
            f"APP {signatures.app_id} has no recorded signature, so this deploy "
            "cannot tell what the change was based on."
        ]
        lines.append(f"Run: {EXPORT_COMMAND} {signatures.app_id}, then commit the export")
        return "\n".join(lines)
    recovery = signatures.rebase_command or (
        f"{EXPORT_COMMAND} {signatures.app_id}, reconcile the tree"
    )
    return "\n".join(
        [
            f"APP {signatures.target_id} moved since the tree was exported, so an "
            "import would overwrite work this patch never saw.",
            f"  BASE    {signatures.base}",
            f"  CURRENT {signatures.on_target}",
            f"Run: {recovery}, then deploy again (or -force to overwrite)",
        ]
    )


__all__ = [name for name in globals() if not name.startswith("_")]
