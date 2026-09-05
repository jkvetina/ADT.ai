"""The live `DEPLOYING PATCH:` reporter: one open row, repainted as it goes.

Split out of ``patch_deploy_render`` when that module crossed the 20 KB context
guard (ADT #434), which is the same seam it was itself created on and the third
time this one console has been cut: `#269` moved `patch` out of `commands_patch`,
`#273` moved the rendering out of `commands_patch_deploy`, and this moves the one
STATEFUL thing out of the rendering. What stays behind is pure functions over a
finished result; what lives here holds a row open, owns a thread, and asks the
terminal what it is. A module that crosses the guard is split, never registered
as debt.

**The drawing itself moved out under ADT #678.** `patch -drop` needed the same
two-half streamed row, and `one-renderer-per-console-shape` says a shape reaching
the screen from a second module gets one renderer rather than a copy. So the
seam, the `\\r` repaint, the erase suffix and the paint lock now live in
`shared/streamed_table.py`, and what is left here is the deploy-specific half:
the ticker, the files counter, and the plan a row is sized from.
"""

from __future__ import annotations

import threading
import time

from adt_ai.cli.constants import print_adt_header
from adt_ai.cli.patch_deploy_layout import (
    DEPLOY_COLUMNS,
    DEPLOY_STATUS_RUNNING,
    _deployment_layout,
    _deployment_row_values,
)
from adt_ai.patch.models import DeploymentPlanItem, DeploymentResult
from adt_ai.shared.streamed_table import ERASE_TO_END_OF_LINE, StreamedTable

# Where the streamed row breaks: everything left of this is knowable BEFORE the
# script runs, everything from here on is its result. A named index rather than a
# literal in two call sites, because it moved with the column order in ADT #444
# and the two halves have to keep meeting at the same seam or they stop rejoining
# byte-for-byte with the batch render.
STREAM_SPLIT = DEPLOY_COLUMNS.index("FILES")

# `ERASE_TO_END_OF_LINE` was defined in this module between `#444` and `#678`, and
# the tests that pin the erase in both directions import it from here. Re-exported
# rather than moved out from under them: it is the renderer's constant now, and a
# rename nobody asked for is not part of extracting a shared renderer.
__all__ = [
    "ConsoleDeployReporter",
    "ERASE_TO_END_OF_LINE",
    "STREAM_SPLIT",
]


