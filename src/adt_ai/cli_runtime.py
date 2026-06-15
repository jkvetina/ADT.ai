from __future__ import annotations

from adt_ai import __version__
from adt_ai.cli_help import format_command_help
from adt_ai.cli_parser import *
from adt_ai.cli_context import *
from adt_ai.cli_commands_history import *
from adt_ai.cli_commands_recompile import *
from adt_ai.cli_commands_exports import *

def _run_static_screen(
    render: Callable[[], None],
    *,
    exit_code: int = 0,
    timer_stdout: TextIO | None = None,
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
        footer_stdout = tracked_stderr if exit_code != 0 and tracked_stderr.had_output else (timer_stdout or tracked_stdout)
        _print_completion_timer(started_at, stdout=footer_stdout)
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


def _run_command_argument_error(command: str, message: str) -> int:
    def render() -> None:
        print_adt_header(f"APEX DEPLOYMENT TOOL: {_command_title(command)}", file=sys.stderr)
        print(f"{command}: error: {message}", file=sys.stderr)
        print(file=sys.stderr)

    return _run_static_screen(render, exit_code=2)


def _run_top_level_argument_error(message: str) -> int:
    def render() -> None:
        print_adt_header("APEX DEPLOYMENT TOOL: ERROR")
        print(f"Error: {message}")
        print()
        _print_module_overview()

    return _run_static_screen(render, exit_code=2)


def main(
    argv: Sequence[str] | None = None,
    gateway_factory: GatewayFactory | None = None,
) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv or raw_argv in (["-h"], ["--help"]):
        return _run_module_overview()
    if raw_argv and _is_unknown_command(raw_argv[0]):
        return _run_invalid_command(raw_argv[0])

    parser = build_parser()
    if raw_argv[0] in PUBLIC_COMMANDS:
        removed_args = _removed_compatibility_args(raw_argv[0], raw_argv[1:])
        if removed_args:
            return _run_command_argument_error(
                raw_argv[0],
                f"unrecognized arguments: {' '.join(removed_args)}",
            )
    if raw_argv[0] in PUBLIC_COMMANDS and _has_help_flag(raw_argv[1:]):
        return _run_command_help(raw_argv[0], parser)
    try:
        args = parser.parse_args(raw_argv)
    except AdtArgumentError as error:
        if raw_argv[0] in PUBLIC_COMMANDS:
            return _run_command_argument_error(raw_argv[0], str(error))
        return _run_top_level_argument_error(str(error))
    except SystemExit as error:
        return int(error.code or 0)

    if args.version:
        print(f"ADT.ai {__version__}")
        return 0

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
        elif args.command == "discovery":
            exit_code = _run_discovery(args, gateway_factory=gateway_factory)
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
        if getattr(args, "debug", False) or not _is_user_database_error(error):
            raise
        _print_database_error(error)
    finally:
        footer_stdout = (
            tracked_stderr
            if exit_code != 0 and tracked_stderr.had_output
            else timer_stdout
        )
        _print_completion_timer(started_at, stdout=footer_stdout)
        _notify_completion(args, exit_code)
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
    return stdout


def console_main() -> None:
    raise SystemExit(main())

__all__ = [name for name in globals() if not name.startswith("__")]
