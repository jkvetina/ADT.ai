"""Drive SQLcl's APEXlang compiler over exported ``apexlang/`` folders.

Connectionless by measurement, not by hope: ``apex validate`` compiles inside
SQLcl and answers on a bare ``sql -S /nolog`` session (verified on SQLcl
26.1.2.132.1334, card `#163`), so the script this runner builds carries no
``connect`` line, needs no credentials, and works in CI and from any checkout.

One SQLcl call per folder rather than one batched session. A batch is measurably
cheaper, three folders cost about as much as one, since JVM startup dominates
the ~4.5s, but the whole batch is a single blocking call, which cannot stream a
per-folder progress row. The console contract makes label-first streaming
non-negotiable, so the per-folder call wins and the batching remains a measured
option if the cost ever bites.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.apex_store import ApexStore, apex_store_path
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.sqlcl_quoting import reject_unquotable
from adt_ai.validate.files import ValidateTarget
from adt_ai.validate.report import FolderReport, parse_validate_output

SqlclRequest = Callable[..., str]

#: The `apex.db` timer one compile of an application is recorded under, beside
#: `export_apex`'s per-format timers (ADT #973). Jan: *"you should store
#: validation timer together with other timers, so you can do countdown (on all
#: places where you are running this)"*. Every place compiles through this
#: runner, so this is the one place that reads and writes it.
VALIDATE_TIMER = "validate"


def compile_estimate(root: Path, app_id: int | None) -> float:
    """What compiling ``app_id`` cost last time, rolled; ``0.0`` with no history.

    Nothing to read for a tree that is no recorded application (`-input`), or a
    project with no `apex.db` yet, and the store is never created just to ask.
    """
    if app_id is None or not apex_store_path(root).is_file():
        return 0.0
    with ApexStore.load(root) as store:
        return float(store.timers().get(app_id, {}).get(VALIDATE_TIMER) or 0.0)


def _record_compile(root: Path, app_id: int | None, elapsed: float) -> None:
    if app_id is None or not apex_store_path(root).is_file():
        return
    with ApexStore.load(root) as store:
        store.roll_timer(app_id, VALIDATE_TIMER, elapsed)

VALIDATE_COMMAND = 'apex validate -input "{input}"'


class ValidateReporter:
    """No-op reporter; the console implementation lives in ``cli/``."""

    def request(self, script: str) -> None:
        """The script about to run, handed over before its row opens (``-debug``)."""

    def begin(self, label: str) -> None:
        pass

    def finish(self, label: str, status: str) -> None:
        pass

    def note(self, message: str) -> None:
        pass

    def close(self, folders: tuple[FolderOutcome, ...]) -> None:
        """Every folder outcome, once the run is done. No-op here (ADT #967).

        `adtai validate` already prints the compiler's own `WARNINGS IN <label>:`
        section for every folder that carried one, so its own reporter has
        nothing to add and leaves this alone. `PatchValidateReporter` overrides
        it to print the `NOTES:` count-and-pointer line Jan asked back after ADT
        #966 replaced `patch`'s row with the clock and dropped it: *"Where did
        the warning dissapeared?"*
        """


@dataclass(frozen=True)
class ValidateRequest:
    targets      : tuple[ValidateTarget, ...]
    root         : Path
    project_root : Path | None = None
    reporter     : ValidateReporter | None = None


@dataclass(frozen=True)
class FolderOutcome:
    target : ValidateTarget
    report : FolderReport


@dataclass(frozen=True)
class ValidateResult:
    folders : tuple[FolderOutcome, ...]

    @property
    def failed(self) -> bool:
        # No targets is a failure, not a pass: nothing was checked, so a green
        # gate would be a lie about work that never happened.
        if not self.folders:
            return True
        return any(folder.report.failed for folder in self.folders)

    @property
    def error_count(self) -> int:
        return sum(1 for folder in self.folders if folder.report.failed)


class ValidateRunner:
    def __init__(self, sqlcl_request: SqlclRequest | None = None) -> None:
        self.sqlcl_request = sqlcl_request or run_sqlcl_script

    def run(self, request: ValidateRequest) -> ValidateResult:
        reporter = request.reporter or ValidateReporter()
        outcomes: list[FolderOutcome] = []
        for target in request.targets:
            # The script is built and handed to the reporter while no row is
            # open, so a `-debug` echo of it is a block of its own above the row
            # rather than text wedged between a label and its verdict.
            try:
                script = _build_script(target)
            except Exception:
                reporter.begin(target.label)
                reporter.finish(target.label, "FAILED")
                raise
            reporter.request(script)
            store_root = request.project_root or request.root
            # Optional, like `close` below: a duck-typed reporter without it
            # still runs, it just has nothing to count down from.
            expect = getattr(reporter, "expect", None)
            if expect is not None:
                expect(compile_estimate(store_root, target.app_id))
            reporter.begin(target.label)
            started = time.monotonic()
            try:
                output = self.sqlcl_request(
                    script,
                    request.root,
                    project_root=request.project_root,
                )
            except Exception:
                # Complete the row before the error reaches the shared banner, so
                # the visible failure sits on the folder being worked.
                reporter.finish(target.label, "FAILED")
                raise
            _record_compile(store_root, target.app_id, time.monotonic() - started)
            report = parse_validate_output(output)
            reporter.finish(target.label, report.status)
            outcomes.append(FolderOutcome(target, report))
        result = tuple(outcomes)
        # Optional, like every other `#273` hook (`ConsoleDeployReporter.advance`
        # is the same shape): a caller's own duck-typed reporter that predates
        # this hook, never subclassing `ValidateReporter`, still runs.
        close = getattr(reporter, "close", None)
        if close is not None:
            close(result)
        return ValidateResult(result)


def _build_script(target: ValidateTarget) -> str:
    # Quoted so a path with spaces survives SQLcl's own tokenizer, and refused
    # when the path holds a `"` SQLcl's quoting cannot carry (ADT #653).
    path = target.path.as_posix()
    reject_unquotable(path, role="staging folder")
    return "\n".join([VALIDATE_COMMAND.format(input=path), "exit;"])
