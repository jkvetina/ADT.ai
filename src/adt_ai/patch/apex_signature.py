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

**And a fifth, also never compared: WHO moved the target, and WHEN** (ADT #925).
A refusal that ends in a merge sends the developer to somebody else's work, so
it names that somebody: APEX's own `LAST_UPDATED_BY` / `LAST_UPDATED_ON`, the
newer of the application row and its newest page. It is read BEFORE the ADT #726
lock for the reason the checksum is (#745): the lock's build-status write stamps
the application with the deploy's own user, and a read after it would blame the
developer being refused. A read that fails answers "unknown" and nothing else.

**A checksum is only meaningful against the environment it was taken from**
(ADT #962). `export_apex` records that environment beside `based_on`, and a
promotion crossing environments, PLAYGROUND exported and deployed onto
WHATEVER, compares the recorded checksum with a live read of the SOURCE
application taken on PLAYGROUND rather than with `on_target`: the target is
being overwritten by design, so its live checksum was never going to match.
``on_target`` still carries what the import log calls LATEST ON TARGET, and
`-deploy` still writes it there; only the verdict's comparison moves, onto a
sixth value, `on_source`, read on the export's own recorded environment. A
same-environment deploy and an export that recorded no environment (every
store written before this) compare `on_target` exactly as before.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.shared.apex_checksum import read_apex_checksum
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
    checksum_at : str = ""
    checksum_env: str = ""
    base_commit : str = ""
    mirror_ref  : str = ""


@dataclass(frozen=True)
class LastChange:
    """Who last moved a live application and when, as APEX recorded it.

    Empty on an application nobody has touched since it was imported: an import
    leaves every row's author and date blank, measured on APEX 26.1.0.
    """

    by : str = ""
    on : str = ""

    @property
    def known(self) -> bool:
        return bool(self.by or self.on)

    def describe(self) -> str:
        """`by JAN on 2026-09-23 16:55`, the log row's one-line form."""
        return f"by {self.by or _NOT_RECORDED} on {self.on or _NOT_RECORDED}"


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
    # Recorded rather than compared, like the two above: who moved the target
    # and when, so the refusal names whose work the merge is with (ADT #925).
    last_change : LastChange = LastChange()
    # When ``based_on`` was taken, so the refusal can say how old the base is.
    based_at    : str = ""
    # The SOURCE application's live checksum, read on the environment the
    # export recorded, only when that differs from the deploy's target (ADT
    # #962). ``None`` means no cross-environment comparison applies: the
    # verdict below compares ``on_target`` exactly as it always has, which is
    # every same-environment deploy and every export that recorded none.
    # ``on_target`` is unaffected either way -- it is still what the import log
    # calls LATEST ON TARGET, because a promotion overwrites the target by
    # design and its live checksum was never going to match.
    on_source   : str | None = None

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
        # `on_source` stands in for `on_target` only for a cross-environment
        # promotion (ADT #962); every other deploy compares its own live read.
        live = self.on_target if self.on_source is None else self.on_source
        if not live:
            # The application does not exist yet, which is what a fresh sandbox
            # id looks like. Nothing is there to be overwritten.
            return OK
        return OK if live == self.based_on else DRIFTED

    @property
    def refused(self) -> bool:
        return self.verdict != OK


def read_target_signature(gateway: Any, app_id: int) -> str:
    """The live APEX checksum of ``app_id``, or empty when it holds no application.

    Read through `shared/apex_checksum`, the reader `export_apex` records with,
    so the live value and ``based_on`` are one read (ADT #962; the block's
    ORA-14552 history, ADT #960, lives with it). Absent is not drifted, so an id
    nothing is installed on comes back as an empty string. APEX reports that by
    raising rather than by answering nothing (see `_NO_APPLICATION_CODE`), so the
    empty answer is made here; every other database error is somebody else's to
    see and re-raises untouched.
    """
    try:
        return read_apex_checksum(gateway, app_id)
    except Exception as error:  # noqa: BLE001 - re-raised unless it is the one case
        if not _is_missing_application(error):
            raise
        return ""


def read_last_change(
    gateway : Any,
    app_id  : int,
    *,
    now     : datetime | None = None,
) -> LastChange:
    """Who last moved ``app_id`` and when, or an empty answer when nobody can say.

    Every failure is the empty answer, not only a missing application: the name
    is a courtesy on a refusal the checksums already decided, and a view a
    grant hides must not turn that refusal into a database error.
    """
    try:
        rows = gateway.fetch_all(queries.APEX_LAST_CHANGE_QUERY, {"app_id": app_id})
    except Exception:  # noqa: BLE001 - attribution never fails a deploy
        return LastChange()
    for row in rows:
        return LastChange(
            by = str(row_value(row, "CHANGED_BY") or "").strip(),
            on = _local_minute(
                str(row_value(row, "CHANGED_ON") or "").strip(),
                str(row_value(row, "DB_NOW") or "").strip(),
                now or datetime.now(),
            ),
        )
    return LastChange()


#: The database clock and this machine's differ by whole time zones plus a few
#: seconds of round trip, so the offset is snapped to the quarter hour every
#: real zone sits on.
_ZONE_STEP = timedelta(minutes=15)


def _local_minute(changed_on: str, db_now: str, now: datetime) -> str:
    """``changed_on`` moved from the database's clock onto this machine's.

    The export stamp YOUR BASE prints is local, so CHANGED ON has to be too or
    the two cannot be read against each other. A value that does not parse is
    printed as APEX gave it, to the minute, rather than dropped.
    """
    try:
        changed = datetime.strptime(changed_on, _DB_FORMAT)
        offset = now - datetime.strptime(db_now, _DB_FORMAT)
    except ValueError:
        return changed_on[:16]
    snapped = round(offset / _ZONE_STEP) * _ZONE_STEP
    return (changed + snapped).strftime("%Y-%m-%d %H:%M")


_DB_FORMAT = "%Y-%m-%d %H:%M:%S"


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
        checksum     = str(entry.get("checksum") or "").strip(),
        checksum_at  = str(entry.get("checksum_at") or "").strip(),
        checksum_env = str(entry.get("checksum_env") or "").strip(),
        base_commit  = str(entry.get("base_commit") or "").strip(),
        mirror_ref   = str(entry.get("mirror_ref") or "").strip(),
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
    last_change : LastChange | None = None,
    *,
    target_env : str = "",
    owner      : str = "",
    signature_gateway_factory: Callable[[str, str], Any] | None = None,
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

    ``last_change`` is taken by the same lock for the same reason: its write
    stamps the application with the deploy's own user (ADT #925).

    ``target_env``, ``owner`` and ``signature_gateway_factory`` answer a
    question `on_target` cannot (ADT #962): whether the export this tree came
    from was taken on a DIFFERENT environment than the one being deployed to.
    PLAYGROUND's recorded checksum read against WHATEVER's live one can never
    match, so when the export recorded an environment and it differs from
    ``target_env`` (case-insensitive), a second live read of the SOURCE
    application (``app_id``, not ``target_id``) is taken on THAT environment,
    through ``signature_gateway_factory(environment, owner)``, and it is what
    the verdict compares instead of `on_target`. A read that fails with a real
    database error is somebody else's to see, the same as `on_target`'s own
    read; only a genuinely absent recorded environment, a matching one, or no
    factory at all skip it, and then the verdict compares `on_target` exactly
    as before.
    """
    recorded = recorded_export(root, app_id)
    on_source: str | None = None
    if (
        signature_gateway_factory is not None
        and recorded.checksum_env
        and target_env
        and recorded.checksum_env.upper() != target_env.upper()
    ):
        on_source = read_target_signature(
            signature_gateway_factory(recorded.checksum_env, owner), app_id
        )
    return ApexSignatures(
        app_id      = app_id,
        target_id   = target_id,
        on_target   = (
            on_target if on_target is not None
            else read_target_signature(gateway, target_id)
        ),
        based_on    = recorded.checksum,
        based_at    = recorded.checksum_at,
        deploying   = tree_signature(tree_root),
        base_commit = recorded.base_commit,
        mirror_ref  = recorded.mirror_ref,
        on_source   = on_source,
        last_change = (
            last_change if last_change is not None
            else read_last_change(gateway, target_id)
        ),
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
    if signatures.last_change.known:
        # Only when APEX recorded one, for the reason MERGE BASE above gives.
        lines.append(f"--   LAST CHANGED     | {signatures.last_change.describe()}")
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
    """The refusal: a headline, who and when on both sides, and the way out.

    Rows a person reads, not values a machine compares (ADT #925, Jan: the
    checksums do not matter on screen). The import log keeps them. YOUR BASE
    carries the export's own date so the gap to CHANGED ON reads at a glance,
    and the commit with its shared ref when the export recorded one, since that
    is what the `Run:` rebase lands on (ADT #725). Every line sits two spaces in,
    so the block reads as one; the error screen dedents it before adding its own
    two, so on screen it sits under `ERROR - PATCH FAILED:` at two (ADT #934).

    The first line is a short uppercase headline and the reason moves below it
    (ADT #934). Jan, on the sentence that opened it: *"Should be shorter and
    uppercased."*
    """
    if signatures.verdict == UNKNOWN:
        headline, steps = unrecorded_rows(signatures)
        return "\n".join(
            [
                headline,
                "",
                "  This deploy cannot tell what the change was based on.",
                "",
                *steps,
            ]
        )
    return "\n".join(
        [
            f"  APP {signatures.target_id} CHANGED SINCE YOUR EXPORT",
            "",
            "  Deploying now would overwrite that work.",
            "",
            *change_rows(signatures),
            "",
            f"  1) {recovery_command(signatures)}",
            "  2) deploy again",
            "  3) or -force to overwrite",
        ]
    )


def change_rows(signatures: ApexSignatures) -> list[str]:
    """`CHANGED BY`, `CHANGED ON` and `YOUR BASE`, two spaces in.

    Shared by the `-deploy` refusal and the `-create` warning that predicts it
    (ADT #957), so the two screens cannot come to disagree about who moved the
    application or what the change was based on.
    """
    change = signatures.last_change
    return [
        f"  CHANGED BY  | {change.by or _NOT_RECORDED}",
        f"  CHANGED ON  | {change.on or _NOT_RECORDED}",
        f"  YOUR BASE   | {_base(signatures)}",
    ]


def unrecorded_rows(signatures: ApexSignatures) -> tuple[str, list[str]]:
    """The headline and the numbered steps for an export that recorded no checksum."""
    return (
        f"  APP {signatures.app_id} HAS NO RECORDED SIGNATURE",
        [f"  1) {export_command(signatures)}", "  2) commit the export"],
    )


def recovery_command(signatures: ApexSignatures) -> str:
    """The rebase when the export shared a base, else the re-export (ADT #725)."""
    return signatures.rebase_command or f"{export_command(signatures)}, reconcile the tree"


def export_command(signatures: ApexSignatures) -> str:
    """`adtai export_apex -apexlang -app <id>`, the re-export on its own.

    The bare command, so the `-create` warning can number it as a step of its
    own (ADT #961) while the `-deploy` refusal keeps its one `Run:` sentence.
    """
    return f"{EXPORT_COMMAND} {signatures.app_id}"


#: What a row says when APEX kept no value, which is every row an import wrote.
_NOT_RECORDED = "(not recorded)"


def _base(signatures: ApexSignatures) -> str:
    """`2026-09-23 16:58 (a5e59eb0 on db/dev)`, each part only when recorded."""
    commit = signatures.base_commit[:8]
    if commit and signatures.mirror_ref:
        commit += f" on {signatures.mirror_ref}"
    when = signatures.based_at or "(export time not recorded)"
    return f"{when} ({commit})" if commit else when


__all__ = [name for name in globals() if not name.startswith("_")]
