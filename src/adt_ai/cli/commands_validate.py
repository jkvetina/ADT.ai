from __future__ import annotations

import argparse
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
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.apex_payloads import drop_legacy_staging, link_payloads
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.error_screen import print_adt_error
from adt_ai.shared.file_list import row as list_row
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.validate.files import ValidateTarget, discovery_label, resolve_targets
from adt_ai.validate.report import UNRECOGNISED, CompileMessage, message_lines
from adt_ai.validate.runner import (
    ValidateReporter,
    ValidateRequest,
    ValidateRunner,
)

# The sibling export that owns the static-file payloads `-apexlang` skips.
FILES_DIR = "files"

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
INPUT_NOT_FOUND_LEAD     = "These inputs are not on disk:"
NOTHING_TO_VALIDATE_LEAD = "Nothing to validate:"


class ConsoleValidateReporter(ValidateReporter):
    """Streams one row per folder: label before the compile, result after.

    Under ``-debug`` the generated script prints first, as a block of its own in
    the shape ``DebugQueryGateway`` gives a ``QUERY:``, and the row follows it
    whole. Echoed from inside the SQLcl call, it landed after the row's label
    and before its verdict, which split one row across the whole block.
    """

    def __init__(self, *, debug: bool = False) -> None:
        self.printer = FixedWidthProgressPrinter()
        self.debug = debug

    def request(self, script: str) -> None:
        if not self.debug:
            return
        print()
        print("SQLCL REQUEST:")
        print(script)
        print()

    def begin(self, label: str) -> None:
        self.printer.begin(label)

    def finish(self, label: str, status: str) -> None:
        self.printer.status(label, status)

    def note(self, message: str) -> None:
        print(f"  {message}")


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

    targets, notes = resolve_targets(root, config, inputs=inputs, app_ids=app_ids)
    refusal = _refusal(targets, root, config, inputs, app_ids)
    if refusal is not None:
        _print_refusal(*refusal, debug_available=hasattr(args, "debug"))
        return 1
    drop_legacy_staging(root)
    notes.extend(_link_payloads(targets))
    reporter = ConsoleValidateReporter(debug=args.debug)

    if targets:
        print_adt_header("VALIDATING:")
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
            print_adt_header(f"UNRECOGNISED OUTPUT {folder.target.label}:")
            print(folder.report.raw.rstrip("\n"))
            print()

    if notes:
        print_adt_header("NOTES:")
        for note in notes:
            reporter.note(note)
        print()

    return 0 if not result.failed and not notes else 1


def _link_payloads(targets: list[ValidateTarget]) -> list[str]:
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
    """
    notes: list[str] = []
    for target in targets:
        if not target.stageable:
            continue
        links = link_payloads(target.path, target.path.parent / FILES_DIR)
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
    """
    print_adt_error(
        "INPUT NOT FOUND",
        [lead, *(list_row(text) for text in rows)],
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
