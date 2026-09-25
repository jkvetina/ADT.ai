from __future__ import annotations

import argparse
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    ConfigError,
    ConfigLoader,
    GatewayFactory,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import _config_search_paths, _repo_root
from adt_ai.cli.context_apex import _flatten_arg_groups
from adt_ai.cli.validate_scan import _scan_applications
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.apex_payloads import drop_legacy_staging, link_payloads
from adt_ai.shared.apexlang_line_endings import (
    PrecheckIssue,
    convert_crlf,
    crlf_issue,
    print_precheck_issues,
)
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.error_screen import print_adt_error
from adt_ai.shared.file_list import row as list_row
from adt_ai.shared.progress import FixedWidthProgressPrinter, commit_line, format_seconds
from adt_ai.shared.streamed_table import ERASE_TO_END_OF_LINE
from adt_ai.validate.files import ValidateTarget, discovery_label, resolve_targets
from adt_ai.validate.report import EMPTY, UNRECOGNISED, CompileMessage, message_lines
from adt_ai.validate.runner import (
    FolderOutcome,
    ValidateReporter,
    ValidateRequest,
    ValidateRunner,
)

# A refusal names what to go and fix, like every other ADT.ai failure screen
# (`docs/console.md` §Failure screens). Jan chose this shape for both cases over a
# row inside `VALIDATING:` and over a `NOTES:` line, 2026-09-10, because neither
# reads as the refusal it is: "This is NOT compatible with other modules error
# handling."
#
# These were their own dashed section headers until ADT #764 folded both into
# `ERROR - INPUT NOT FOUND:`, which is the same answer to the reader in both
# cases: what you pointed me at is not there. They are lead LINES now rather than
# headers, so the wording that separates the two survives inside one screen; the
# `_HEADER` suffix goes with them, because the console inventory holds the
# family's twelve codes and a lead line is body text rather than furniture.
# Jan, 2026-09-25 (ADT #966): *"Also change "VALIDATING:" to "VALIDATING APPS:""*.
VALIDATING_HEADER = "VALIDATING APPS:"

INPUT_NOT_FOUND_LEAD     = "INPUTS NOT ON DISK"
NOTHING_TO_VALIDATE_LEAD = "NOTHING TO VALIDATE"


