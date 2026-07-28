"""Hydrate ADT/Oracle variables from the user's shell startup file.

An AI tool (Claude Code, Codex) spawns a non-login, non-interactive shell, so
``~/.zshrc`` never runs and everything the user exports there is missing from
ADT.ai's environment: ``ADT_KEY`` (which decrypts connection passwords),
``ADT_ENV``, ``ORACLE_HOME``, the Instant Client library paths, and the SQLcl
``PATH`` entries. The command then fails in a way that reads like a config bug
rather than a missing environment.

:func:`hydrate_environment` runs once at the top of the CLI entry point, so
every module inherits it from a single hook. When either sentinel
(``ADT_ENV`` / ``ORACLE_HOME``) is unset it fills in the allowlisted variables
from the user's startup file — never overwriting one that is already set, so
an explicit environment always wins.

Extraction is hybrid. The file is read as text and its ``export VAR=value``
lines are parsed with ``~`` and ``$VAR`` expansion, which covers the normal
case without executing anything. Only when a sentinel is *still* unresolved
does it fall back to running the shell (``zsh -lic 'export -p'``), which also
sees variables set inside a function, a conditional, or an ``eval``.

POSIX only; Windows is an explicit no-op.

What actually carries each consumer, measured on macOS 2026-07-27 rather than
reasoned about:

- **Thick mode**: ``ORACLE_HOME``. python-oracledb reads it out of the live
  ``os.environ`` when ``init_oracle_client()`` runs, so a late-set value works
  — which is why hydration must mutate the real environment, not a copy. With
  only ``DYLD_LIBRARY_PATH`` set the call fails ``DPI-1047``: dyld fixes its
  search path when the process starts and never rereads the variable.
- **SQLcl**: ``PATH``. It is a Java program on the JDBC *thin* driver — ADT.ai
  never asks it for thick JDBC — so it needs no Oracle client libraries at
  all, only to be found and launched.

``DYLD_LIBRARY_PATH`` and ``LD_LIBRARY_PATH`` are still hydrated because they
are correct on Linux and harmless here — **not** because they reach a child
process on macOS. They do not: SIP strips ``DYLD_*`` when exec'ing a protected
binary, and the SQLcl launcher is a bash script, so ``/bin/bash`` drops the
variable before the JVM ever starts.

Values are never logged. :class:`BootstrapResult` carries variable names only,
because ``ADT_KEY`` is a password.
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable, MutableMapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ShellRunner = Callable[[Sequence[str]], str]

# Either one missing means the process never saw the user's startup file.
SENTINEL_VARIABLES = ("ADT_ENV", "ORACLE_HOME")

# The variables worth carrying over. PATH is absent on purpose: it is derived
# from the hydrated ORACLE_HOME below rather than copied wholesale.
HYDRATED_VARIABLES = (
    "ADT_REPO",
    "ADT_CLIENT",
    "ADT_PROJECT",
    "ADT_ENV",
    "ADT_BRANCH",
    "ADT_SCHEMA",
    "ADT_KEY",
    "ORACLE_HOME",
    "TNS_ADMIN",
    "NLS_LANG",
    "DBVERSION",
    "DYLD_LIBRARY_PATH",
    "LD_LIBRARY_PATH",
    "OCI_LIB_DIR",
    "OCI_INC_DIR",
    "JAVA_TOOL_OPTIONS",
)

# Startup files per shell, in the order a real session would pick them up.
STARTUP_FILES = {
    "zsh" : (".zshrc", ".zprofile", ".zshenv"),
    "bash": (".bash_profile", ".bashrc", ".profile"),
    "sh"  : (".profile",),
}
DEFAULT_STARTUP_FILES = (".profile", ".zshrc", ".bashrc")

# Appended to PATH relative to ORACLE_HOME, in this order.
PATH_SUFFIXES = ("", "sqlcl/bin")

SHELL_TIMEOUT_SECONDS = 10

_EXPORT_LINE = re.compile(r"^\s*export\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_DECLARE_LINE = re.compile(r"^\s*declare\s+-x\s+([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
_VARIABLE_REFERENCE = re.compile(
    r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)"
)


@dataclass(frozen=True)
class BootstrapResult:
    """What hydration did — names only, never values (``ADT_KEY`` is a secret)."""

    source : str = ""               # startup file parsed, or the shell that was run
    method : str = ""               # "" | "parse" | "shell"
    applied: tuple[str, ...] = ()   # variables this run set
    skipped: tuple[str, ...] = ()   # variables found but already set explicitly
    home   : str = ""               # home the run resolved against, for display

    def display_source(self, home: Path | None = None) -> str:
        """The source with the home directory folded back to ``~``, for ``doctor``."""
        if not self.source:
            return ""
        home_path = str(home) if home is not None else (self.home or str(Path.home()))
        if self.source.startswith(home_path + os.sep):
            return "~" + self.source[len(home_path):]
        return self.source


_LAST_RESULT = BootstrapResult()


def last_result() -> BootstrapResult:
    """The most recent :func:`hydrate_environment` outcome, for ``doctor``."""
    return _LAST_RESULT


def reset_last_result() -> None:
    global _LAST_RESULT
    _LAST_RESULT = BootstrapResult()


def hydrate_environment(
    env          : MutableMapping[str, str] | None = None,
    *,
    home         : Path | None = None,
    shell        : str | None = None,
    shell_runner : ShellRunner | None = None,
    platform_name: str | None = None,
) -> BootstrapResult:
    """Fill in missing ADT/Oracle variables from the user's startup file.

    Returns a :class:`BootstrapResult`; also stored for :func:`last_result`.
    Safe to call more than once — a fully-set environment is a no-op.
    """
    global _LAST_RESULT

    target = os.environ if env is None else env
    if (platform_name or os.name) == "nt":
        _LAST_RESULT = BootstrapResult()
        return _LAST_RESULT
    if all(target.get(name) for name in SENTINEL_VARIABLES):
        _LAST_RESULT = BootstrapResult()
        return _LAST_RESULT

    home_path = home or Path.home()
    shell_path = shell if shell is not None else target.get("SHELL", "")

    values, source = _parse_startup_files(home_path, shell_path, target)
    method = "parse" if values else ""

    if not _sentinels_resolved(values, target):
        shell_values = _read_from_shell(shell_path, shell_runner)
        if shell_values:
            # The file is closer to the user's intent, so it wins on overlap.
            for name, value in shell_values.items():
                values.setdefault(name, value)
            method = "shell"
            source = shell_path

    applied, skipped = _apply(values, target, home_path)
    _LAST_RESULT = BootstrapResult(
        source  = source if applied else "",
        method  = method if applied else "",
        applied = applied,
        skipped = skipped,
        home    = str(home_path),
    )
    return _LAST_RESULT


def _sentinels_resolved(
    values: MutableMapping[str, str],
    env   : MutableMapping[str, str],
) -> bool:
    return all(values.get(name) or env.get(name) for name in SENTINEL_VARIABLES)


def _startup_candidates(home: Path, shell: str) -> list[Path]:
    names = STARTUP_FILES.get(Path(shell).name, DEFAULT_STARTUP_FILES)
    return [home / name for name in names]


def _parse_startup_files(
    home : Path,
    shell: str,
    env  : MutableMapping[str, str],
) -> tuple[dict[str, str], str]:
    """Parse each candidate in order; the first productive file names the source."""
    values: dict[str, str] = {}
    source = ""
    for path in _startup_candidates(home, shell):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        before = len(values)
        _parse_exports(text, values, env, home)
        if not source and len(values) > before:
            source = str(path)
    return values, source


def _parse_exports(
    text  : str,
    values: dict[str, str],
    env   : MutableMapping[str, str],
    home  : Path,
) -> None:
    for line in text.splitlines():
        match = _EXPORT_LINE.match(line)
        if match is None:
            continue
        name, raw = match.group(1), match.group(2)
        if name in values:
            continue  # first file wins
        literal, quoted = _unquote(raw)
        values[name] = literal if quoted else _expand(literal, values, env, home)


def _unquote(raw: str) -> tuple[str, bool]:
    """Strip surrounding quotes; the flag says the value is single-quoted (literal)."""
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1].replace("'\\''", "'"), value[0] == "'"
    # An unquoted value ends at an inline comment.
    comment = value.find(" #")
    if comment != -1:
        value = value[:comment].rstrip()
    return value, False


def _expand(
    value : str,
    values: MutableMapping[str, str],
    env   : MutableMapping[str, str],
    home  : Path,
) -> str:
    if value.startswith("~"):
        value = str(home) + value[1:]

    def replace(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        resolved = values.get(name)
        if resolved is None:
            resolved = env.get(name, "")
        return resolved

    return _VARIABLE_REFERENCE.sub(replace, value)


def _read_from_shell(shell: str, runner: ShellRunner | None) -> dict[str, str]:
    """Ask a login+interactive shell for its exports; fail-soft on any error."""
    if not shell:
        return {}
    run = runner or _run_login_shell
    try:
        output = run([shell, "-lic", "export -p"])
    except Exception:  # noqa: BLE001 - a missing/failing shell is not a command failure
        return {}
    values: dict[str, str] = {}
    for line in (output or "").splitlines():
        match = _EXPORT_LINE.match(line) or _DECLARE_LINE.match(line)
        if match is None:
            continue
        name, raw = match.group(1), match.group(2)
        values.setdefault(name, _unquote(raw)[0])
    return values


def _run_login_shell(command: Sequence[str]) -> str:
    completed = subprocess.run(
        command,
        check          = True,
        capture_output = True,
        text           = True,
        timeout        = SHELL_TIMEOUT_SECONDS,
    )
    return completed.stdout


def _apply(
    values: MutableMapping[str, str],
    env   : MutableMapping[str, str],
    home  : Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    applied: list[str] = []
    skipped: list[str] = []
    for name in HYDRATED_VARIABLES:
        value = values.get(name)
        if not value:
            continue
        if env.get(name):
            skipped.append(name)
            continue
        env[name] = value
        applied.append(name)

    if _extend_path(env):
        applied.append("PATH")

    return tuple(sorted(applied)), tuple(sorted(skipped))


def _extend_path(env: MutableMapping[str, str]) -> bool:
    """Append ORACLE_HOME and its SQLcl launcher dir to PATH, without duplicates."""
    oracle_home = env.get("ORACLE_HOME")
    if not oracle_home:
        return False
    current = env.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    added = False
    for suffix in PATH_SUFFIXES:
        entry = str(Path(oracle_home) / suffix) if suffix else oracle_home
        if entry in entries:
            continue
        entries.append(entry)
        added = True
    if added:
        env["PATH"] = os.pathsep.join(entries)
    return added
