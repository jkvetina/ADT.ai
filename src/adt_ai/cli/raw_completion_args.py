"""The completion-sound options, read off the raw argv.

A refusal drawn before argparse finished, or after it gave up, has no namespace
to ask whether `-beep` or `-nobeep` was given, so these read those two, and the
`-root` / `-config-dir` that locate the project config, straight off the argv in
both the `-flag value` and the `-flag=value` spellings (`#656`).

Split out of `cli/runtime.py` by ADT #923, which found that module at 21 KB: this
is the one part of it with a single job and no other reader.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence


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
