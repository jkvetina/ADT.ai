from __future__ import annotations

import argparse
from collections.abc import Sequence

from adt_ai.cli.constants import (
    PUBLIC_MODULES,
    REMOVED_COMPATIBILITY_FLAGS,
    AdtArgumentParser,
)
from adt_ai.cli.help import generated_command_usage as _generated_command_usage
from adt_ai.cli.parser_admin import add_admin_parsers
from adt_ai.cli.parser_database import add_database_parsers
from adt_ai.cli.parser_exports import add_export_parsers
from adt_ai.cli.parser_history import add_history_parsers


def build_parser() -> argparse.ArgumentParser:
    parser = AdtArgumentParser(
        prog="adt",
        description="Modern ADT command line tool.",
    )
    parser.add_argument("--version", action="store_true", help="show version and exit")
    subparsers = parser.add_subparsers(
        dest         = "command",
        parser_class = AdtArgumentParser,
    )

    add_history_parsers(subparsers)
    add_export_parsers(subparsers)
    add_admin_parsers(subparsers)
    add_database_parsers(subparsers)

    for command, _description, _aliases in PUBLIC_MODULES:
        _add_completion_args(_command_parser(parser, command))
    _apply_generated_command_usages(parser)
    return parser


def _add_completion_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--beep",
        "-beep",
        nargs="?",
        const=True,
        default=False,
        metavar="THEME",
        help=(
            "force the completion chime on for this run, optionally with a theme "
            "override"
        ),
    )
    parser.add_argument(
        "--nobeep",
        "-nobeep",
        action="store_true",
        help="suppress completion sounds for this run",
    )


def _canonical_command_name(command: str) -> str:
    for module_name, _description, aliases in PUBLIC_MODULES:
        if command == module_name or command in aliases:
            return module_name
    return command


def _command_title(command: str) -> str:
    return _canonical_command_name(command).upper()


def _command_parser(parser: argparse.ArgumentParser, command: str) -> argparse.ArgumentParser:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action.choices[command]
    raise KeyError(command)


def _has_help_flag(argv: Sequence[str]) -> bool:
    return any(arg in {"-h", "--help"} for arg in argv)


def _apply_generated_command_usages(parser: argparse.ArgumentParser) -> None:
    for command, _description, _aliases in PUBLIC_MODULES:
        command_parser = _command_parser(parser, command)
        command_parser.usage = _generated_command_usage(command, command_parser)


def _removed_compatibility_args(command: str, argv: Sequence[str]) -> list[str]:
    removed = set(REMOVED_COMPATIBILITY_FLAGS.get(_canonical_command_name(command), ()))
    rejected: list[str] = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg in removed:
            rejected.append(arg)
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                rejected.append(argv[index + 1])
                index += 1
        index += 1
    return rejected

__all__ = [name for name in globals() if not name.startswith("__")]
