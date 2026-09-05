"""Landing an APEXlang tree on an application id.

`export_apex -apexlang` writes the tree and `validate` compiles it; this module
is the third side of that round trip, the one that puts the tree back into the
Builder. It owns two questions and nothing else: which application id the tree
lands on, and what SQLcl is asked to do about it.

**One flag carries it, `-app`, and its value is the TARGET rather than a filter**
(ADT #592, Jan 2026-08-29). `-fullapp` used to say which applications ship whole
and there was no way at all to say where they land, so the two questions folded
into one flag: *"you can pass any number you like; if you dont pass the number,
that means no app id changes"*. Absent is no APEX deploy, bare is every
application the patch touches under its own id, and a value is the id the tree
lands on instead. The old name is on `REMOVED_COMPATIBILITY_FLAGS` so the change
in what a value MEANS cannot be met silently.

**The sandbox id is derived, never configured per developer.** The application
number carries the task number, so app `1100` on task `123` imports as
`1100123`. Uniqueness comes from the task number, which is already unique across
developers, so nobody maintains a range and no two people can collide by
construction.

**`-alias` travels with `-id`, always**, which is an invariant here rather than a
caller's habit. An APEX alias is unique within a workspace, so importing app
1000's tree under a test id while keeping its alias collides with the original,
and APEX reports that at import time, long after the run believed it had a
sandbox. `build_import_script` refuses the half-formed pair rather than emitting
it.

The flag surface is measured, not remembered: SQLcl `26.2.1.209.2118` ships
`apex import` with `-id`, `-alias`, `-name`, `-schema`, `-workspaceid`/
`-workspace`, `-imageprefix`, `-offset`, `-proxy`, `-deployment` and
`-buildstatus`, read out of `oracle/dbtools/extension/apex/command/Help.properties`
inside `lib/ext/dbtools-apex.jar`, where `apex import -input ./projects/x -id
1001` is Oracle's own documented example. Retargeting is therefore a flag on the
command and never an edit to the tree's `deployments/default.json`, which is what
lets a promote import the byte-identical tree a test import already validated.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adt_ai.patch.sql_literal import escape_literal
from adt_ai.shared.sqlcl_quoting import reject_unquotable

# Quoted for the reason `validate.runner.VALIDATE_COMMAND` is: SQLcl tokenizes
# the line itself, so an unquoted staging path carrying a space truncates in
# silence.
IMPORT_COMMAND = 'apex import -input "{input}"'

# The audit stamp, run in the import's own SQLcl session (ADT #682). The
# workspace is read off the application row rather than passed in: the stamp
# needs one and the row already names it, so nothing new has to be configured
# or threaded down for the block to run. The version is read for the same
# reason -- it is handed back unchanged, which is all APEX needs to stamp.
STAMP_BLOCK = """DECLARE
    l_workspace     apex_applications.workspace%TYPE;
    l_version       apex_applications.version%TYPE;
BEGIN
    SELECT workspace, version
    INTO   l_workspace, l_version
    FROM   apex_applications
    WHERE  application_id = {app_id};
    --
    APEX_UTIL.SET_WORKSPACE(p_workspace => l_workspace);
    APEX_CUSTOM_AUTH.SET_USER('{account}');
    --
    APEX_APPLICATION_ADMIN.SET_APPLICATION_VERSION(
        p_application_id    => {app_id},
        p_version           => l_version
    );
    COMMIT;
