from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import TextIO

from adt_ai import __version__
from adt_ai.cli.commands_connection import _run_connection
from adt_ai.cli.commands_diff import _run_diff
from adt_ai.cli.commands_export_data import _run_export_data
from adt_ai.cli.commands_exports import _run_export_apex, _run_export_db
from adt_ai.cli.commands_history import _run_calendar, _run_rebuild, _run_search
from adt_ai.cli.commands_patch import _run_patch
from adt_ai.cli.commands_recompile import _run_discovery, _run_doctor, _run_recompile
from adt_ai.cli.commands_search_graph import _search_argument_error
from adt_ai.cli.commands_ut import _run_ut3
from adt_ai.cli.commands_validate import _run_validate
from adt_ai.cli.constants import (
    PUBLIC_COMMANDS,
    PUBLIC_MODULES,
    AdtArgumentError,
    ConfigError,
    ConnectionConfigError,
    GatewayFactory,
    TextSink,
    _StderrTracker,
    _StdoutTracker,
    print_adt_header,
    print_module_banner,
)
from adt_ai.cli.context import (
    _is_user_database_error,
    _notify_completion,
    _print_completion_timer,
    _print_config_error,
    _print_database_error,
    _print_sqlcl_error,
    _print_unexpected_error,
)
from adt_ai.cli.context_errors import _print_export_failures
from adt_ai.cli.gateways import gateway_scope
from adt_ai.cli.help import format_command_help
from adt_ai.cli.parser import (
    _command_parser,
    _command_title,
    _has_help_flag,
    _removed_compatibility_args,
    build_parser,
)
from adt_ai.cli.raw_completion_args import _completion_args_from_raw
from adt_ai.cli.rebuild_refresh import _rebuild_argument_error
from adt_ai.cli.validate_scan import _scan_argument_error
from adt_ai.export_db.failures import ExportObjectsFailedError
from adt_ai.shared.announce import announced_factory, strict_mode
from adt_ai.shared.env_bootstrap import hydrate_environment
from adt_ai.shared.error_screen import exit_code_for, print_adt_error
from adt_ai.shared.internal_paths import migrate_internal_files
from adt_ai.shared.sqlcl_script import SqlclScriptError


class _TrackedScreen:
    """The stdout/stderr tracker pair one screen runs behind.

    The trackers are what the console guard reads and what holds trailing
    newlines back so the shared ``TIMER`` footer can still retract them
    (``cli/stream_tracker.py``), so every screen in this file installs both and
    every screen takes both down again. That was written out three times, and
    the footer-target rule beside it twice, which is three places to keep in
    step with each other and with whatever a tracker becomes next (`#670`).
    """

    def __init__(self, stdout: _StdoutTracker, stderr: _StdoutTracker) -> None:
        self.stdout = stdout
        self.stderr = stderr

    def footer_target(self, exit_code: int, default: TextSink | None = None) -> TextSink:
        """Where the ``TIMER`` footer goes.

        A failed run whose message went to stderr keeps its footer there, under
        the message, rather than on a stdout the reader may have piped away.
        ``default`` is the caller's own routing for the success case
        (``search -format yaml`` sends chrome to stderr); without one the
        footer follows the output.
        """
        if exit_code != 0 and self.stderr.had_output:
            return self.stderr
        return self.stdout if default is None else default


@contextmanager
def _tracked_screen() -> Iterator[_TrackedScreen]:
    """Install the tracker pair for one screen, and restore the real streams.

    A context manager rather than a paired call, so a failure between the
    install and the command's own ``try`` cannot leave the process printing
    through a tracker that nobody will finalize.
    """
    original_stdout = sys.stdout
    original_stderr = sys.stderr
    tracked_stdout = (
        original_stdout
        if isinstance(original_stdout, _StdoutTracker)
        else _StdoutTracker(original_stdout)
    )
    tracked_stderr = (
        original_stderr
        if isinstance(original_stderr, _StdoutTracker)
        else _StderrTracker(original_stderr, tracked_stdout)
    )
    sys.stdout = tracked_stdout
    sys.stderr = tracked_stderr
    try:
        yield _TrackedScreen(tracked_stdout, tracked_stderr)
    finally:
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr


