from __future__ import annotations

from adt_ai.cli.constants import (
    ApexApplication,
    ApexOwnerCount,
    ApexWorkspace,
    DataTable,
    print_adt_header,
    print_adt_table,
)
from adt_ai.shared.progress import FixedWidthProgressPrinter, schema_label

#: A constant rather than a literal at the call site, so a rename sweeps through
#: one edit and the docs grep for it has something to find (`#509`, `#555`).
WORKSPACE_NOT_FOUND_HEADER = "WARNING - WORKSPACE NOT FOUND:"


class ConsoleApexRevealReporter:
    def begin_workspaces(self) -> None:
        """The first title on a `-reveal` screen, before the inventory reads.

        `-reveal` reads every schema's applications, then the workspaces, then
        the owner counts, and only then prints its three tables, deliberately:
        reporting between two reads put a row under a header it had nothing to
        do with (`#360`). That batching left all of it running behind the
        connection block's closing blank, so the title it will print first goes
        up in front of the reads instead (`#372`).
        """
        print_adt_header("WORKSPACES:")

    def workspaces(
        self,
        workspaces: list[ApexWorkspace],
        configured_workspace: str | None = None,
    ) -> None:
        """The instance's workspaces, with the configured one marked `ACTIVE`.

        `configured_workspace` is `apex.workspace` from the connection file. It
        stopped narrowing this screen on `#564` and now only marks a row, so a
        value naming nothing marks nothing rather than emptying the table.
        Matched case-blind: both sides are Oracle identifiers, and the file
        spells them however the person who wrote it felt like.
        """
        configured = (configured_workspace or "").upper()
        print_adt_table(
            [
                {
                    "workspace": workspace.workspace,
                    "workspace_id": workspace.workspace_id,
                    "owners": workspace.owners,
                    "apps": workspace.applications,
                    "developers": workspace.developers,
                    "active": "Y" if workspace.workspace.upper() == configured else "",
                }
                for workspace in workspaces
            ]
        )

    def workspace_not_found(self, workspace: str, source: str) -> None:
        """Someone named a workspace this instance does not have (`#564`).

        Before this the screen simply came back empty at exit `0`, which is what
        Jan hit on 2026-08-26 with a schema name sitting in `apex.workspace`,
        and read as ADT failing to identify a workspace it had just listed
        applications for.
        """
        print_adt_header(WORKSPACE_NOT_FOUND_HEADER)
        print(f"  {workspace} ({source}) matches no workspace on this instance")
        print()

    def owner_counts(self, owner_counts: list[ApexOwnerCount]) -> None:
        if not owner_counts:
            return
        print_adt_header("APPLICATIONS PER LISTED OWNERS:")
        print_adt_table(
            [
                {
                    "owner": owner_count.owner,
                    "workspace": owner_count.workspace,
                    "apps": owner_count.applications,
                }
                for owner_count in owner_counts
            ]
        )

    def applications(self, schema: str, applications: list[ApexApplication]) -> None:
        if not applications:
            return
        workspace = applications[0].workspace
        print_adt_header("APEX APPLICATIONS:", f"{workspace} | {schema_label(schema)}")
        print_adt_table(
            [
                {
                    "app_id": application.app_id,
                    "name": _truncate_console_value(application.app_name, 40),
                    "pages": application.pages,
                    "updated_at": application.updated_at,
                }
                for application in applications
            ],
            min_widths={"name": 40},
        )


def _truncate_console_value(value: object, width: int) -> str:
    text = str(value or "")
    if len(text) <= width:
        return text
    return f"{text[:width - 2]}.."


class ConsoleExportDataReporter:
    def __init__(self, silent: bool = False) -> None:
        self._silent = silent
        self._progress = FixedWidthProgressPrinter()

    def start_export(self, total: int) -> None:
        # Parity with export_db's EXPORTING <n> OBJECTS: (ADT #237), the same
        # "starting a run of N things" moment, so the same wording.
        print_adt_header(f"EXPORTING {total} TABLES:")

    def export_table(self, table: DataTable) -> None:
        if self._silent:
            return
        self._progress.begin(table.name.upper())

    def finish_table(self, table: DataTable, row_count: int) -> None:
        if self._silent:
            return
        self._progress.finish(table.name.upper(), row_count)

    def finish_export(self) -> None:
        return None


__all__ = [name for name in globals() if not name.startswith("__")]