END;
/"""


@dataclass(frozen=True)
class ApexTarget:
    """What `-app` asked for, resolved once at the parse edge.

    ``full_app_ids`` is what the existing selection readers already understand
    (`patch/full_app.py`): ``None`` is nothing full, ``[]`` is every application
    the patch touches. Retargeting never narrows the selection, it only moves
    where the selection lands, so a target id still ships every application whole
    and the two questions stay separable below this line.
    """

    selected     : bool
    target_id    : int | None
    full_app_ids : list[int] | None


def resolve_target(values: list[int] | None) -> ApexTarget:
    """The three states of `-app`, told apart once.

    A second id is refused rather than reduced to the first. Several
    applications cannot fold onto one id, and taking ``values[0]`` would deploy
    one of them and drop the other behind a correct-looking screen, which is the
    shape `#255` was filed on when a patch folder was selected by prefix.
    """
    if values is None:
        return ApexTarget(selected=False, target_id=None, full_app_ids=None)
    if len(values) > 1:
        named = ", ".join(str(value) for value in values)
        raise ValueError(
            f"-app takes one target application id, got {len(values)}: {named}. "
            "Several applications cannot land on one id."
        )
    return ApexTarget(
        selected     = True,
        target_id    = values[0] if values else None,
        full_app_ids = [],
    )


def derive_sandbox_app_id(app_id: int, task: int) -> int:
    """The application number carrying the task number, as one id.

    Concatenation rather than arithmetic on purpose: the task number's own width
    is what keeps the two halves readable in the Builder's application list, and
    an operator reading `1100123` can see both the application and the card it
    belongs to without a lookup.
    """
    if app_id <= 0:
        raise ValueError(f"application id must be positive, got {app_id}")
    if task <= 0:
        # A zero or negative task number produces `1100` back (a collision with
        # the real application) or a `-` in the middle of an integer literal.
        raise ValueError(f"task number must be positive, got {task}")
    return int(f"{app_id}{task}")


def recover_task_number(app_id: int, target_id: int) -> int | None:
    """The task half of a derived id, inverting :func:`derive_sandbox_app_id`.

    That function concatenates `<app_id><task>` into one id, so undoing it is a
    prefix strip rather than arithmetic: take `app_id`'s own digits off the
    front of `target_id` and read what is left as the task. `None` when
    `target_id` does not look derived from `app_id` at all: no matching prefix,
    a task of zero, or a remainder no derivation could have written.

    **The inverse is EXACT, and the leading zero is why** (ADT #702). A
    remainder of `0123` used to be `lstrip("0")`-ed into task `123`, so
    `recover_task_number(100, 1000123)` answered `123`, a pair that derives
    `100123` and can never derive `1000123`. Recovery that reaches ids the
    derivation cannot is not an inverse, and downstream it let
    `apex_drop.resolve_sandbox` clear an application whose number merely
    RESEMBLES a derived sandbox id for a destructive drop. So the recovered task
    is re-derived and required to equal the target it came out of, which is one
    rule rather than a list of remainder shapes to keep in step with the
    derivation.

    One rule, used by both `apex_drop._prefix_sources` (deciding whether a
    target id IS a derived sandbox at all) and `apex_deploy._task_number`
    (recovering the alias suffix for one already known to be); before `#670`
    each kept its own copy of the identical prefix strip.
    """
    suffix = str(target_id)
    prefix = str(app_id)
    if not suffix.startswith(prefix) or len(suffix) <= len(prefix):
        return None
    task = int(suffix[len(prefix):])
    if task <= 0:
        return None
    # The one rule, and it is the derivation's own: a task is recovered only when
    # it derives back the very id it was recovered from. `0123` reads as `123`,
    # which derives `100123` rather than the `1000123` in hand, so the answer is
    # "not a derived id" without a separate leading-zero test to keep in step.
    return task if derive_sandbox_app_id(app_id, task) == target_id else None


def derive_sandbox_alias(alias: str, task: int) -> str:
    """The alias that travels with a derived id, in the same step that derives it.

    Case preserving, because an alias is the application's own and a run has no
    business normalising it; the task suffix alone is what makes it unique in the
    workspace.
    """
    if not alias:
        raise ValueError("cannot derive a sandbox alias from an empty alias")
    if task <= 0:
        raise ValueError(f"task number must be positive, got {task}")
    return f"{alias}_{task}"


def build_import_script(
    input_path : Path,
    target     : ApexTarget,
    alias      : str | None = None,
    account    : str = "",
) -> str:
    """The SQLcl script that lands ``input_path`` on the target application.

    One call per tree, and `exit;` closes it, the same shape
    `validate.runner._build_script` already uses so the two halves of the round
    trip cannot drift on how a SQLcl script is spelled.

    ``account`` is the developer `shared/identity` resolved, and a retarget
    carrying one is followed by :data:`STAMP_BLOCK` in the same session (ADT
    #682). An APEXlang import writes no audit column at all -- measured on APEX
    26.1, an export taken with `p_with_audit_info` set is byte-identical to one
    taken without it, and the tree carries no `create_flow` to hang a
    `p_created_by` on -- so a sandbox lands owned by nobody and the Builder
    shows it as such. Jan, 2026-09-04: *"MY test app looks in APEX like it was
    created by ME and not by noone nowhere!"*

    The stamp is a write through a supported API rather than an audit setter,
    because APEX exposes none: `SET_APPLICATION_VERSION` stamps the row, so the
    version already on it is read and handed straight back. **One call, and the
    value it passes is identical** -- measured on APEX 26.1, the API writes the
    audit columns even when nothing about the version changes, on a row whose
    columns were empty and on one already stamped. Jan asked whether the second
    call could go; it could. The application's own version text never moves, and
    `apex_applications.last_updated_by` / `last_updated_on` then name the
    developer and the moment. `created_by` stays empty because nothing in APEX
    can write it -- which is why `apex_drop.droppable_by` clears an ownerless
    sandbox rather than refusing one.

    **Only a retarget is stamped.** A bare `-app` lands each application under
    its own id, and rewriting a real application's audit row would erase who
    last worked it in the Builder to record a deploy instead.
    """
    if target.target_id is not None and not alias:
        raise ValueError(
            "-alias travels with -id: importing under application id "
            f"{target.target_id} while keeping the source alias collides with "
            "the application the tree was exported from"
        )
    path = input_path.as_posix()
    # Refused when the path holds a `"` SQLcl's quoting cannot carry (ADT #653);
    # the alias comes from an APEX application and takes the same check.
    reject_unquotable(path, role="staging folder")
    command = IMPORT_COMMAND.format(input=path)
    lines = [command]
    if target.target_id is not None and alias:
        reject_unquotable(alias, role="application alias")
        lines = [f'{command} -id {target.target_id} -alias "{alias}"']
        if account.strip():
            lines.append(
                STAMP_BLOCK.format(
                    app_id  = target.target_id,
                    account = escape_literal(account.strip()),
                )
            )
    return "\n".join([*lines, "exit;"])


__all__ = [name for name in globals() if not name.startswith("_")]
