"""What `patch` prints while it compiles the APEXlang tree, and when it refuses (ADT #964).

Two pieces, one function each, so either can be reshaped without touching the
other or the gate in `patch/apex_validate.py` that drives them.

**Progress is `validate`'s own.** `adtai validate` prints one streamed row per
tree, the label before the ~4.5s SQLcl call and its clock after it, and `patch`
prints exactly that, through the same reporter. Two differences. The header is
`patch`'s own, `VALIDATING APEXLANG APPS:` (ADT #971, Jan: *"In patch module
rename "VALIDATING APPS:" to "VALIDATING APEXLANG APPS:""*), because among the
patch's other sections the bare word does not say what is compiled. And it
goes out later: `validate` knows it has trees to compile before it starts, and
`patch` only learns it inside the build or the deploy, so the header opens with
the first row and a run with nothing to compile prints nothing at all.

**The refusal is the failed import's block, moved in front of it.** Jan's deploy
reported the compiler's errors under `ERROR - DEPLOYMENT FAILED:` as `APP:` and
the lines under it; the refusal carries the same lines from the same
`import_error_lines`, two columns in, under a header of the same family, before
anything connected.
"""

from __future__ import annotations

from adt_ai.cli.commands_validate import ConsoleValidateReporter, print_apexlang_issues
from adt_ai.patch.apex_validate import ApexlangValidationError
from adt_ai.shared.progress import print_adt_header
from adt_ai.validate.report import import_error_lines
from adt_ai.validate.runner import FolderOutcome

PATCH_VALIDATING_HEADER = "VALIDATING APEXLANG APPS:"
#: `-deploy`'s post-deploy scan (ADT #676), on these same rows since ADT #988.
PATCH_VERIFYING_HEADER = "VERIFYING APPLICATIONS:"


class PatchValidateReporter(ConsoleValidateReporter):
    """`validate`'s rows under `validate`'s header, opened by the first tree.

    ``header`` is the one other section that streams these rows: `-deploy`'s
    application scan (ADT #988, Jan: *"the outcome should be same as other
    apexlang validation"*), under `PATCH_VERIFYING_HEADER`.
    """

    def __init__(
        self,
        *,
        debug: bool = False,
        live: bool | None = None,
        header: str = PATCH_VALIDATING_HEADER,
    ) -> None:
        super().__init__(debug=debug, live=live)
        self.opened = False
        self.header = header

    def _open(self) -> None:
        if not self.opened:
            print_adt_header(self.header)
            self.opened = True

    def request(self, script: str) -> None:
        self._open()
        super().request(script)

    def begin(self, label: str) -> None:
        self._open()
        super().begin(label)

    def close(self, folders: tuple[FolderOutcome, ...]) -> None:
        """`WARNING - APEXLANG ISSUES:`, one row per app carrying a warning (ADT #967, #971).

        Before ADT #966 the row itself said `OK (221 warnings)`; #966 replaced
        that with the clock and `patch` stopped saying anything about warnings at
        all. Jan: *"Where did the warning dissapeared?"*, approved as "Count +
        note to use adtai validate to see the list": `adtai validate` already
        prints the compiler's own `WARNINGS IN <label>:` section in full, so
        `patch` only owes the count and where to read it. ADT #967 printed it as
        a `NOTES:` line; Jan, ADT #971: *"This should be presented as a warning
        and not NOTES. And I want the same NOTES change in PATCH module."*, so
        it is `export_apex`'s own warning, from the same renderer.

        Silent on a run this call cannot itself see failed folders in: a failing
        tree raises `ApexlangValidationError` right after `validate_trees` reads
        this same result, and `ERROR - VALIDATION FAILED:` is the one screen that
        follows a refusal, never a warning beside it. That also leaves nothing
        but warnings to count here.

        Prints the moment validation closes, on `-create` as much as on
        `-deploy` (ADT #988). Between ADT #971 and #988 `-create` held this
        back with `hold()`/`flush_issues()` until `create_database_patch`
        returned, because the build read the live APEX checksum right after
        the rows closed and only the still-announcing header covered that
        read -- a loophole #988 closed in the guard. The read now runs inside
        each app's own row, before it closes (`patch/apex_drift.py::
        DriftReads`), so nothing is left for this section to wait on.

        No trailing blank of its own: `patch` always renders another section
        right after this one, and `print_adt_header` normalizes the gap above
        whatever that is (`shared/progress.print_adt_header`).
        """
        if not folders or any(outcome.report.failed for outcome in folders):
            return
        print_apexlang_issues(folders)


def print_validation_failure(error: ApexlangValidationError) -> None:
    """`ERROR - VALIDATION FAILED:`, one `APP:` block per refused tree, the way out.

    Named by the application the tree belongs to, never the id a retarget would
    land it on, because that is the id `adtai validate -app` takes.
    """
    print_adt_header("ERROR - VALIDATION FAILED:")
    for index, outcome in enumerate(error.failed):
        if index:
            print()
        print(f"  APP: {outcome.target.label}")
        # Two columns in rather than four (ADT #966, Jan: *"nested by 2 more
        # spaces then they should"*): the locator sits under `APP:`'s value.
        for line in import_error_lines(outcome.report):
            print(f"  {line}".rstrip())
    print()


__all__ = [
    "PATCH_VALIDATING_HEADER",
    "PATCH_VERIFYING_HEADER",
    "PatchValidateReporter",
    "print_validation_failure",
]
