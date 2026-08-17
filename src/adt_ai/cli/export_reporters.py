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

    def workspaces(self, workspaces: list[ApexWorkspace]) -> None:
        print_adt_table(
            [
                {
                    "workspace": workspace.workspace,
                    "workspace_id": workspace.workspace_id,
                    "owners": workspace.owners,
                    "applications": workspace.applications,
                    "developers": workspace.developers,
                }
                for workspace in workspaces
            ]
        )

    def owner_counts(self, owner_counts: list[ApexOwnerCount]) -> None:
        if not owner_counts:
            return
        print_adt_header("APPLICATIONS PER LISTED OWNERS:")
        print_adt_table(
            [
                {
                    "owner": owner_count.owner,
                    "applications": owner_count.applications,
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