def _run_static_screen(
    render: Callable[[], None],
    *,
    exit_code: int = 0,
    timer_stdout: TextSink | None = None,
    completion_args: argparse.Namespace | None = None,
) -> int:
    with _tracked_screen() as screen:
        started_at = time.monotonic()
        try:
            render()
        finally:
            _print_completion_timer(
                started_at,
                stdout=screen.footer_target(exit_code, timer_stdout),
                completion_args=completion_args,
                exit_code=exit_code,
            )
    return exit_code


def _run_command_help(command: str, parser: argparse.ArgumentParser) -> int:
    print_module_banner(_command_title(command))
    print(format_command_help(command, _command_parser(parser, command)), end="")
    return 0


def _run_command_argument_error(
    command: str,
    message: str,
    raw_args: Sequence[str] | None = None,
) -> int:
    # `<command>: error: <message>` sat flush under the banner until ADT #764:
    # no header, no remedy, and a lowercase word as the only thing marking a
    # refusal on a screen that can run to hundreds of lines.
    def render() -> None:
        print_module_banner(_command_title(command), file=sys.stderr)
        print_adt_error(
            "ARGUMENT INVALID",
            message,
            f"Run `adtai {command} -h` for the options this command takes.",
        )

    return _run_static_screen(
        render,
        exit_code=exit_code_for("ARGUMENT INVALID"),
        completion_args=_completion_args_from_raw(raw_args or []),
    )


def _run_top_level_argument_error(
    message: str,
    raw_args: Sequence[str] | None = None,
) -> int:
    # Same completion contract as the command-level screen above, and
    # `console.md` §Completion sounds states it once for both: `-beep` fires
    # "on any executable path, argument errors and connection failures
    # included". This screen alone passed no args, so `adtai -bogus -beep` was
    # silent while `adtai export_db -bogus -beep` chimed (`#656`).
    # The whole screen is a refusal, so all of it goes to stderr (ADT #764).
    # The banner no longer carries the word ERROR either: the code below it says
    # so, and putting it in the module slot made the tool's own name look like
    # the thing that failed.
    def render() -> None:
        print_module_banner(file=sys.stderr)
        print_adt_error("ARGUMENT INVALID", message)
        _print_module_overview(file=sys.stderr)

    return _run_static_screen(
        render,
        exit_code=exit_code_for("ARGUMENT INVALID"),
        completion_args=_completion_args_from_raw(raw_args or []),
    )


def _run_top_level_error(error: Exception) -> int:
    # Catch-all for failures during parser construction / setup, before any
    # command banner has printed. Always show the banner and a friendly
    # message; the raw traceback only appears under -debug (handled by caller).
    def render() -> None:
        print_module_banner(file=sys.stderr)
        print_adt_error(
            "UNEXPECTED ERROR",
            f"{type(error).__name__}: {error}",
            debug_available=True,
        )

    return _run_static_screen(render, exit_code=exit_code_for("UNEXPECTED ERROR"))


