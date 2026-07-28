from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path

from adt_ai.cli.constants import ConfigError, ConfigLoader, print_adt_header
from adt_ai.cli.context import _config_search_paths, _repo_root
from adt_ai.cli.context_apex import _flatten_arg_groups
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.progress import FixedWidthProgressPrinter
from adt_ai.validate.files import ValidateTarget, resolve_targets
from adt_ai.validate.report import UNRECOGNISED, CompileMessage, message_lines
from adt_ai.validate.runner import (
    ValidateReporter,
    ValidateRequest,
    ValidateRunner,
)
from adt_ai.validate.staging import stage_apexlang, staging_root_for

# The sibling export that owns the static-file payloads `-apexlang` skips.
FILES_DIR = "files"


class ConsoleValidateReporter(ValidateReporter):
    """Streams one row per folder: label before the compile, result after."""

    def __init__(self, silent: bool = False) -> None:
        self.silent = silent
        self.printer = FixedWidthProgressPrinter()

    def begin(self, label: str) -> None:
        if not self.silent:
            self.printer.begin(label)

    def finish(self, label: str, status: str) -> None:
        if not self.silent:
            self.printer.status(label, status)

    def note(self, message: str) -> None:
        print(f"  {message}")


def _run_validate(args: argparse.Namespace) -> int:
    print_adt_header("APEX DEPLOYMENT TOOL: VALIDATE")
    root = Path(args.root).expanduser().resolve()
    inputs = _flatten_arg_groups(args.input)
    app_ids = _flatten_arg_groups(args.app)
    config = _optional_config(args, root, inputs, app_ids)

    targets, notes = resolve_targets(root, config, inputs=inputs, app_ids=app_ids)
    targets, staging_notes = _stage_targets(targets, root)
    notes.extend(staging_notes)
    reporter = ConsoleValidateReporter(silent=args.silent)

    if targets and not args.silent:
        print_adt_header("VALIDATING:")
    result = ValidateRunner(sqlcl_request=_sqlcl_request(args.debug)).run(
        ValidateRequest(
            targets      = tuple(targets),
            root         = root,
            project_root = root,
            reporter     = reporter,
        )
    )

    for folder in result.folders:
        if folder.report.warnings:
            # Never silent: FILE_IGNORED means the compiler did not check that
            # file at all, which a bare "OK" would hide behind a clean pass.
            _print_messages(f"WARNINGS: {folder.target.label}", folder.report.warnings)
        if folder.report.errors:
            _print_messages(f"ERRORS: {folder.target.label}", folder.report.errors)
        elif folder.report.outcome == UNRECOGNISED:
            # Never swallow output the parser could not read: show it verbatim so
            # the user can see what SQLcl actually said.
            print_adt_header(f"UNRECOGNISED OUTPUT: {folder.target.label}")
            print(folder.report.raw.rstrip("\n"))
            print()

    if notes:
        print_adt_header("NOTES:")
        for note in notes:
            reporter.note(note)
        print()

    return 0 if not result.failed and not notes else 1


def _stage_targets(
    targets : list[ValidateTarget],
    root    : Path,
) -> tuple[list[ValidateTarget], list[str]]:
    """Point each project target at a staged tree that has its payloads linked in.

    `apexlang/` alone cannot validate on any app that owns static files: the
    export omits the payloads by design and `static-files.apx` references every
    one of them. Staging hardlinks the sibling `files/` export into place, so the
    compiler sees a complete tree while the repo keeps exactly one copy of each
    file (card `#165`).

    The label is deliberately not rewritten — the user asked about their export,
    so the progress row and every message section keep naming the folder they can
    open, and the staging tree stays an invisible build detail.
    """
    staged: list[ValidateTarget] = []
    notes: list[str] = []
    for target in targets:
        if not target.stageable:
            staged.append(target)
            continue
        files_root = target.path.parent / FILES_DIR
        tree = stage_apexlang(target.path, files_root, staging_root_for(root, target.path))
        staged.append(replace(target, path=tree.path))
        if tree.payload_files == 0 and _references_payloads(target.path):
            # The compiler will report one REFERENCE_NOT_FOUND per payload, which
            # says what is missing but not how to get it. This says how.
            notes.append(
                f"{target.label}: static files are referenced but none are exported "
                f"- run `adtai export_apex -app {target.app_id or ''} -files`.".replace(
                    "  ", " "
                )
            )
    return staged, notes


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


def _sqlcl_request(debug: bool):
    """Echo the generated script under ``-debug``, mirroring ``DebugQueryGateway``.

    Read from the module global at call time so the CLI facade's patch sync can
    swap SQLcl out wholesale in tests.
    """
    if not debug:
        return run_sqlcl_script

    def request(script: str, root: Path, project_root: Path | None = None) -> str:
        print()
        print("SQLCL REQUEST:")
        print(script)
        print()
        return run_sqlcl_script(script, root, project_root=project_root)

    return request


def _optional_config(
    args    : argparse.Namespace,
    root    : Path,
    inputs  : list[str] | None,
    app_ids : list[str] | None,
) -> dict[str, object]:
    """Load ``config.yaml`` only when path resolution actually needs it.

    A pure ``-input`` run must work from any folder — that is what makes the
    command usable in CI and from a checkout with no ADT project around it — so
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