class ConsoleValidateReporter(ValidateReporter):
    """Streams one row per folder: label before the compile, result after.

    Under ``-debug`` the generated script prints first, as a block of its own in
    the shape ``DebugQueryGateway`` gives a ``QUERY:``, and the row follows it
    whole. Echoed from inside the SQLcl call, it landed after the row's label
    and before its verdict, which split one row across the whole block.

    **Ticks once a second on a terminal** (ADT #967). Jan, on the row sitting
    still for the whole ~4.5s SQLcl call: *"You were supose to show the times as
    you go, not when you are done!"* `begin()` used to print only the label and
    leave the clock for `finish()`; now a live run paints the full row with
    `0:00:00` immediately and a daemon thread repaints it every second until the
    real verdict lands. Follows `ConsoleDeployReporter`'s ticker exactly (ADT
    #670): the thread gets its OWN `threading.Event` rather than reading
    `self._stop`, so `_stop_ticker` clearing that attribute from the main thread
    cannot be observed mid-loop, and a `_paint_lock` serializes a tick against
    the closing row so neither can land half-written or overwrite the other.

    **The live render is chosen by `isatty`, never by a flag**, the same
    convention as `StreamedTable`: a redirected run, CI and pytest's `capsys`
    print exactly the one line per row this reporter always has, byte-identical
    to before this ticked -- the label, then ` .... 0:00:05\\n`.
    """

    #: How often the open row repaints while a compile is blocking. A second is
    #: what the eye reads as "alive" (`ConsoleDeployReporter.TICK_SECONDS`).
    TICK_SECONDS = 1.0

    def __init__(self, *, debug: bool = False, live: bool | None = None) -> None:
        self.printer = FixedWidthProgressPrinter()
        self.debug = debug
        self._started = 0.0
        self.live = sys.stdout.isatty() if live is None else live
        self._label: str | None = None
        self._row_open = False
        # Paints arrive from `begin`/`finish` and from the ticker thread, so the
        # two are serialized the same way `StreamedTable` serializes its own
        # (ADT #670): a half-written row interleaved with another is unreadable.
        self._paint_lock = threading.Lock()
        self._stop: threading.Event | None = None
        self._expected = 0.0

    def expect(self, seconds: float) -> None:
        """What the next compile cost last time, so its open row counts down.

        ADT #973. Jan: *"you should store validation timer together with other
        timers, so you can do countdown (on all places where you are running
        this)"*. The open row shows what is left of the stored time, the way an
        export row's clock does, and the closed row the real elapsed time. With
        no history there is nothing to count down from, and the clock counts up.
        """
        self._expected = seconds

    def request(self, script: str) -> None:
        if not self.debug:
            return
        print()
        print("SQLCL REQUEST:")
        print(script)
        print()

    def begin(self, label: str) -> None:
        self._started = time.monotonic()
        self.printer.begin(label)
        if not self.live:
            return
        self._label = label
        self._row_open = True
        self._repaint(self._clock(0.0))
        stop = threading.Event()
        self._stop = stop
        threading.Thread(target=self._tick, args=(stop,), daemon=True).start()

    def finish(self, label: str, status: str) -> None:
        # The compile time, never the verdict (ADT #966). Jan, on the bare error
        # count this cell used to carry: *"No, show timer instead"*. The sections
        # below the rows say what failed; the clock is the progress bars' own.
        elapsed = int(time.monotonic() - self._started + 0.5)
        clock = format_seconds(elapsed).strip()
        self._stop_ticker()
        if not self.live:
            self.printer.status(label, clock)
            return
        with self._paint_lock:
            self._row_open = False
            print(
                "\r" + self.printer.row_text(label, clock) + ERASE_TO_END_OF_LINE,
                flush=True,
            )
        commit_line()
        self._label = None

    def note(self, message: str) -> None:
        print(f"  {message}")

    def _tick(self, stop: threading.Event) -> None:
        """Repaint the open row once a second until its own event is set.

        ``stop`` is a parameter, not `self._stop` (ADT #670): the loop owns the
        event it waits on for as long as it runs, so `_stop_ticker` clearing the
        attribute from the main thread cannot be read out from under it.
        """
        while not stop.wait(self.TICK_SECONDS):
            if not self._row_open:
                return
            self._repaint(self._clock(time.monotonic() - self._started))

    def _clock(self, elapsed: float) -> int:
        """The open row's seconds: left of the estimate, else elapsed so far."""
        if self._expected > 0:
            return max(0, int(self._expected - elapsed + 0.5))
        return int(elapsed + 0.5)

    def _repaint(self, seconds: int) -> None:
        """Redraw the open row in place. A no-op unless a row is genuinely open.

        `_row_open` is read INSIDE the lock because `finish` clears it inside the
        same one: a tick already past its own wait would otherwise repaint over
        a row that has just printed its real verdict (ADT #670).
        """
        with self._paint_lock:
            # `_row_open` is only ever True with a real label set beside it
            # (`begin`); the `None` check is for mypy's narrowing, not a case
            # that happens.
            if not self._row_open or self._label is None:
                return
            clock = format_seconds(seconds).strip()
            print(
                "\r" + self.printer.row_text(self._label, clock) + ERASE_TO_END_OF_LINE,
                end  = "",
                flush= True,
            )

    def _stop_ticker(self) -> None:
        if self._stop is not None:
            self._stop.set()
            self._stop = None


# What `export_apex -apexlang` and `patch` say about a compile that found
# anything (ADT #971). Jan: *"This should be presented as a warning and not
# NOTES. And I want the same NOTES change in PATCH module."* The count per
# application and one pointer for all of them, spelled as Jan drew it: the `#`
# is the reader's own application id, which the rows above already list.
APEXLANG_ISSUES_HEADER = "WARNING - APEXLANG ISSUES:"
APEXLANG_ISSUES_POINTER = "  1) run `adtai validate -app #` for more details"


