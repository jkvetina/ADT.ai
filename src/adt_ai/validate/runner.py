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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared.db import run_sqlcl_script
from adt_ai.validate.files import ValidateTarget
from adt_ai.validate.report import FolderReport, parse_validate_output

SqlclRequest = Callable[..., str]

VALIDATE_COMMAND = 'apex validate -input "{input}"'


class ValidateReporter:
    """No-op reporter; the console implementation lives in ``cli/``."""

    def begin(self, label: str) -> None:
        pass

    def finish(self, label: str, status: str) -> None:
        pass

    def note(self, message: str) -> None:
        pass


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
            reporter.begin(target.label)
            try:
                output = self.sqlcl_request(
                    _build_script(target),
                    request.root,
                    project_root=request.project_root,
                )
            except Exception:
                # Complete the row before the error reaches the shared banner, so
                # the visible failure sits on the folder being worked.
                reporter.finish(target.label, "FAILED")
                raise
            report = parse_validate_output(output)
            reporter.finish(target.label, report.status)
            outcomes.append(FolderOutcome(target, report))
        return ValidateResult(tuple(outcomes))


def _build_script(target: ValidateTarget) -> str:
    # Quoted so a path with spaces survives SQLcl's own tokenizer.
    return "\n".join([VALIDATE_COMMAND.format(input=target.path.as_posix()), "exit;"])
