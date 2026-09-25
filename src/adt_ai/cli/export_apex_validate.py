"""`export_apex -apexlang` compiles what it just exported (ADT #967).

Jan: *"the apexlang validation should be done also after the export_apex,
since many of the apps are actually not importable as they are and creating
patch for each app is too much work."* Approved: "Report only, exit 0" -- this
never turns a successful export into a failing command, and it changes nothing
about `-reveal` or an export that never asked for `-apexlang`.

Split out of `commands_export_apex.py` to respect the 24 KB context-size guard
(`tests/contracts/test_context_file_size.py`); the seam is the same one
`patch_validate_render.py` sits on, one small module per caller of `validate`'s
runner.

**Reuses `validate`'s whole pipeline rather than a copy of it**: the same
tree-preparation helpers `commands_validate.py` runs before
`adtai validate` connects (`_link_payloads`, `_convert_crlf`), and the same
`ValidateRunner`/`ValidateRequest`. What differs is where the row sits and what
happens on a refusal: `adtai validate` refuses the whole command, and this
reports and moves on.

**A step of each application's own block, never a section of its own** (ADT
#971). Jan: *"Remove VALIDATING APPS section, and fold it as a step below app
files when apexlang is requested."* So the compile runs from the export
runner's `validate_apexlang` hook, right after that application's last format,
as a `VALIDATING APEXLANG` row drawn by the reporter that drew those formats:
a bar counting down from the compile's stored `apex.db` timer like every
export row above it (ADT #973), or under `-compact` one more slice of the
application's own bar row, budgeted from the same timer. What the compile
found is one `WARNING - APEXLANG ISSUES:` section per schema segment, the one
`patch` prints (Jan: *"This should be presented as a warning and not NOTES."*),
and the full messages stay `adtai validate`'s own.

`sqlcl_request` arrives as a parameter rather than a module-scope import of the
CLI facade's own patchable global: this module is owned by `export_apex`
(`tests/contracts/test_partial_release_imports.py`, ADT #895) and a release
without that command withholds it, so nothing bundled in every release --
`cli/__init__.py` included -- may name it at import time. `commands_export_apex.py`
already carries `run_sqlcl_script` as its own module global for exactly this
reason and hands it down.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from adt_ai.cli.commands_validate import (
    _convert_crlf,
    _link_payloads,
    print_apexlang_issues,
)
from adt_ai.export_apex.actions import ACTION_HEADERS, skipped_by_apex_release
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.progress import FALLBACK_TARGET_SECONDS
from adt_ai.shared.apexlang_line_endings import print_precheck_issues
from adt_ai.shared.progress import print_adt_header
from adt_ai.validate.files import resolve_targets
from adt_ai.validate.runner import (
    FolderOutcome,
    SqlclRequest,
    ValidateRequest,
    ValidateResult,
    ValidateRunner,
    compile_estimate,
)

if TYPE_CHECKING:  # pragma: no cover - `ApexRun` imports this module's caller
    from adt_ai.cli.commands_export_apex import ApexRun
    from adt_ai.export_apex.inventory import ApexApplication
    from adt_ai.export_apex.progress import ApexProgressReporter

#: The row's label inside `EXPORTING APP <id>/<alias>:`, as Jan drew it. One
#: string with the `-compact` budget's pair (`export_apex/actions.py`).
VALIDATE_ROW = ACTION_HEADERS["validate"]


class ExportValidation:
    """Compile every APEXlang application a schema segment exports, one at a time.

    Called once per application by the export runner (`validate_apexlang`),
    then `close()` once the segment has exported everything. No-op unless
    `-apexlang` was asked for and the instance carries the format
    (`skipped_by_apex_release`, the same gate the export itself already passed
    to write anything at all): an export that wrote nothing has nothing to
    compile, and an export of any other format asked for no validation. Each
    application is resolved through `validate`'s own offline lookup
    (`resolve_targets`), so the warning's rows are `<id>/<alias>`, exactly
    `patch`'s and `adtai validate`'s own; one whose tree does not exist (an
    export that skipped writing it) is silently left out rather than reported as
    a validate refusal.

    Never changes the export's own exit code: errors and warnings alike are
    counted under `WARNING - APEXLANG ISSUES:`. `-reveal` never reaches this
    class at all, it is a different mode with its own early return in
    `commands_export_apex.py`.
    """

    def __init__(
        self, run: ApexRun, versions: dict[str, str], *, sqlcl_request: SqlclRequest,
    ) -> None:
        self.enabled = bool(run.actions.get("apexlang")) and not skipped_by_apex_release(
            "apexlang", versions.get("APEX")
        )
        self.root = run.root
        self.config = dict(run.config)
        self.sqlcl_request = sqlcl_request
        self.folders: list[FolderOutcome] = []
        self.notes: list[Any] = []

    def __call__(self, application: ApexApplication, reporter: ApexProgressReporter) -> None:
        if not self.enabled:
            return
        targets, _missing = resolve_targets(
            self.root, self.config, app_ids=[str(application.app_id)]
        )
        targets = [target for target in targets if target.path.exists()]
        if not targets:
            return
        self.notes.extend(
            _link_payloads(targets, ApexFileResolver.from_config(self.root, self.config))
        )
        self.notes.extend(_convert_crlf(targets))
        request = ValidateRequest(
            targets      = tuple(targets),
            root         = self.root,
            project_root = self.root,
        )
        runner = ValidateRunner(sqlcl_request=self.sqlcl_request)
        results: list[ValidateResult] = []
        # Drawn like every export row above it, counting down from the stored
        # compile time (ADT #973). Jan: *"you should store validation timer
        # together with other timers, so you can do countdown"*. The runner
        # records the compile itself, as every other caller's compile is.
        reporter.run(
            VALIDATE_ROW,
            compile_estimate(self.root, application.app_id) or FALLBACK_TARGET_SECONDS,
            lambda: results.append(runner.run(request)),
            app_id=application.app_id,
        )
        result = results[0]
        self.folders.extend(result.folders)

    def close(self) -> None:
        """The segment's findings, once every application in it has compiled."""
        plain_notes = print_precheck_issues(self.notes)
        print_apexlang_issues(self.folders)
        if plain_notes:
            print_adt_header("NOTES:")
            for note in plain_notes:
                print(f"  {note}")
        self.folders = []
        self.notes = []


__all__ = [
    "VALIDATE_ROW",
    "ExportValidation",
]
