from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable, Sequence
from typing import TextIO

from adt_ai import __version__
from adt_ai.cli.commands_connection import _run_connection
from adt_ai.cli.commands_dependencies import _dependencies_argument_error, _run_dependencies
from adt_ai.cli.commands_export_data import _run_export_data
from adt_ai.cli.commands_exports import _run_export_apex, _run_export_db
from adt_ai.cli.commands_flow import _run_flow
from adt_ai.cli.commands_history import _run_rebuild, _run_search_repo
from adt_ai.cli.commands_recompile import _run_discovery, _run_doctor, _run_recompile
from adt_ai.cli.constants import (
    PUBLIC_COMMANDS,
    PUBLIC_MODULES,
    AdtArgumentError,
    ConfigError,
    ConnectionConfigError,
    GatewayFactory,
    _StderrTracker,
    _StdoutTracker,
    print_adt_header,
)
from adt_ai.cli.context import (
    _is_user_database_error,
    _print_completion_timer,
    _print_config_error,
    _print_database_error,
    _print_unexpected_error,
)
from adt_ai.cli.help import format_command_help
from adt_ai.cli.parser import (
    _command_parser,
    _command_title,
    _has_help_flag,
    _removed_compatibility_args,
    build_parser,
)


def _run_static_screen(
    render: Callable[[], None],
    *,
    exit_code: int = 0,
    timer_stdout: TextIO | None = None,
    completion_args: argparse.Namespace | None = None,
) -> int:
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
    started_at = time.monotonic()
    try:
        render()
    finally:
        footer_stdout = (
            tracked_stderr
            if exit_code != 0 and tracked_stderr.had_output
            else (timer_stdout or tracked_stdout)
        )
        _print_completion_timer(
            started_at,
            stdout=footer_stdout,
            completion_args=completion_args,
            exit_code=exit_code,
        )
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return exit_code


def _run_command_help(command: str, parser: argparse.ArgumentParser) -> int:
    print_adt_header(f"APEX DEPLOYMENT TOOL: {_command_title(command)}")
    print(format_command_help(command, _command_parser(parser, command)), end="")
    return 0


def _run_command_argument_error(
    command: str,
    message: str,
    raw_args: Sequence[str] | None = None,
) -> int:
    def render() -> None:
        print_adt_header(f"APEX DEPLOYMENT TOOL: {_command_title(command)}", file=sys.stderr)
        print(f"{command}: error: {message}", file=sys.stderr)
        print(file=sys.stderr)

    return _run_static_screen(
        render,
        exit_code=2,
        completion_args=_completion_args_from_raw(raw_args or []),
    )


def _run_top_level_argument_error(message: str) -> int:
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: {message}")
        print()
        _print_module_overview()

    return _run_static_screen(render, exit_code=2)


def _run_top_level_error(error: Exception) -> int:
    # Catch-all for failures during parser construction / setup, before any
    # command banner has printed. Always show the banner and a friendly
    # message; the raw traceback only appears under -debug (handled by caller).
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR", file=sys.stderr)
        print(f"Error: {type(error).__name__}: {error}", file=sys.stderr)
        print(file=sys.stderr)
        print("This is unexpected. Use -debug to show the Python traceback.", file=sys.stderr)

    return _run_static_screen(render, exit_code=1)