def main(
    argv: Sequence[str] | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    # One hook for every module: an AI tool's shell never ran ~/.zshrc, so the
    # ADT/Oracle variables are missing until we read them ourselves.
    hydrate_environment()

    raw_argv = list(sys.argv[1:] if argv is None else argv)
    debug_requested = "-debug" in raw_argv or "--debug" in raw_argv
    if not raw_argv:
        return _run_module_overview()
    if _is_unknown_command(raw_argv[0]):
        # A word that is not a command is named as one, which is more use than
        # the overview alone, so this runs before the help check below.
        return _run_invalid_command(raw_argv[0])
    if raw_argv[0] not in PUBLIC_COMMANDS and _has_help_flag(raw_argv):
        # Top-level help, however it was spelled. The test was `raw_argv in
        # (["-h"], ["--help"])`, so `adtai -h extra` fell through to argparse's
        # own help action and printed the raw `usage:` block `console.md`
        # §Top-level help says this screen never shows (`#670`). Past the check
        # above, a first token that is not a command starts with `-`, so this
        # cannot swallow a command's own `-h`, which takes the per-command help
        # screen further down.
        return _run_module_overview()

    try:
        parser = build_parser()
        if raw_argv[0] in PUBLIC_COMMANDS:
            removed_args = _removed_compatibility_args(raw_argv[0], raw_argv[1:])
            if removed_args:
                return _run_command_argument_error(
                    raw_argv[0],
                    f"UNRECOGNIZED ARGUMENTS: {' '.join(removed_args)}",
                    raw_argv[1:],
                )
        if raw_argv[0] in PUBLIC_COMMANDS and _has_help_flag(raw_argv[1:]):
            return _run_command_help(raw_argv[0], parser)
        args = parser.parse_args(raw_argv)
    except AdtArgumentError as error:
        if raw_argv[0] in PUBLIC_COMMANDS:
            return _run_command_argument_error(raw_argv[0], str(error), raw_argv[1:])
        return _run_top_level_argument_error(str(error), raw_argv)
    except SystemExit as error:
        return int(error.code or 0)
    except Exception as error:
        # Parser construction or other setup failed before any command banner.
        # Show the shared ERROR screen instead of leaking a raw traceback.
        if debug_requested:
            raise
        return _run_top_level_error(error)

    if args.version:
        print(f"ADT.ai {__version__}")
        return 0

    # Second hook for every module, same reason as hydrate_environment(): the
    # generated data files belong under config/internal/, and a project root
    # written by an older ADT.ai still has them loose in config/. Relocating
    # here rather than per command is what makes it hold for every command. It
    # runs before the banner, so it neither prints nor raises (internal_paths).
    _migrate_internal_files(args)

    # A flag the chosen mode would ignore is refused before the banner, on the
    # parser-style screen with exit 2, never inside a handler that already
    # printed it. `dependencies` and `flow` were retired by `#30`, so they reach
    # `_is_unknown_command` above and never get here.
    argument_error = _mode_argument_error(args)
    if argument_error is not None:
        return _run_command_argument_error(raw_argv[0], argument_error, raw_argv[1:])

    with _tracked_screen() as screen:
        exit_code = _run_command(args, screen, gateway_factory)

    return exit_code


def _run_command(
    args: argparse.Namespace,
    screen: _TrackedScreen,
    gateway_factory: GatewayFactory | None,
) -> int:
    """Dispatch one parsed invocation behind an installed tracker pair."""
    resources = ExitStack()
    gateway_resources = resources.enter_context(gateway_scope())
    if gateway_factory is not None and strict_mode():
        # The one place every command's injected gateway passes through, so the
        # console guard is armed for all of them from here rather than enrolled
        # per command. The real gateway is wrapped in cli.gateways.build_gateway.
        gateway_factory = announced_factory(gateway_factory)
    if gateway_factory is not None:
        # Injected gateways obey the same command lifetime as real ones. The
        # wrapper deduplicates cached factories by identity before teardown.
        gateway_factory = gateway_resources.track_factory(gateway_factory)
    started_at = time.monotonic()
    timer_stdout = _command_timer_stdout(args, screen.stdout)
    exit_code = 0
    try:
        if args.command == "calendar":
            exit_code = _run_calendar(args)
        elif args.command == "export_db":
            exit_code = _run_export_db(args, gateway_factory=gateway_factory)
        elif args.command == "export_data":
            exit_code = _run_export_data(args, gateway_factory=gateway_factory)
        elif args.command == "export_apex":
            exit_code = _run_export_apex(args, gateway_factory=gateway_factory)
        elif args.command == "patch":
            exit_code = _run_patch(args, gateway_factory=gateway_factory)
        elif args.command == "diff":
            exit_code = _run_diff(args, gateway_factory=gateway_factory)
        elif args.command == "rebuild":
            exit_code = _run_rebuild(args, gateway_factory=gateway_factory)
        elif args.command == "search":
            exit_code = _run_search(args, gateway_factory=gateway_factory)
        elif args.command == "recompile":
            exit_code = _run_recompile(args, gateway_factory=gateway_factory)
        elif args.command == "doctor":
            exit_code = _run_doctor(args)
        elif args.command == "discovery":
            exit_code = _run_discovery(args, gateway_factory=gateway_factory)
        elif args.command == "connection":
            exit_code = _run_connection(args, gateway_factory=gateway_factory)
        elif args.command == "ut":
            exit_code = _run_ut3(args, gateway_factory=gateway_factory)
        elif args.command == "validate":
            exit_code = _run_validate(args, gateway_factory=gateway_factory)
    except KeyboardInterrupt:
        exit_code = 130
        print("\nInterrupted by user.", file=sys.stderr)
    except (ConnectionConfigError, ConfigError) as error:
        exit_code = 1
        if getattr(args, "debug", False):
            raise
        _print_config_error(error, debug_available=hasattr(args, "debug"))
    except Exception as error:
        exit_code = 1
        if getattr(args, "debug", False):
            raise
        # `hasattr` rather than the value: the namespace carries the attribute
        # exactly where the parser declared the flag, so the commands that never
        # took `-debug` stop closing their refusals by advising it (`#656`).
        debug_available = hasattr(args, "debug")
        if isinstance(error, SqlclScriptError):
            _print_sqlcl_error(error, debug_available=debug_available)
        elif isinstance(error, ExportObjectsFailedError):
            # `diff` and `recompile` pull through the export runner too, and a
            # refused object reaches them after the rest was written (`#917`).
            _print_export_failures(error, debug_available=debug_available)
        elif _is_user_database_error(error):
            _print_database_error(error, debug_available=debug_available)
        else:
            _print_unexpected_error(error, debug_available=debug_available)
    finally:
        resources.close()
        # A completed multi-schema run (run_schema_sections) already printed
        # its own per-segment TIMER footers and set this latch on loop
        # completion only, a mid-loop failure leaves it unset, so the shared
        # footer below still covers that case exactly as before.
        if getattr(screen.stdout, "final_timer_emitted", False):
            _notify_completion(args, exit_code)
        else:
            _print_completion_timer(
                started_at,
                stdout=screen.footer_target(exit_code, timer_stdout),
                completion_args=args,
                exit_code=exit_code,
            )

    return exit_code


def _migrate_internal_files(args: argparse.Namespace) -> None:
    """Sweep an older layout's ``config/`` data files into ``config/internal/``.

    ``-root`` is declared by every module with a project root; the handful that
    carry none (a bare screen never reaches here) simply have nothing to sweep.
    """
    root = getattr(args, "root", None)
    if root is None:
        return
    migrate_internal_files(Path(root).expanduser())


def _is_unknown_command(value: str) -> bool:
    return not value.startswith("-") and value not in PUBLIC_COMMANDS


def _module_display_name(module_name: str, aliases: tuple[str, ...]) -> str:
    if not aliases:
        return module_name
    return f"{module_name} ({', '.join(aliases)})"


def _print_module_overview(file: TextIO | None = None) -> None:
    module_rows = [
        (_module_display_name(module_name, aliases), description)
        for module_name, description, aliases in PUBLIC_MODULES
    ]
    module_width = max(len(module_name) for module_name, _description in module_rows)

    print_adt_header("MODULES:", file=file)
    for module_name, description in module_rows:
        print(f"  {module_name:<{module_width}}  {description}", file=file)


def _run_module_overview() -> int:
    print_module_banner()
    print("Modern ADT command line tool.")
    print()
    _print_module_overview()
    print()
    return 0


def _run_invalid_command(command: str) -> int:
    # The whole screen is a refusal, so all of it goes to stderr and
    # `footer_target` puts the TIMER under it there (ADT #764). Before this card
    # it was on stdout, exited 1 where an unknown FLAG exited 2, and put the
    # word ERROR in the banner's module slot.
    def render() -> None:
        print_module_banner(file=sys.stderr)
        details: list[str] | None = None
        if command == "init":
            details = ["Use:", "  adtai doctor -init"]
        print_adt_error("UNKNOWN COMMAND", f"`{command}` IS NOT AN ADT.ai COMMAND", details)
        _print_module_overview(file=sys.stderr)

    return _run_static_screen(render, exit_code=exit_code_for("UNKNOWN COMMAND"))


def _command_timer_stdout(args: argparse.Namespace, stdout: TextSink) -> TextSink:
    # The one command that prints a machine `-format` keeps stdout pure data.
    if args.command == "search" and getattr(args, "format", "table") != "table":
        return sys.stderr
    return stdout


#: The commands whose modes refuse a flag another mode takes, by command.
_MODE_ARGUMENT_CHECKS = {
    "rebuild":  _rebuild_argument_error,
    "search":   _search_argument_error,
    "validate": _scan_argument_error,
}


def _mode_argument_error(args: argparse.Namespace) -> str | None:
    check = _MODE_ARGUMENT_CHECKS.get(args.command)
    return None if check is None else check(args)


__all__ = [name for name in globals() if not name.startswith("__")]