def _counted(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def print_apexlang_issues(folders: tuple[FolderOutcome, ...] | list[FolderOutcome]) -> None:
    """`WARNING - APEXLANG ISSUES:`, one row per tree with errors or warnings.

    Silent when every tree compiled clean, so a clean run grows no output. No
    trailing blank of its own: both callers render another section right after
    this one, and `print_adt_header` normalizes the gap above it (the reason
    `PatchValidateReporter.close` gives).
    """
    rows = []
    for folder in folders:
        report = folder.report
        counts = [
            _counted(len(messages), noun)
            for messages, noun in ((report.errors, "error"), (report.warnings, "warning"))
            if messages
        ]
        if counts:
            rows.append(f"  {folder.target.label}: {', '.join(counts)}")
    if not rows:
        return
    print_adt_header(APEXLANG_ISSUES_HEADER)
    for row in rows:
        print(row)
    print()
    print(APEXLANG_ISSUES_POINTER)


def _run_validate(
    args: argparse.Namespace,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    print_module_banner("VALIDATE")
    root = Path(args.root).expanduser().resolve()
    if args.scan:
        # The one mode that connects: it asks the running application what the
        # exported files cannot answer (`#30`), see `cli/validate_scan.py`.
        return _scan_applications(args, root, gateway_factory)
    inputs = _flatten_arg_groups(args.input)
    app_ids = _flatten_arg_groups(args.app)
    config = _optional_config(args, root, inputs, app_ids)

    targets, missing_trees = resolve_targets(root, config, inputs=inputs, app_ids=app_ids)
    notes: list[str | PrecheckIssue] = [*missing_trees]
    refusal = _refusal(targets, root, config, inputs, app_ids)
    if refusal is not None:
        _print_refusal(*refusal, debug_available=hasattr(args, "debug"))
        return 1
    drop_legacy_staging(root)
    notes.extend(_link_payloads(targets, ApexFileResolver.from_config(root, config)))
    notes.extend(_convert_crlf(targets))
    reporter = ConsoleValidateReporter(debug=args.debug)

    if targets:
        print_adt_header(VALIDATING_HEADER)
    # The module global, read at call time, so the CLI facade's patch sync can
    # swap SQLcl out wholesale in tests.
    result = ValidateRunner(sqlcl_request=run_sqlcl_script).run(
        ValidateRequest(
            targets      = tuple(targets),
            root         = root,
            project_root = root,
            reporter     = reporter,
        )
    )

    for folder in result.folders:
        if folder.report.warnings:
            # Always printed: FILE_IGNORED means the compiler did not check that
            # file at all, which a bare "OK" would hide behind a clean pass.
            _print_messages(f"WARNINGS IN {folder.target.label}:", folder.report.warnings)
        if folder.report.errors:
            _print_messages(f"ERRORS IN {folder.target.label}:", folder.report.errors)
        elif folder.report.outcome == UNRECOGNISED:
            # Never swallow output the parser could not read: show it verbatim so
            # the user can see what SQLcl actually said.
            print_adt_header(f"WARNING - UNRECOGNISED OUTPUT {folder.target.label}:")
            print(folder.report.raw.rstrip("\n"))
            print()
        elif folder.report.outcome == EMPTY:
            # The row carries the clock since ADT #966, so the verdict it used
            # to spell is said here: a broken export, never a quiet pass.
            notes.append(f"{folder.target.label}: no APEXlang files, export it again")

    # A precheck issue is a warning and still fails the run: the committed tree
    # is the broken one until the conversion is committed (ADT #928, #934).
    plain_notes = print_precheck_issues(notes)
    if plain_notes:
        print_adt_header("NOTES:")
        for note in plain_notes:
            reporter.note(note)
        print()

    return 0 if not result.failed and not notes else 1


def _link_payloads(targets: list[ValidateTarget], resolver: ApexFileResolver) -> list[str]:
    """Complete every project tree in place, so the compiler reads the real folder.

    `apexlang/` alone cannot validate on any app that owns static files: the export
    omits the payloads by design and `static-files.apx` references every one of
    them. ADT #165 answered that by assembling a second tree under
    `config/temp/apexlang/` and compiling the copy; ADT #765 replaced it with
    hardlinks into the export's own `shared-components/static-files/`, which is
    gitignored by a file the folder carries itself.

    So there is nothing to rewrite on the target any more. The progress row, every
    message section and the folder the compiler opens are all the one path the user
    asked about, and a tree that was correct on disk cannot be made wrong by what
    another target staged (`shared/apex_payloads.py` carries the full argument).

    The payloads are read from the folder `export_apex -files` writes them to,
    `apex_path_files` under the application's folder, which is the tree's parent.
    A hard-coded `files/` made every payload a `REFERENCE_NOT_FOUND` in a
    project that sets the key (ADT #923).
    """
    notes: list[str] = []
    for target in targets:
        if not target.stageable:
            continue
        links = link_payloads(target.path, resolver.files_root(target.path.parent))
        if links.linked == 0 and _references_payloads(target.path):
            # The compiler will report one REFERENCE_NOT_FOUND per payload, which
            # says what is missing but not how to get it. This says how.
            notes.append(
                f"{target.label}: static files are referenced but none are exported "
                f"- run `adtai export_apex -app {target.app_id or ''} -files`.".replace(
                    "  ", " "
                )
            )
    return notes


def _convert_crlf(targets: list[ValidateTarget]) -> list[PrecheckIssue]:
    """Hand the compiler LF, one warning per tree that had to be converted (ADT #928).

    SQLcl's APEXlang compiler cannot read CRLF, and on a whole application it
    crashed rather than reported. The same conversion a `patch -deploy` import
    runs, so the check and the import read the same bytes; the note keeps the
    run failing until the conversion is committed, because the committed tree is
    still the broken one.
    """
    issues: list[PrecheckIssue] = []
    for target in targets:
        if not target.stageable:
            continue
        if converted := convert_crlf(target.path):
            issues.append(crlf_issue(target.label, converted))
    return issues


def _refusal(
    targets : list[ValidateTarget],
    root    : Path,
    config  : Mapping[str, Any],
    inputs  : list[str] | None,
    app_ids : list[str] | None,
) -> tuple[str, list[str], str] | None:
    """The header, the subject rows and the remedy, when the run cannot start.

    Two cases, and neither is a result: a path that is not on disk, and a bare run
    with no export anywhere under the configured root. Both were reported as
    content before ADT #765, the first as a `NOT_FOUND` row inside `VALIDATING:`
    and the second as a `NOTES:` line, which put a refusal where the reader looks
    for findings.

    **Decided here rather than by SQLcl.** The old row spent a JVM start to be told
    a folder is absent, which this process can see for itself; the timer for that
    story read `TIMER: 2s` for an answer available in no time at all.
    """
    missing = [target.label for target in targets if not target.path.exists()]
    if missing:
        return (
            INPUT_NOT_FOUND_LEAD,
            missing,
            "Check the path, or run `adtai export_apex -apexlang` to create one.",
        )
    if not targets and not inputs and not app_ids:
        return (
            NOTHING_TO_VALIDATE_LEAD,
            [f"No {APEXLANG_DIR}/ folder under {discovery_label(root, config)}"],
            "Run `adtai export_apex -apexlang` first.",
        )
    return None


def _print_refusal(
    lead   : str,
    rows   : list[str],
    remedy : str,
    *,
    debug_available : bool,
) -> None:
    """One failure screen, through the shared renderer.

    A refusal is chrome rather than a result, so nothing may suppress it: a run
    that refused and said nothing would exit 1 with a banner and a timer and no
    reason at all.

    This drew its own screen until ADT #764, which is why it had the indent, the
    blank line and the unconditional `-debug` hint written out by hand and was
    the ninth shape a sweep of eight found. `lead` carries the wording that
    separates the two cases; both take the same code, because "the folder you
    named is not on disk" and "there is nothing here to validate" are one answer
    to the reader: what you pointed me at is not there.

    The lead is a short uppercase headline, and the rows sit one blank line
    under it (ADT #934).
    """
    print_adt_error(
        "INPUT NOT FOUND",
        [lead, "", *(list_row(text) for text in rows)],
        remedy,
        debug_available=debug_available,
    )


def _references_payloads(apexlang_root: Path) -> bool:
    """Does this export name static-file payloads it does not carry?"""
    return (apexlang_root / "shared-components" / "static-files.apx").is_file()


def _print_messages(header: str, messages: tuple[CompileMessage, ...]) -> None:
    """One section per folder, in the blank-content-blank shape of a table section.

    ``print_adt_table`` opens with a blank line and closes with one; the stanza
    list keeps that spacing so a `WARNINGS:` section sitting above an `ERRORS:`
    section reads the same as any other pair of sections in the tool.
    """
    print_adt_header(header)
    print()
    for line in message_lines(messages):
        print(line)
    print()


def _optional_config(
    args    : argparse.Namespace,
    root    : Path,
    inputs  : list[str] | None,
    app_ids : list[str] | None,
) -> dict[str, object]:
    """Load ``config.yaml`` only when path resolution actually needs it.

    A pure ``-input`` run must work from any folder, that is what makes the
    command usable in CI and from a checkout with no ADT project around it, so
    a missing config is not an error there. ``-app`` and bare discovery do read
    ``path_apex``, and a missing config there simply means the defaults.
    """
    if inputs and not app_ids:
        return {}
    try:
        return ConfigLoader(_config_search_paths(args.config_dir, root, _repo_root())).load().data
    except ConfigError:
        return {}


__all__ = [name for name in globals() if not name.startswith("__")]
