"""`adtai patch -drop`: remove the sandbox applications a `-app` deploy created.

Split out of ``commands_patch_deploy.py`` by ADT #670, which pushed that module
past the 24 KB context guard (`tests/contracts/test_context_file_size.py`). The
seam is the one the command's own docstring already draws: `-drop` is an ACTION
beside `-archive` rather than a modifier on `-deploy`, it names no patch folder,
and it shares nothing with the deploy but the gateway factories. A module that
crosses the guard is split, never registered as debt.

The import direction is deliberate: this module reads
`_patch_deploy_gateway_factories` out of `commands_patch_deploy`, and nothing
there imports back. `cli/patch_build.py` is the only caller of
`run_drop_applications` and reaches it here.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from adt_ai.cli.commands_patch_deploy import _patch_deploy_gateway_factories
from adt_ai.cli.constants import (
    GatewayFactory,
    PatchError,
    QueryGateway,
    print_adt_header,
)
from adt_ai.cli.context import (
    _config_search_paths,
    _print_connection_block,
    _repo_root,
)
from adt_ai.patch.apex_drop import (
    ApexApplication,
    ApexRelease,
    SandboxApplication,
    build_drop_script,
    droppable_by,
    identity_source,
    ownership_refusal,
    read_applications,
    read_release,
    resolve_sandbox,
    write_drop_log,
)
from adt_ai.patch.selection import apex_owner_schemas, apex_patch_schema
from adt_ai.shared.identity import commit_account, load_identity
from adt_ai.shared.streamed_table import StreamedTable
from adt_ai.shared.tables import _AdtTableLayout, _compute_adt_layout

#: `export_apex -reveal`'s own section, reused rather than re-minted: `-drop`
#: reports applications, and growing the console is a reviewed act (`#372`).
APEX_APPLICATIONS_HEADER = "APEX APPLICATIONS:"

#: The two outcomes a drop row carries. Cell values rather than console
#: furniture, the same way the deploy table's own `SUCCESS`/`ERROR` are.
DROPPED_STATUS = "DELETED"
FAILED_STATUS  = "FAILED"

#: What an OPEN row says while its application is still being removed. The word
#: the deploy table already uses for exactly this state (`#441`), so a running
#: `-drop` row mints no console string of its own. Not a member of the two
#: outcomes above, because it never reaches a finished row, but it sizes STATUS
#: all the same: the live paint and the row that replaces it have to draw the
#: same geometry, or the shorter closing row leaves the longer paint's tail on
#: screen.
DROP_STATUS_RUNNING = "IN PROGRESS"

#: The columns the drop table carries, in the order it prints them.
DROP_COLUMNS = ("APPLICATION", "ALIAS", "SOURCE", "STATUS")

#: Where the streamed row breaks. `APPLICATION | ALIAS | SOURCE` are read off
#: the live application list before anything is removed; `STATUS` is the answer
#: the drop returns, and it is the only cell that has to wait.
DROP_SPLIT = DROP_COLUMNS.index("STATUS")

#: `APPLICATION` and `SOURCE` are application ids, so they are digits that should
#: line up rather than text; declaring them is the caller saying what the column
#: IS, which is what `_compute_adt_layout` asks for when a run has no rows yet to
#: sniff.
DROP_NUMERIC = ("APPLICATION", "SOURCE")


def run_drop_applications(
    args: argparse.Namespace,
    *,
    root: Path,
    config: dict[str, object],
    gateway_factory: GatewayFactory | None,
) -> int:
    """Remove the sandbox applications a `-app` deploy created (ADT #592).

    An ACTION beside `-archive` rather than a modifier on `-deploy`: removing a
    sandbox application is the step after a card lands exactly as archiving a
    patch folder is, and a deploy that drops instead of importing is a deploy
    that deploys nothing. So it takes no `-name` and touches no patch folder.

    **It runs where the grant already is.** `#142` carved `patch -deploy` out of
    the agent SQLcl block, and this is the same command reaching the same SQLcl
    path, so no fresh carve-out is owed for a mechanic that would be identical
    under a top-level verb.

    **The rail refuses before anything is written, and `-force` never widens it.**
    Every id is resolved against the live application list first, so a run naming
    one droppable id and one production id removes neither. `patch/apex_drop.py`
    owns the rule; what happens here is the connecting and the reporting.

    **The ownership check sits behind the rail, and `-force` is its override
    (ADT #639).** The creator APEX recorded for each target is compared with the
    developer in `config/IDENTITY.yaml`, read the way every other `-my` reads it,
    and a sandbox somebody else created refuses with both names on the screen.
    Judged for every id before the first drop, the same ordering the rail holds
    to. A target recording NO creator drops without the flag (`#682`): no APEX
    import writes that column, so refusing on it made `-force` routine.

    `-target` is required for the same reason `-deploy` requires it: a
    destructive action against whichever environment happens to be the
    connection file's default is exactly what a rail exists to prevent.
    """
    if not args.target:
        print("Missing required target: use -target TARGET with -drop", file=sys.stderr)
        print(file=sys.stderr)
        return 2
    # The sandbox's own source application answers for the schema (ADT #602),
    # read off the first id because a run drops sandboxes of one application's
    # workspace. Same reader the install-script grouping uses, so `-drop`
    # connects as the schema the patch it is cleaning up deployed as.
    owners = apex_owner_schemas(root)
    schema = apex_patch_schema(config, owners, next(iter(args.drop), None))
    gateway_factory, _dev, connection_provider = _patch_deploy_gateway_factories(
        args, root, config, gateway_factory
    )
    gateway = gateway_factory(schema)
    # **The two dictionary reads happen UNDER the connection header, not after
    # it** (ADT #670). They were described here as settled silence, the class Jan
    # approved on 2026-08-16 for the export checksum and the post-deploy check:
    # two rows immediately below the connection block, over before a reader could
    # notice them. Silence is still the right answer; the PLACEMENT was not.
    # `CONNECTING TO SCHEMA <schema>, <env>:` retires its claim at the blank line
    # under its version rows (`cli/stream_tracker.py`), so a read sitting after
    # the block is a blocking call behind a finished section and
    # `AnnouncedGateway.guard` records it. It went unseen because the fakes in
    # `tests/cli/test_patch_drop.py` report no version, so the section never got
    # a body to close it; against a real instance it fires on every `-drop`.
    #
    # A list rather than two `nonlocal` slots so nothing downstream has to be
    # `| None`: the callback either fills it or raises, and a raise never reaches
    # the unpacking below.
    workspace: list[tuple[ApexRelease, dict[int, ApexApplication]]] = []
    try:
        _print_connection_block(
            gateway,
            connection_provider(schema),
            schema          = schema,
            environment     = args.target,
            debug           = args.debug,
            before_versions = lambda: workspace.append(
                (read_release(gateway), read_applications(gateway))
            ),
        )
    except ValueError as error:
        raise PatchError(str(error)) from error
    release, applications = workspace[0]
    # `IDENTITY.yaml` first, git as the fallback, through the one reader every
    # `-my` shares (ADT #469): `apex_account` is the login APEX records as a
    # creator, which is why it is the half compared here. The file is loaded
    # once more than `resolve_commit_identity` would, so the refusal can say
    # which of the two sources the name it prints came from.
    identity = load_identity(_config_search_paths(args.config_dir, root, _repo_root()))
    account = commit_account(identity, root)
    try:
        sandboxes = [resolve_sandbox(app_id, applications) for app_id in args.drop]
    except ValueError as error:
        raise PatchError(str(error)) from error
    if not args.force:
        refusal = ownership_refusal(
            sandboxes,
            account,
            environment = args.target,
            source      = identity_source(identity),
        )
        if refusal is not None:
            raise PatchError(refusal)
    # Above the first DROP and below the rail, so a refused run prints no header
    # at all rather than an empty section over its `PATCH FAILED:` screen, and the
    # rows fill in behind it, which is the shape §Console output contract
    # prescribes for a wait. `APEX APPLICATIONS:` is `export_apex -reveal`'s own
    # section and no new string is minted for this one (`#372`).
    #
    # **The rows now fill in behind it as each application goes, rather than
    # arriving together once the last one has** (ADT #678). `#658` made this a
    # loop so a mid-run failure could not unwind past the table, which fixed what
    # a CRASHED run reports and left what a WORKING run reports untouched: the
    # header still sat alone for the whole of the wait, and Jan watched ten ids
    # take 39 seconds behind it before a single row appeared. That is the exact
    # anti-pattern §Console output contract names ("Never build the whole row and
    # print it after the work"); the receipt guarantee `#658` bought survives
    # because a streamed row is on screen before the drop that closes it, which
    # is strictly stronger than a table printed afterwards.
    #
    # The failure itself is not swallowed. Its row is completed with `FAILED`
    # first, then the exception is re-raised for the shared handler to render
    # through `context_errors`, exactly as every other command path reports one,
    # and the ids behind it are left alone: a `-drop` that has already met an
    # unexplained SQLcl refusal has no business continuing to remove
    # applications.
    reporter = ConsoleDropReporter()
    reporter.begin_drop(sandboxes)
    statuses: list[str] = []
    failure: Exception | None = None
    for sandbox in sandboxes:
        reporter.begin_application(sandbox)
        try:
            row = _drop_application(
                gateway,
                sandbox,
                release,
                root,
                config      = config,
                schema      = schema,
                environment = args.target,
                override    = _override_note(sandbox, account, forced=args.force),
            )
        except Exception as error:
            row = _drop_row(sandbox, FAILED_STATUS)
            failure = error
        reporter.end_application(row)
        statuses.append(str(row["STATUS"]))
        if failure is not None:
            break
    reporter.end_drop()
    if failure is not None:
        raise failure
    return 1 if any(status != DROPPED_STATUS for status in statuses) else 0


def _drop_row(sandbox: SandboxApplication, status: str) -> dict[str, object]:
    """One application as one row. The three left cells are known before the drop.

    That split is what makes the row streamable: `APPLICATION`, `ALIAS` and
    `SOURCE` are read off the live application list the rail already resolved
    against, so only `STATUS` has to wait for the work.
    """
    return {
        "APPLICATION": sandbox.target.app_id,
        "ALIAS": sandbox.target.alias,
        "SOURCE": sandbox.source.app_id,
        "STATUS": status,
    }


def _drop_layout(rows: list[dict[str, object]]) -> _AdtTableLayout:
    """Column geometry, shared by the streamed row, the live paint and a batch render.

    The STATUS reservation is unconditional and measures the RUNNING word beside
    the two outcomes, so the column is sized once for every string this table can
    put in it. Sizing it only for the live render would leave the closing row
    narrower than the paint under it on a terminal and, worse, would break the
    byte-identity between the live and quiet renders that the console contract
    turns on. That trade is stated rather than absorbed: `IN PROGRESS` is eleven
    characters against `DELETED`'s seven, so every drop table is four characters
    wider, live or not (the same cost `#444` accepted on the deploy table).

    **A row measured here carries the word an open row shows, never a blank
    STATUS**, and that is a correctness rule rather than a tidiness one.
    `_compute_adt_layout` sniffs a column as numeric when every cell is a digit
    string OR empty, so a plan whose STATUS cells were blank sized the column
    numeric and RIGHT-aligned it, while a batch render of the finished rows saw
    `DELETED` and left-aligned the same column. The first live run printed
    `SOURCE        STATUS` where every unit test printed `SOURCE   STATUS`,
    because each test happened to size its layout from rows that already carried
    an outcome. Alignment is a property of the value, so the plan states the
    value the column will actually hold.
    """
    return _compute_adt_layout(
        rows,
        list(DROP_COLUMNS),
        {
            "STATUS": max(
                len(status)
                for status in (DROPPED_STATUS, FAILED_STATUS, DROP_STATUS_RUNNING)
            )
        },
        DROP_NUMERIC,
    )


class ConsoleDropReporter:
    """Streams `APEX APPLICATIONS:` so each application's wait sits on its own row.

    Jan, on the ten-id run this was filed on: "YOU PRINT THE APP WHICH YOU ARE
    DROPPING, YOU PRINT THE STATUS WHEN YOU ARE DONE AND YOU MOVE TO THE NEXT
    LINE." That is what the three calls below do, and `shared/streamed_table.py`
    is where the drawing lives, so this command and `patch -deploy` cannot drift
    apart on the seam, the repaint or the erase.

    There is no ticker here, unlike the deploy reporter. A deploy is one long row
    whose only sign of life is a moving counter; a drop is many short rows, and
    the row that appears when the next application starts is itself the progress.
    A per-row timer would be a console addition rather than a flush of structure
    that already exists, which is Jan's call to make and not this card's.
    """

    def __init__(self, live: bool | None = None) -> None:
        self._live = live
        self._table: StreamedTable | None = None

    def begin_drop(self, planned: list[SandboxApplication]) -> None:
        """The section, then a table sized off every row the run can print.

        Sizing from the PLAN rather than from results is what lets the header go
        up before the first drop: the ids and aliases are already known, so no
        row printed later can widen a column that is on screen. It takes the
        sandboxes rather than rows so the caller cannot hand it a plan whose
        STATUS cells are blank, which is the shape that mis-aligned the column
        (see `_drop_layout`).
        """
        print_adt_header(APEX_APPLICATIONS_HEADER)
        self._table = StreamedTable(
            _drop_layout([_drop_row(sandbox, DROP_STATUS_RUNNING) for sandbox in planned]),
            split = DROP_SPLIT,
            live  = self._live,
        )
        self._table.open_table()

    def begin_application(self, sandbox: SandboxApplication) -> None:
        self._open_table().begin_row(
            list(_drop_row(sandbox, DROP_STATUS_RUNNING).values())
        )

    def end_application(self, row: dict[str, object]) -> None:
        self._open_table().end_row([row[column] for column in DROP_COLUMNS])

    def end_drop(self) -> None:
        self._open_table().close_table()

    def _open_table(self) -> StreamedTable:
        """The table `begin_drop` opened.

        Named here rather than assumed, so a caller driving the reporter out of
        order says so instead of raising on `None` two frames down.
        """
        if self._table is None:  # pragma: no cover, ordering is the command's
            raise RuntimeError("begin_drop must open the table before a row is drawn")
        return self._table


def _override_note(sandbox: SandboxApplication, account: str, *, forced: bool) -> str | None:
    """What `-force` stepped over for this sandbox, or ``None`` when nothing.

    Written only when the flag changed the outcome: a forced drop of your own
    sandbox is an ordinary drop and its receipt says so by carrying no row. A
    sandbox recording no creator reads the same way since `#682`, the check
    letting it through on its own, so the flag stepped over no refusal and a row
    here would name one that never happened. The `CREATED BY` row still says
    `(none)`, that being what APEX recorded either way.
    """
    if not forced or droppable_by(sandbox.target, account):
        return None
    return f"-force dropped a sandbox created by {sandbox.target.created_by}"


def _drop_application(
    gateway: QueryGateway,
    sandbox: SandboxApplication,
    release: ApexRelease,
    root: Path,
    *,
    config: dict[str, object],
    schema: str,
    environment: str,
    override: str | None,
) -> dict[str, object]:
    """One application removed, reported as one row.

    The outcome is read back off `apex_applications` rather than parsed out of
    the SQLcl transcript: the question the row answers is whether the application
    is gone, and only the dictionary can say so. A transcript that reported
    success over an application still standing is exactly the false SUCCESS
    `#312` was filed on.
    """
    output = gateway.sqlcl_request(build_drop_script(sandbox, release), root)
    survived = read_applications(gateway).get(sandbox.target.app_id) is not None
    outcome = FAILED_STATUS if survived else DROPPED_STATUS
    write_drop_log(
        root,
        config,
        schema      = schema,
        environment = environment,
        sandbox     = sandbox,
        outcome     = outcome,
        output      = output,
        override    = override,
    )
    return {
        "APPLICATION": sandbox.target.app_id,
        "ALIAS": sandbox.target.alias,
        "SOURCE": sandbox.source.app_id,
        "STATUS": outcome,
    }


__all__ = [name for name in globals() if not name.startswith("__")]
