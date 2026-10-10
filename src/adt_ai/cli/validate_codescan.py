"""``validate -codescan``: SQLcl's code scanner over the exports, gated by a baseline (ADT #1026).

A plain `validate` asks the APEXlang compiler whether the exported source still
compiles. `-codescan` asks SQLcl's `codescan` whether the exported code, the
database tree and the APEXlang trees alike, breaks a quality rule, and fails
only on findings the tree's baseline does not already hold. Connectionless like
a plain run: `codescan` answers on a bare `sql -S /nolog` session.

The screen reuses `validate`'s own parts rather than growing new ones: the
streamed row and its clock (`ConsoleValidateReporter`), the stanza a compiler
message prints as (`message_lines`), the refusal screen, `NOTES:` and the
`WARNING - UNRECOGNISED OUTPUT` section. What is new is what Jan approved on
2026-10-05: the `CODESCAN:` header over the rows, one `NEW VIOLATIONS IN
<label>:` section per tree with something new, and the `BASELINE:` line closing
the run. Under `codescan_fail_on: any` every finding fails, and the section
listing them is `validate`'s own `ERRORS IN <label>:`, since "new" would
misname findings the baseline already holds.

ADT #1023 adds `codescan_rules`, the rules a run reports, and one line under
each new finding naming who changed its file last, read off the commit store
`rebuild` keeps; no store, no line.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.cli.commands_validate import (
    INPUT_NOT_FOUND_LEAD,
    NOTHING_TO_VALIDATE_LEAD,
    ConsoleValidateReporter,
    _optional_config,
    _print_messages,
    _print_refusal,
)
from adt_ai.cli.constants import print_adt_header
from adt_ai.cli.context_apex import _flatten_arg_groups
from adt_ai.shared.codescan_settings import (
    FAIL_ON_ANY,
    CodescanSettingError,
    codescan_fail_on,
    codescan_ignore,
    codescan_paths,
    codescan_rules,
)
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.file_list import capped, more_row, row
from adt_ai.validate.codescan import (
    CodescanRequest,
    CodescanRunner,
    bom_files,
    codescan_targets,
    path_inside,
    verdict,
)
from adt_ai.validate.codescan_baseline import baseline, record
from adt_ai.validate.codescan_blame import blame_line, last_changes
from adt_ai.validate.codescan_report import EMPTY, UNRECOGNISED, Finding
from adt_ai.validate.files import ValidateTarget
from adt_ai.validate.report import MESSAGE_WIDTH, CompileMessage, message_lines

CODESCAN_HEADER          = "CODESCAN:"
NEW_VIOLATIONS_HEADER    = "NEW VIOLATIONS IN {label}:"
CODESCAN_PRECHECK_HEADER = "WARNING - CODESCAN PRECHECK ISSUE:"
BASELINE_LABEL           = "BASELINE:"

#: Who changed a new finding's file last sits under its message, at the
#: message's own indent (ADT #1023).
BLAME_INDENT = "    "

#: The one line a BOM-carrying tree gets, above the files it names.
BOM_DESCRIPTION = (
    "{count} file(s) start with a UTF-8 byte order mark, which codescan reads as clean"
)


def run_codescan(
    args          : argparse.Namespace,
    root          : Path,
    sqlcl_request : Callable[..., str],
) -> int:
    """Scan, compare each tree with its baseline, print, record the clean ones."""
    inputs = _flatten_arg_groups(args.input)
    app_ids = _flatten_arg_groups(args.app)
    # Loaded whatever the flags, `-input` included: the gate's own setting and
    # the baseline belong to the project the run is standing in.
    config = _optional_config(args, root, None, None)
    try:
        kinds = codescan_paths(config)
        fail_on = codescan_fail_on(config)
        # Every entry checked before anything scans (ADT #1024).
        ignore = codescan_ignore(config)
        rules = codescan_rules(config)
    except CodescanSettingError as error:
        headline, _, detail = str(error).partition("\n\n")
        print_adt_error("CONFIGURATION INVALID", headline, detail)
        return exit_code_for("CONFIGURATION INVALID")

    targets, notes = codescan_targets(root, config, kinds, inputs, app_ids)
    refusal = _refusal(targets, inputs, app_ids)
    if refusal is not None:
        _print_refusal(*refusal, debug_available=True)
        return 1
    boms = {target.label: bom_files(target.path) for target in targets}

    print_adt_header(CODESCAN_HEADER)
    outcomes = CodescanRunner(sqlcl_request).run(CodescanRequest(
        targets  = tuple(targets),
        root     = root,
        reporter = ConsoleValidateReporter(debug=args.debug),
        ignore   = ignore,
    ))

    known = new = fixed = 0
    failed = False
    for outcome in outcomes:
        label = outcome.target.label
        scan = outcome.scan
        if scan.outcome == UNRECOGNISED:
            # Never swallow what could not be read: show SQLcl's own words.
            print_adt_header(f"WARNING - UNRECOGNISED OUTPUT {label}:")
            print(scan.raw.rstrip("\n"))
            print()
            failed = True
            continue
        if scan.outcome == EMPTY:
            notes.append(f"{label}: codescan read no files, export it again")
            continue
        result = verdict(
            scan, baseline(root, label), fail_on, bom=bool(boms[label]), rules=rules,
        )
        if result.comparison is not None:
            known += result.comparison.known
            new += result.comparison.new
            fixed += result.comparison.fixed
        if result.listed:
            every = fail_on == FAIL_ON_ANY
            blame = {} if every else _blame(root, config, outcome.target.path, result.listed)
            _print_findings(label, result.listed, every=every, blame=blame)
        failed = failed or result.violated
        if result.records:
            # Every finding, the profile's or not (ADT #1023): `verdict` docstring.
            record(root, label, scan.findings)

    _print_bom_files(targets, boms)
    if notes:
        print_adt_header("NOTES:")
        for note in notes:
            print(f"  {note}")
        print()
    _print_summary(f"{BASELINE_LABEL} {known} known, {new} new, {fixed} fixed")
    return 1 if failed or notes or any(boms.values()) else 0


def _print_summary(line: str) -> None:
    """The closing line, one blank under whatever section printed last.

    A section already ends on its own blank, the rows do not, so the gap is
    normalized rather than printed: the stream keeps trailing newlines
    retractable for exactly this (`cli/stream_tracker.py`).
    """
    print()
    normalize = getattr(sys.stdout, "normalize_trailing_newlines", None)
    if callable(normalize):
        normalize(2)
    print(line)


def _refusal(
    targets : list[ValidateTarget],
    inputs  : list[str] | None,
    app_ids : list[str] | None,
) -> tuple[str, list[str], str] | None:
    """The two cases `validate` refuses, worded for what `-codescan` looks for."""
    missing = [target.label for target in targets if not target.path.exists()]
    if missing:
        return (INPUT_NOT_FOUND_LEAD, missing, "Check the path.")
    if not targets and not inputs and not app_ids:
        return (
            NOTHING_TO_VALIDATE_LEAD,
            ["No exported database or apexlang/ tree for codescan_paths"],
            "Run `adtai export_db` or `adtai export_apex -apexlang` first.",
        )
    return None


def _print_findings(
    label    : str,
    findings : tuple[Finding, ...],
    *,
    every    : bool,
    blame    : Mapping[str, str] | None = None,
) -> None:
    """One stanza per finding: locator, rule, message wrapped at 80 columns.

    ``blame`` maps a finding's file to who changed it last (ADT #1023), one
    line under the message at its indent; a file it does not name gets none.
    """
    messages = tuple(_message(finding) for finding in findings)
    if every:
        # Every finding fails under `any`, so they read as `validate`'s errors.
        _print_messages(f"ERRORS IN {label}:", messages)
        return
    print_adt_header(NEW_VIOLATIONS_HEADER.format(label=label))
    print()
    for index, (finding, message) in enumerate(zip(findings, messages, strict=True)):
        if index:
            print()
        for line in message_lines((message,)):
            print(line)
        attribution = (blame or {}).get(finding.file)
        if attribution:
            print(f"{BLAME_INDENT}{attribution}")
    print()


def _blame(
    root     : Path,
    config   : Mapping[str, Any] | None,
    tree     : Path,
    findings : tuple[Finding, ...],
) -> dict[str, str]:
    """Each finding's file mapped to its last change in the commit store (ADT #1023).

    A finding's file is relative to its tree and the store keeps repository
    paths, so each is read through the project root; a tree outside the root,
    an `-input` elsewhere on disk, has nothing the store could name.
    """
    paths: dict[str, str] = {}
    for finding in findings:
        repository = path_inside(tree / finding.file, root)
        if repository is not None:
            paths[finding.file] = repository
    commits = last_changes(root, config, paths.values())
    width = MESSAGE_WIDTH - len(BLAME_INDENT)
    return {
        file: blame_line(commits[repository], config, width)
        for file, repository in paths.items()
        if repository in commits
    }


def _message(finding: Finding) -> CompileMessage:
    return CompileMessage(
        file    = finding.file,
        line    = finding.line,
        column  = finding.column,
        type    = finding.rule,
        message = finding.message,
    )


def _print_bom_files(targets: list[ValidateTarget], boms: dict[str, list[str]]) -> None:
    """`WARNING - CODESCAN PRECHECK ISSUE:`, the shape of `validate`'s CRLF precheck.

    The tree on its own line, what is wrong one level under it, then the files,
    capped: the screen needs the pattern, not the inventory. Nothing is
    rewritten, a BOM is the developer's to remove, so the run fails until it is.
    """
    flagged = [target.label for target in targets if boms[target.label]]
    if not flagged:
        return
    print_adt_header(CODESCAN_PRECHECK_HEADER)
    for label in flagged:
        files = boms[label]
        print(f"  {label}")
        print(f"    {BOM_DESCRIPTION.format(count=len(files))}")
        shown, remaining = capped(files)
        for path in shown:
            print(row(path, depth=2))
        if remaining:
            print(more_row(remaining, depth=3))
    print()


__all__ = [name for name in globals() if not name.startswith("__")]