def main(
    argv: Sequence[str] | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    debug_requested = "-debug" in raw_argv or "--debug" in raw_argv
    if not raw_argv or raw_argv in (["-h"], ["--help"]):
        return _run_module_overview()
    if raw_argv and _is_unknown_command(raw_argv[0]):
        return _run_invalid_command(raw_argv[0])

    try:
        parser = build_parser()
        if raw_argv[0] in PUBLIC_COMMANDS:
            removed_args = _removed_compatibility_args(raw_argv[0], raw_argv[1:])
            if removed_args:
                return _run_command_argument_error(
                    raw_argv[0],
                    f"unrecognized arguments: {' '.join(removed_args)}",
                    raw_argv[1:],
                )
        if raw_argv[0] in PUBLIC_COMMANDS and _has_help_flag(raw_argv[1:]):
            return _run_command_help(raw_argv[0], parser)
        args = parser.parse_args(raw_argv)
    except AdtArgumentError as error:
        if raw_argv[0] in PUBLIC_COMMANDS:
            return _run_command_argument_error(raw_argv[0], str(error), raw_argv[1:])
        return _run_top_level_argument_error(str(error))
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

    if args.command in {"dependencies", "depends"}:
        dependencies_error = _dependencies_argument_error(args)
        if dependencies_error is not None:
            return _run_command_argument_error(raw_argv[0], dependencies_error)

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
    started_at = time.monotonic()
    timer_stdout = _command_timer_stdout(args, tracked_stdout)
    exit_code = 0
    try:
        if args.command == "export_db":
            exit_code = _run_export_db(args, gateway_factory=gateway_factory)
        elif args.command == "export_data":
            exit_code = _run_export_data(args, gateway_factory=gateway_factory)
        elif args.command == "export_apex":
            exit_code = _run_export_apex(args, gateway_factory=gateway_factory)
        elif args.command == "rebuild":
            exit_code = _run_rebuild(args)
        elif args.command == "search_repo":
            exit_code = _run_search_repo(args)
        elif args.command == "recompile":
            exit_code = _run_recompile(args, gateway_factory=gateway_factory)
        elif args.command == "doctor":
            exit_code = _run_doctor(args)
        elif args.command == "dependencies":
            exit_code = _run_dependencies(args, gateway_factory=gateway_factory)
        elif args.command == "flow":
            exit_code = _run_flow(args, gateway_factory=gateway_factory)
        elif args.command == "discovery":
            exit_code = _run_discovery(args, gateway_factory=gateway_factory)
        elif args.command == "connection":
            exit_code = _run_connection(args)
    except KeyboardInterrupt:
        exit_code = 130
        print("\nInterrupted by user.", file=sys.stderr)
    except (ConnectionConfigError, ConfigError) as error:
        exit_code = 1
        if getattr(args, "debug", False):
            raise
        _print_config_error(error)
    except Exception as error:
        exit_code = 1
        if getattr(args, "debug", False):
            raise
        if _is_user_database_error(error):
            _print_database_error(error)
        else:
            _print_unexpected_error(error)
    finally:
        footer_stdout = (
            tracked_stderr
            if exit_code != 0 and tracked_stderr.had_output
            else timer_stdout
        )
        _print_completion_timer(
            started_at,
            stdout=footer_stdout,
            completion_args=args,
            exit_code=exit_code,
        )
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr

    return exit_code


def _is_unknown_command(value: str) -> bool:
    return not value.startswith("-") and value not in PUBLIC_COMMANDS


def _module_display_name(module_name: str, aliases: tuple[str, ...]) -> str:
    if not aliases:
        return module_name
    return f"{module_name} ({', '.join(aliases)})"


def _print_module_overview() -> None:
    module_rows = [
        (_module_display_name(module_name, aliases), description)
        for module_name, description, aliases in PUBLIC_MODULES
    ]
    module_width = max(len(module_name) for module_name, _description in module_rows)

    print_adt_header("MODULES:")
    for module_name, description in module_rows:
        print(f"  {module_name:<{module_width}}  {description}")


def _run_module_overview() -> int:
    print_adt_header("APEX DEPLOYMENT TOOL")
    print("Modern ADT command line tool.")
    print()
    _print_module_overview()
    print()
    return 0


def _run_invalid_command(command: str) -> int:
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
    started_at = time.monotonic()
    try:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: unknown command `{command}`.")
        print()
        if command == "init":
            print("Use:")
            print("  adtai doctor -init")
            print()
        elif command in {"update", "upgrade"}:
            print("Use one of:")
            print("  adtai doctor -update")
            print("  adtai doctor -sqlcl")
            print()
        _print_module_overview()
    finally:
        _print_completion_timer(started_at, stdout=tracked_stdout)
        tracked_stdout.finalize()
        if tracked_stderr is not tracked_stdout:
            tracked_stderr.finalize()
        sys.stdout = original_stdout
        sys.stderr = original_stderr
    return 1


def _command_timer_stdout(args: argparse.Namespace, stdout: TextIO) -> TextIO:
    if args.command == "dependencies" and getattr(args, "format", "table") != "table":
        return sys.stderr
    return stdout


def _completion_args_from_raw(raw_args: Sequence[str]) -> argparse.Namespace | None:
    beep = _raw_beep_value(raw_args)
    nobeep = _raw_flag_present(raw_args, ("-nobeep", "--nobeep"))
    if beep is False and not nobeep:
        return None
    return argparse.Namespace(
        beep       = beep,
        nobeep     = nobeep,
        root       = _raw_option_value(raw_args, ("-root", "--root"), "."),
        config_dir = _raw_option_values(raw_args, ("-config-dir", "--config-dir")),
    )


def _raw_beep_value(raw_args: Sequence[str]) -> bool | str:
    value: bool | str = False
    index = 0
    while index < len(raw_args):
        arg = raw_args[index]
        if arg in {"-beep", "--beep"}:
            value = True
            if index + 1 < len(raw_args) and not raw_args[index + 1].startswith("-"):
                value = raw_args[index + 1]
                index += 2
                continue
        else:
            for name in ("-beep", "--beep"):
                if arg.startswith(f"{name}="):
                    value = arg.split("=", 1)[1] or True
                    break
        index += 1
    return value


def _raw_flag_present(raw_args: Sequence[str], names: tuple[str, ...]) -> bool:
    return any(arg in names for arg in raw_args)


def _raw_option_value(
    raw_args: Sequence[str],
    names: tuple[str, ...],
    default: str,
) -> str:
    values = _raw_option_values(raw_args, names)
    return values[-1] if values else default


def _raw_option_values(raw_args: Sequence[str], names: tuple[str, ...]) -> list[str]:
    values: list[str] = []
    index = 0
    while index < len(raw_args):
        value = raw_args[index]
        matched = False
        for name in names:
            if value == name:
                if index + 1 < len(raw_args):
                    values.append(raw_args[index + 1])
                index += 2
                matched = True
                break
            if value.startswith(f"{name}="):
                values.append(value.split("=", 1)[1])
                index += 1
                matched = True
                break
        if not matched:
            index += 1
    return values


__all__ = [name for name in globals() if not name.startswith("__")]
