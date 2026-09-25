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


class PatchValidateReporter(ConsoleValidateReporter):
    """`validate`'s rows under `validate`'s header, opened by the first tree."""

    def __init__(self, *, debug: bool = False, live: bool | None = None) -> None:
        super().__init__(debug=debug, live=live)
        self.opened = False
        # `-create` holds the warning until its build returns (`flush_issues`):
        # the build reads the target's APEX checksum right after compiling, and
        # the blank above the warning's pointer line closes the section that
        # read would otherwise run under (`tests/conftest.py::
        # no_silent_blocking_phase`). A deploy on its own prints it at once.
        self.hold_issues = False
        self._held: list[FolderOutcome] = []

    def _open(self) -> None:
        if not self.opened:
            print_adt_header(PATCH_VALIDATING_HEADER)
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

        No trailing blank of its own: `patch` always renders another section
        right after this one, and `print_adt_header` normalizes the gap above
        whatever that is (`shared/progress.print_adt_header`) regardless of what
        this leaves on the stream. Printing one anyway would additionally close
        this section's on-screen announcement two lines early, ahead of the live
        signature-drift read `build_database_patch` still has to make before the
        next header -- a silent database call the console guard exists to catch
        (`tests/conftest.py::no_silent_blocking_phase`).
        """
        if not folders or any(outcome.report.failed for outcome in folders):
            return
        if self.hold_issues:
            self._held.extend(folders)
            return
        print_apexlang_issues(folders)

    def hold(self) -> None:
        """Keep the warning back until `flush_issues`: a `-create` build is running."""
        self.hold_issues = True

    def flush_issues(self) -> None:
        """Print what `close` held, and stop holding: the `-create` build is done."""
        held, self._held = self._held, []
        self.hold_issues = False
        print_apexlang_issues(held)


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
    "PatchValidateReporter",
    "print_validation_failure",
]
