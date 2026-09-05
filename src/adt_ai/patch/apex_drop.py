"""Removing the sandbox application a `-app` deploy created (ADT #592).

`patch/apex_import.py` derives a sandbox id from an application and a task
number, and `patch/apex_deploy.py` lands a tree on it. Nothing overwrites
anything, which is the property that makes the workflow safe and also the reason
this module exists: task `124` lands on `100124` beside task `123`'s `100123`, so
whatever creates the sandbox has to remove it when the card closes or the
workspace silently fills with dead copies of the real application.

**SQLcl's `apex` command has no drop verb**, measured 2026-08-29: its whole set
is `list`, `log`, `export`, `version`, `validate`, `import` and `generate`. The
transport is the one every legacy full export already runs before it imports,
supplied by Jan on 2026-08-30: `wwv_flow_imp.import_begin`, then
`wwv_flow_imp.remove_flow(wwv_flow.g_flow_id)`, then `import_end` and a commit.
It runs as the PARSING SCHEMA, `import_begin` having set the workspace context,
so `apex_instance_admin.remove_application` and the instance-admin grant it needs
are not involved anywhere. `sandbox/apex/100_ORDERS/f100.sql` line 63 is the
shipped example.

**The rail is what makes it safe, and it has no override.** An id is droppable
only when it is a DERIVED sandbox id, which is two facts rather than one:

  * ``<app><task>``, where ``<app>`` is an application the workspace actually
    holds and ``<task>`` is a positive integer, and
  * the target carries the derived ``<SOURCE_ALIAS>_<task>`` alias.

A real application's own id is never a strict prefix-extension of itself, so
`-drop 100` refuses by construction and names what sits at that id. `-force`
never reaches the rail: a destructive drop a flag can widen has no rail at all,
which is the one place `-force`'s ordinary `patch` meaning (override a refusal,
`#309`) is not on offer.

The alias half is not decoration. `100123` could be somebody's real application
that merely happens to start with `100`, and the prefix alone cannot tell the
two apart; the alias is the fingerprint `derive_sandbox_alias` wrote in the same
step that derived the id, so it is what proves a `-app` deploy minted the target.

**Behind the rail sits the ownership check, and that one `-force` does override
(ADT #639).** A sandbox somebody else created is not yours to drop: when the
target records a creator (`apex_applications.created_by`, read live off the
target environment) it has to be the developer running the command, `apex_account`
in `config/IDENTITY.yaml` with git's `user.name` as the fallback, compared without
regard to case. Jan, 2026-09-01: *"verify the app owner (create by) on the fly
from the target env and compare with user IDENTITY"*. A mismatch refuses and
names who did create it; `-force` drops it anyway and the receipt records the
override.

**A target recording NO creator drops without `-force` (ADT #682).** It refused
until then, on the reading that nothing can verify what was never written -- but
the column is one no import can fill. Measured on SANDBOX (APEX 26.1,
2026-09-04): every application there carries an empty `created_by`, an APEXlang
`apex import` leaves it empty (`#639`, and `wwv_flow.g_user` set beforehand does
not change it), and APEX exposes no `p_created_by` at all on its flow-level
import API -- `wwv_flow_imp.create_flow` takes `p_last_updated_by` and
`p_last_upd_yyyymmddhh24miss` and nothing else audit-shaped. So an application
imported from an export taken without audit columns can never carry the value the
check was reading, and requiring the override for it made `-force` the routine
way to drop an ordinary sandbox, which is the one thing a destructive flag must
not become.

Removing an application nobody is recorded as having made steps over nobody, so
there is no refusal to override: the rail above still proves the target is a
derived sandbox, and that is the safety property. The two questions come apart
here rather than fold together -- :func:`droppable_by` answers whether the check
lets it through, :func:`owned_by` still answers whose it is, and the receipt's
`OVERRIDDEN` row is written from the second so a forced drop of an
unattributable sandbox no longer claims to have overridden somebody.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.patch import queries
from adt_ai.patch.apex_import import derive_sandbox_alias, recover_task_number
from adt_ai.patch.deploy_paths import deploy_log_folder
from adt_ai.patch.settings import deploy_log_name
from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared import text_files
from adt_ai.shared.row_values import row_value

DEPLOY_COMMAND = "adtai patch -target <ENV> -deploy -app"

#: Where the developer's own name comes from, named in every ownership refusal so
#: the reader knows which file to fix when the two sides disagree.
IDENTITY_HINT = "config/IDENTITY.yaml apex_account"


@dataclass(frozen=True)
class ApexApplication:
    """One row of `apex_applications`, cut down to what the rail and the
    ownership check read."""

    app_id       : int
    alias        : str
    owner        : str
    workspace    : str
    workspace_id : int
    created_by   : str = ""


@dataclass(frozen=True)
class ApexRelease:
    """The two values `import_begin` is given, read off `apex_release`.

    ``version`` is `p_version_yyyy_mm_dd` and comes from `api_compatibility`;
    ``release`` is `p_release` and comes from `version_no`. The pairing is
    measured rather than remembered: on SANDBOX, 2026-08-30, the two answer
    `2026.03.30` and `26.1.0`, which is exactly what the shipped `f100.sql`
    export passes.
    """

    version : str
    release : str


@dataclass(frozen=True)
class SandboxApplication:
    """A target the rail cleared, with the source that authorized it."""

    target : ApexApplication
    source : ApexApplication
    task   : int


def read_applications(gateway: Any) -> dict[int, ApexApplication]:
    """Every application the connected schema can see, keyed by id."""
    found: dict[int, ApexApplication] = {}
    for row in gateway.fetch_all(queries.APEX_WORKSPACE_APPS_QUERY):
        app_id = _integer(row_value(row, "APP_ID"))
        if app_id is None:
            continue
        found[app_id] = ApexApplication(
            app_id       = app_id,
            alias        = str(row_value(row, "APP_ALIAS") or ""),
            owner        = str(row_value(row, "OWNER") or ""),
            workspace    = str(row_value(row, "WORKSPACE") or ""),
            workspace_id = _integer(row_value(row, "WORKSPACE_ID")) or 0,
            created_by   = str(row_value(row, "CREATED_BY") or "").strip(),
        )
    return found


def owned_by(application: ApexApplication, account: str) -> bool:
    """Whether ``account`` is the creator APEX recorded for ``application``.

    Compared without regard to case: APEX upper-cases a workspace login where
    `IDENTITY.yaml` is typed by hand. Nobody owns an application recording no
    creator, and an empty account owns nothing, so both halves have to be there.
    """
    recorded = application.created_by.strip().casefold()
    return bool(recorded) and recorded == account.strip().casefold()


def droppable_by(application: ApexApplication, account: str) -> bool:
    """Whether the ownership check lets ``account`` drop ``application``.

    Wider than :func:`owned_by` by exactly one case, and deliberately so: an
    application recording no creator is droppable by anybody the rail already
    cleared, because there is nobody to step over and no import can write that
    column anyway (ADT #682, see the module docstring). A recorded creator is
    still compared, so somebody else's sandbox needs `-force` as it always did.
    """
    if not application.created_by.strip():
        return True
    return owned_by(application, account)


def identity_source(identity: Mapping[str, Any]) -> str:
    """Where the account being compared came from, for the refusal to name.

    The file when it names an `apex_account`, else git, said so: a refusal that
    read "IDENTITY.yaml says Jan Kvetina" over a checkout with no such file would
    send the reader to fix a line that does not exist.
    """
    if str(identity.get("apex_account") or "").strip():
        return IDENTITY_HINT
    return f"git user.name ({IDENTITY_HINT} is not set)"


def ownership_refusal(
    sandboxes   : Sequence[SandboxApplication],
    account     : str,
    *,
    environment : str,
    source      : str = IDENTITY_HINT,
) -> str | None:
    """The refusal for every sandbox in ``sandboxes`` that is somebody else's.

    ``None`` when none is, which includes every sandbox recording no creator at
    all (ADT #682). Every id is judged before the first is dropped, the same
    ordering the rail holds itself to, and the refusal names each creator
    because the reader's next question is whose sandbox they were about to
    remove. The `Run:` line spells the override out with the refused ids, so the
    one command a reader may want is on the screen rather than guessed.
    """
    refused = [sandbox for sandbox in sandboxes if not droppable_by(sandbox.target, account)]
    if not refused:
        return None
    you = (
        f"{source} says {account}"
        if account
        else f"this checkout names no developer ({IDENTITY_HINT}, else git user.name)"
    )
    # Every refused sandbox records a creator: `droppable_by` lets an unrecorded
    # one through, so the no-creator wording this loop used to carry is gone
    # along with the refusal it explained.
    lines = [
        f"APP {sandbox.target.app_id} ({sandbox.target.alias}) was created by "
        f"{sandbox.target.created_by}, and {you}, so it is not yours to drop."
        for sandbox in refused
    ]
    ids = " ".join(str(sandbox.target.app_id) for sandbox in refused)
    lines.append(
        f"Run: adtai patch -target {environment} -drop {ids} -force drops somebody "
        "else's sandbox anyway"
    )
    return "\n".join(lines)


def read_release(gateway: Any) -> ApexRelease:
    """The APEX version the transport declares, or a refusal naming the gap.

    An instance that reports no release cannot be handed a block claiming one, so
    the run stops rather than guessing a version, which would be a value nobody
    measured sitting in a destructive statement.
    """
    for row in gateway.fetch_all(queries.APEX_RELEASE_QUERY):
        release = str(row_value(row, "VERSION_NO") or "").strip()
        version = str(row_value(row, "API_COMPATIBILITY") or "").strip()
        if release and version:
            return ApexRelease(version=version, release=release)
    raise ValueError(
        "This connection reports no APEX release, so the drop has no version to "
        "declare.\nRun: check that the target schema is APEX enabled"
    )


def resolve_sandbox(
    target_id    : int,
    applications : Mapping[int, ApexApplication],
) -> SandboxApplication:
    """The rail: ``target_id`` cleared as a derived sandbox, or a refusal.

    Raises ``ValueError`` with the message the caller prints. Every refusal names
    the application it found, because the reader's next question is always which
    application they were actually pointing at.
    """
    target = applications.get(target_id)
    if target is None:
        raise ValueError(
            f"APP {target_id} holds no application this schema can see, so there "
            "is nothing to drop.\n"
            "Run: adtai patch -target <ENV> -drop <the id the deploy reported>"
        )
    expected: list[str] = []
    # Longest prefix first: app `1` and app `100` can both prefix `100123`, and
    # only the specific one derived it. The alias settles it either way, so this
    # is about which refusal gets reported when neither matches.
    for source, task in _prefix_sources(target, applications):
        derived = derive_sandbox_alias(source.alias, task)
        if target.alias == derived:
            return SandboxApplication(target=target, source=source, task=task)
        expected.append(f"{source.app_id} ({source.alias}) would carry {derived}")
    if expected:
        raise ValueError(
            f"APP {target_id} carries alias {target.alias}, which no sandbox "
            "import derived, so it is a real application rather than a copy.\n"
            + "\n".join(f"  {line}" for line in expected)
            + f"\nRun: {DEPLOY_COMMAND} <id> creates a droppable sandbox; remove "
            "anything else in the Builder"
        )
    raise ValueError(
        f"APP {target_id} is not a derived sandbox id: it is application "
        f"{target.alias} in workspace {target.workspace}.\n"
        "Run: -drop the <application><task> id a sandbox deploy reported, never "
        "an application's own id"
    )


def build_drop_script(sandbox: SandboxApplication, release: ApexRelease) -> str:
    """The transport, addressed at the sandbox and at nothing else.

    The target's OWN owner and workspace id are what the block declares, read off
    its `apex_applications` row rather than off the source's, so a sandbox whose
    parsing schema differs from the application it was copied from still removes
    itself as itself.

    ``owner`` is escaped before it lands in `p_default_owner => '{owner}'`: a
    schema name carrying an apostrophe would otherwise end that literal early
    and take the rest of the transport with it (`#670`, same defect as
    `signatures.object_rows`).
    """
    return queries.APEX_DROP_BLOCK.format(
        version      = release.version,
        release      = release.release,
        workspace_id = sandbox.target.workspace_id,
        app_id       = sandbox.target.app_id,
        owner        = escape_literal(sandbox.target.owner),
    )


def write_drop_log(
    root        : Path,
    config      : dict[str, Any],
    *,
    schema      : str,
    environment : str,
    sandbox     : SandboxApplication,
    outcome     : str,
    output      : str,
    moment      : datetime | None = None,
    override    : str | None = None,
) -> Path:
    """Write one dictionary-verified receipt below the resolved APEX root.

    ``override`` is the note a `-force` drop of somebody else's sandbox leaves
    behind, rendered as an `OVERRIDDEN` row for the reason the deploy log has
    one: what the flag did is the auditable fact, and a receipt reading like an
    ordinary drop would hide that the ownership check was stepped over.

    A drop names no patch folder, so its artifact belongs to the application's
    own export tree. ``ApexFileResolver`` is the writer that already resolves
    ``path_apex`` and its schema token; reusing it keeps this destructive action
    from inventing a second reading of the configured layout.

    The environment remains folder context and the sandbox application id is
    the filename target. Keeping those two meanings separate is deliberate:
    ``-target DEV`` selects ``logs_DEV/``, while ``100123`` produces
    ``apex_drop_100123`` inside the stamped filename.
    """
    apex_root = ApexFileResolver.from_config(root, config).for_schema(schema).apex_root()
    log_folder = apex_root / deploy_log_folder(config, environment)
    log_folder.mkdir(parents=True, exist_ok=True)
    log_path = log_folder / deploy_log_name(
        config,
        moment = moment or datetime.now(),
        stem   = f"apex_drop_{sandbox.target.app_id}",
        status = outcome,
    )
    labels = (
        ("ENVIRONMENT", environment),
        ("SOURCE APPLICATION", sandbox.source.app_id),
        ("TARGET APPLICATION", sandbox.target.app_id),
        ("ALIAS", sandbox.target.alias),
        ("CREATED BY", sandbox.target.created_by or "(none)"),
        ("DICTIONARY OUTCOME", outcome),
        *(() if override is None else (("OVERRIDDEN", override),)),
    )
    content = "\n".join(
        [
            "-- APEX APPLICATION DROP",
            *(f"--   {label:<18} | {value}" for label, value in labels),
            "",
            output.rstrip(),
        ]
    )
    text_files.write_text(log_path, content)
    return log_path


def _prefix_sources(
    target       : ApexApplication,
    applications : Mapping[int, ApexApplication],
) -> list[tuple[ApexApplication, int]]:
    """Every application whose id is a strict prefix of the target's, with its task.

    Same workspace only: an APEX alias is unique per workspace, so the derivation
    only holds inside one, and matching across the boundary would let an
    application in one workspace authorize dropping an application in another.

    The prefix strip itself is `apex_import.recover_task_number`, the one
    inverse of `derive_sandbox_app_id` both this module and `apex_deploy.py`
    read (`#670`). It is an EXACT inverse (`#702`): a remainder carrying a
    leading zero answers `None`, and every task it does recover re-derives the
    id it came from. So `1000123` is not app `100` on task `123` here, however
    much it looks like it, because that pair derives `100123`. An id no
    derivation can reach is a real application rather than a sandbox.
    """
    found: list[tuple[ApexApplication, int]] = []
    for source in applications.values():
        if source.app_id == target.app_id or source.workspace_id != target.workspace_id:
            continue
        task = recover_task_number(source.app_id, target.app_id)
        if task is not None:
            found.append((source, task))
    return sorted(found, key=lambda pair: -len(str(pair[0].app_id)))


def _integer(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = [name for name in globals() if not name.startswith("_")]