class ConsoleDeployReporter:
    """Streams `DEPLOYING PATCH:` so each script's hang attaches to its own row.

    ADT #273. Before this the CLI ran the entire deploy and printed the finished
    table afterwards, so Jan's 42-file run sat on `CONNECTING TO SCHEMA
    APP_OWNER, DEV:` for its whole duration with nothing on screen to say it was
    alive, the exact anti-pattern SOP §Console output contract names ("Never
    build the whole row and print it after the work"). Old ADT had it right: size
    the columns off the plan, print the header before the first script
    (`patch.py:508-521`), then one row per script as it finishes (`patch.py:592`).

    `begin_script` prints `FILE | SCHEMA`, everything knowable before the work,
    and stops mid-line; `end_script` completes that same line with
    `FILES | TIMER | STATUS`. The seam between them is `STREAM_SPLIT`, which
    moved with the column order in ADT #444. `streamed` lets the CLI fall back to
    the batch render when the loop never ran, so the SKIPPED path is unchanged.

    On a terminal it repaints that open row instead of waiting (ADT #434). One
    script per patch is the ordinary shape, so `#273`'s row-per-script streaming
    left Jan's deploy showing a single half-written line for its whole duration:
    "You are not printing the files until you are fully done ... Idea is that you
    could reprint the line as you go to show you are doing something and how far
    you are." `advance` moves the FILES cell as `run_sqlcl_script` hands over each
    echoed marker, and a one-second ticker moves TIMER in between, because a
    single long statement prints nothing at all while it runs and a frozen
    counter is the thing being complained about.

    **The cell counts files FINISHED, and the row opens on `0/n` rather than on
    nothing** (ADT #441). Both halves of that were wrong in `#434`, and both were
    already visible in its own recorded evidence, `0s`, `1s`, `2s`, `3s`, then
    `1/6 3s` ... `6/6 15s`. The total is known before the first line arrives, so
    an empty cell beside a ticking timer measures nothing but the reader's
    patience. And a marker echoes when its file is about to RUN, the install
    script writing `PROMPT -- FILE:` above the `@` link rather than below it, so
    the raw marker count reads one file ahead of the work: on the two-file patch
    Jan deployed that put `2/2` on screen for the whole of the second file and
    left `1/2` alive only as long as the first one took (2026-08-21: "it showed
    files as 2/2 with a long timer. Where was 1/2? It was not visible at all.").

    **An open row says `IN PROGRESS`**, also Jan's, same day. It is the one
    status a running row can carry honestly, and it costs the STATUS column four
    characters on every deploy table, live or not, because the live paint and the
    row that replaces it must draw the same geometry.

    **The live render is chosen by `isatty`, never by a flag**, so a redirected
    run, a CI job and a piped `adtai` keep printing exactly one line per script.
    A log carrying every intermediate paint is worse than the batch render it
    replaced. That decision sits in `StreamedTable` since `#678`, which is what
    stops this command and `patch -drop` answering it two ways.
    """

    # How often TIMER is redrawn while a statement is blocking. A second is what
    # the eye reads as "alive" and is slow enough that a two-hour deploy costs a
    # few thousand short writes.
    TICK_SECONDS = 1.0

    def __init__(self, live: bool | None = None, folder: str = "") -> None:
        # The resolved patch folder, appended to the section header (ADT #443).
        # `RELEVANT COMMITS:` used to carry it, and shortening that header left
        # `-deploy` with nothing on screen naming which folder a ref resolved to
        # (`DeploymentPlanItem.file` is `sql_path.name`, just `APP_OWNER.sql`),
        # which is the guard `#255` put there: a prefix match onto the wrong patch
        # has to be visible in the run. It rides an existing header rather than a
        # new line, so the console gains no string.
        self.folder = folder
        self.streamed = False
        self._live = live
        self._table: StreamedTable | None = None
        self._item: DeploymentPlanItem | None = None
        # Files FINISHED, never markers echoed: `advance` converts one to the
        # other, so nothing downstream of it has to remember the difference.
        self._finished: int | None = None
        self._total: int | None = None
        self._started: float | None = None
        self._stop: threading.Event | None = None

    def _open_table(self) -> StreamedTable:
        """The table `begin_deploy` opened, for a row of it.

        A row cannot be drawn before the table it belongs to is open, and the
        runner calls `begin_deploy` before the first script for exactly that
        reason. Named here rather than assumed, so a caller driving the reporter
        out of order says so instead of raising on `None` two frames down.
        """
        if self._table is None:  # pragma: no cover, ordering is the runner's
            raise RuntimeError("begin_deploy must open the table before a row is drawn")
        return self._table

    def begin_deploy(self, plan: list[DeploymentPlanItem]) -> None:
        self.streamed = True
        self._table = StreamedTable(
            _deployment_layout([], plan), split = STREAM_SPLIT, live = self._live
        )
        # Mirror print_adt_table's opening exactly: the section title, then a
        # leading blank, the column header, and the separator.
        print_adt_header("DEPLOYING PATCH:", self.folder)
        self._table.open_table()

    def begin_script(self, item: DeploymentPlanItem) -> None:
        table = self._open_table()
        if not table.live:
            table.begin_row([item.file, item.schema, "", "", ""])
            return
        self._item = item
        # `0`, not `None`: the plan already knows how many files this script
        # links (`item.files` is the same `_countable_file_total` the runner
        # recomputes as `deployed_total`), so the row can say `0/n` from its
        # first paint instead of showing a timer beside an empty cell for the
        # whole of SQLcl's startup, the connect and the script's preamble.
        self._finished = 0
        self._total = getattr(item, "files", None)
        self._started = time.monotonic()
        table.begin_row(self._paint_values())
        # The thread is handed its OWN event rather than left to read
        # `self._stop` (ADT #670). `_stop_ticker` sets the event and then drops
        # the reference, both from the main thread, so a loop that re-read the
        # attribute could pass its `is not None` test and then find `None` on
        # the very next load. The `AttributeError` that follows is raised inside
        # a daemon thread, where nothing on screen reports it and the row it was
        # painting simply stops moving, which is the frozen counter `#434` was
        # filed on wearing a different hat.
        stop = threading.Event()
        self._stop = stop
        threading.Thread(target=self._tick, args=(stop,), daemon=True).start()

    def advance(self, deployed: int, total: int) -> None:
        """One more countable file REACHED, repaint the open row.

        Optional on the reporter protocol, like every `#273` hook: the runner
        calls it only when it finds it, so a caller passing its own reporter
        never has to grow one.

        ``deployed`` counts markers echoed, and a marker echoes just before its
        file runs, so the file it names is the one starting rather than one that
        finished: the count of files behind us is one less (ADT #441). The
        subtraction lives here, at the single point where the two meanings meet,
        so the paint renders a number that already means what its cell says.

        It tolerates being called before the table is open, which `end_script`
        and `end_deploy` deliberately do not: those are the runner's own ordering
        and a caller getting them wrong should hear about it, while this hook can
        genuinely fire early from a reader that starts producing lines before the
        row does. A stray `advance` must not crash a deploy.
        """
        table = self._table
        if table is None or not table.live or not table.row_open:
            return
        self._finished = max(0, deployed - 1)
        self._total = total
        table.repaint(self._paint_values())

    def end_script(self, result: DeploymentResult) -> None:
        self._stop_ticker()
        # A `NOT RUN` row, where the run broke before this script started and no
        # leading segment was ever printed, owes its whole line. `end_row` is
        # where that is decided, because the renderer is what knows whether a row
        # is open, and deciding it here as well would be the second reader of one
        # fact.
        self._open_table().end_row(_deployment_row_values(result))

    def end_deploy(self, results: list[DeploymentResult]) -> None:
        self._stop_ticker()
        # The trailing blank print_adt_table emits, so the section closes the same
        # way whichever render produced it.
        self._open_table().close_table()

    def _tick(self, stop: threading.Event) -> None:
        """Repaint the open row once a tick, until its own event is set.

        ``stop`` is a parameter and not `self._stop` (ADT #670): the loop then
        owns the object it waits on for as long as it runs, and `_stop_ticker`
        clearing the attribute is no longer something this thread can observe
        halfway through an expression.

        The `row_open` read here is an optimisation, never the guard: `repaint`
        re-makes that decision under the renderer's own paint lock, which is what
        stops a tick already past its `wait()` from drawing `IN PROGRESS` over a
        row that has just printed its verdict.
        """
        table = self._open_table()
        while not stop.wait(self.TICK_SECONDS):
            if not table.row_open:
                return
            table.repaint(self._paint_values())

    def _stop_ticker(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None

    def _paint_values(self) -> list[object]:
        """The open row's cells: everything known, STATUS reading `IN PROGRESS`.

        STATUS never carries a verdict until the script returns. A row that
        filled it early would print `SUCCESS` beside a deploy that can still fail
        on its next statement, which is a worse lie than a slow counter, and
        `IN PROGRESS` is exactly the claim a running row can make.

        The FILES cell is blank only when the total is genuinely unknown, which
        no `DeploymentPlanItem` reaches today; `0/n` is what a run that has
        finished nothing yet prints.
        """
        item = self._item
        files = (
            ""
            if self._finished is None or self._total is None
            else f"{self._finished}/{self._total}"
        )
        elapsed = (
            ""
            if self._started is None
            else f"{int(time.monotonic() - self._started + 0.5)}s"
        )
        return [
            "" if item is None else item.file,
            "" if item is None else item.schema,
            files,
            elapsed,
            DEPLOY_STATUS_RUNNING,
        ]
