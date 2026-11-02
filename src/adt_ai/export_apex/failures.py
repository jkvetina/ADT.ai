"""Slices the export could not write, carried to the end of the segment.

**One failed slice used to end the whole run** (ADT #1092): `export_apex -all
-app 122 131 158` on PLAYGROUND stopped at 122's APEXlang slice, and 131 and 158
were never exported. The runner now records the failure against its application,
carries on with every slice and application after it, and raises
`ApexExportFailedError` once the segment is done, so the command still exits 1
and lists every failure under one `ERROR - APP EXPORT FAILED:` header, the
screen Jan picked on 2026-10-28. `export_db` made the same move for objects in
`#917`.

**A run that ends early still lists them** (ADT #1114). The aggregate used to be
built only after a normal return, so a later application's lost connection, or
an argument refusal, dropped every failure listed before it. The aggregate now
carries the terminal error as `abort`, and the failures print first. An
application's own setup (the collection start, its enrichments and comments) is
guarded like a slice and listed under `APP <id>/<alias>` alone.

**And the failure says what APEX said** (ADT #1093). The export opens its APEX
session as the application it exports, so that application's own
error-handling function sees every refusal first, and the template the APEX
docs ship rewrites an internal error into `An unexpected internal application
error has occurred`. Every core application on PLAYGROUND carries one. So a
masked refusal is asked again by `refusal_probe`, the same `GET_APPLICATION`
call with the session detached, which answers with APEX's own sentence: a
master theme that cannot be found, legacy Tabs the APEXlang format refuses.
Reproduced on SANDBOX against the fixture with that handler installed.
"""

from __future__ import annotations

import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.actions import ACTION_HEADERS
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.export_apex.postprocess import _bind_params
from adt_ai.shared.progress import print_adt_header

#: What an application's error handler leaves of a refusal. Matched on the
#: sentence rather than the code, because ORA-20987 is also what APEX raises
#: for the refusals it does explain.
MASKED_TEXT = "An unexpected internal application error has occurred"

#: Jan's pick, 2026-10-28: one header over every failed slice, `export_db`'s
#: `#917` list in the `ERROR - ` family because the command exits 1.
APP_EXPORT_FAILED_HEADER = "ERROR - APP EXPORT FAILED:"

#: The reason wraps inside the 80-column grid at the four-column excerpt indent
#: `ERROR - DEPLOYMENT FAILED:` already uses.
REASON_INDENT = "    "
REASON_WIDTH = 78

_CALL = re.compile(r"l_files := APEX_EXPORT\.GET_APPLICATION \(.*?\);", re.S)
_APEX_PREFIX = re.compile(r"^ORA-20987: (APEX - )?")
_APEX_SUFFIX = re.compile(r"\s*- Contact your application administrator\.?\s*$")
_TAG = re.compile(r"<[^>]+>")
#: Lines that locate an error rather than state it: the PL/SQL stack, the
#: driver's documentation link, and the APEX debug id a refusal can carry
#: (ADT #1097, live on SANDBOX: the screen Jan picked has no such line).
_NOISE = ("ORA-06512:", "Help:", "Details about this incident are available via debug id")


def is_masked_refusal(error: BaseException) -> bool:
    """Whether an application's error handler replaced APEX's own text."""
    text = str(error)
    return "ORA-20987" in text and MASKED_TEXT in text


def refusal_probe(action_sql: str) -> str:
    """The format's own `GET_APPLICATION` call, asked outside the APEX session.

    Lifted out of the export block rather than written again, so the probe
    cannot ask a different question than the export did.
    """
    match = _CALL.search(action_sql)
    if match is None:
        raise ValueError("the export block carries no APEX_EXPORT.GET_APPLICATION call")
    return queries.EXPORT_REFUSAL_PROBE.replace("{call}", match.group(0))


def explain_refusal(
    gateway: Any,
    action_sql: str,
    binds: Mapping[str, Any],
    error: Exception,
) -> Exception:
    """APEX's own refusal behind a masked one, else ``error`` itself.

    Asked only for a masked refusal, so a healthy export or one APEX already
    explained costs no second round trip. A probe that is not refused leaves the
    original error standing: it is the only account there is.
    """
    if not is_masked_refusal(error):
        return error
    probe = refusal_probe(action_sql)
    try:
        gateway.execute(probe, _bind_params(probe, binds))
    except Exception as reason:
        return reason
    return error


def failure_reason(error: BaseException) -> list[str]:
    """What went wrong, in the words of whoever refused, one entry per line."""
    lines: list[str] = []
    for line in str(error).splitlines():
        line = line.strip()
        if not line or line.startswith(_NOISE):
            continue
        if line.startswith("ORA-20987:"):
            line = _APEX_SUFFIX.sub("", _APEX_PREFIX.sub("", line))
            line = _TAG.sub("", line)
        lines.append(line)
    return lines or [type(error).__name__]


@dataclass(frozen=True)
class ApexSliceFailure:
    """One slice that raised. `application` is `None` for a schema-level one."""

    application: ApexApplication | None
    #: `None` for a failure in the application's own setup, before any slice.
    action: str | None
    error: Exception

    @property
    def label(self) -> str:
        """`APP <id>/<alias> | <SLICE>`, the slice as its own row named it.

        A schema-level slice (`-rest`, `-files_ws`) belongs to no application and
        carries no `APP` half, as on the `-compact` bar; an application's setup
        belongs to no slice and carries no slice half.
        """
        if self.application is None:
            return ACTION_HEADERS[self.action or ""]
        application = self.application
        app = f"APP {application.app_id}/{application.app_alias}"
        return app if self.action is None else f"{app} | {ACTION_HEADERS[self.action]}"


class ApexExportFailedError(RuntimeError):
    """Raised once a segment has exported everything it could, never instead.

    `abort` is the error that ended the run before it could finish (a lost
    connection, a refused argument), `None` for a run that completed. It is also
    this error's `__cause__`, so the traceback still reaches it.
    """

    def __init__(
        self,
        failures: Sequence[ApexSliceFailure],
        abort: Exception | None = None,
    ) -> None:
        self.failures = list(failures)
        self.abort = abort
        count = len(self.failures)
        noun = "slice" if count == 1 else "slices"
        message = f"{count} {noun} could not be exported: " + ", ".join(
            failure.label for failure in self.failures
        )
        if abort is not None:
            message += f"; the run then ended on {type(abort).__name__}"
        super().__init__(message)


def print_app_export_failures(failures: Sequence[ApexSliceFailure]) -> None:
    """Every failed slice under one header, the slice at two, the reason at four."""
    if not failures:
        return
    print_adt_header(APP_EXPORT_FAILED_HEADER)
    for failure in failures:
        print(f"  {failure.label}")
        for line in failure_reason(failure.error):
            for row in textwrap.wrap(
                line,
                width             = REASON_WIDTH,
                initial_indent    = REASON_INDENT,
                subsequent_indent = REASON_INDENT,
            ):
                print(row)


__all__ = [
    "APP_EXPORT_FAILED_HEADER",
    "ApexExportFailedError",
    "ApexSliceFailure",
    "MASKED_TEXT",
    "explain_refusal",
    "failure_reason",
    "is_masked_refusal",
    "print_app_export_failures",
    "refusal_probe",
]
