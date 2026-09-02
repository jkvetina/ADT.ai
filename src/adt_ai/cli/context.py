from __future__ import annotations

# ruff: noqa: F401 - compatibility facade re-exports moved helpers.
import argparse
import importlib
import os
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

import adt_ai
from adt_ai.cli.constants import (
    ConfigError,
    ConfigLoader,
    ConnectionLoader,
    ConnectionResult,
    TextSink,
    _StdoutTracker,
)
from adt_ai.cli.context_apex import (
    ApexAppSelection,
    _apex_actions,
    _apex_explicit_actions,
    _apex_recent_report_only,
    _apex_scope,
    _app_in_selection,
    _flatten_arg_groups,
    _flatten_compile_setting_groups,
    _parse_apex_app_selection,
    _parse_apex_export_filter_groups,
    _parse_apex_page_selection,
    _split_config_values,
)
from adt_ai.cli.context_connection import (
    _print_connection_block,
    _print_connection_versions,
)
from adt_ai.cli.context_debug import (
    DebugQueryGateway,
    _debug_sql,
    _print_startup_debug,
)
from adt_ai.cli.context_errors import (
    _display,
    _is_database_connection_error,
    _is_user_database_error,
    _print_config_error,
    _print_database_error,
    _print_sqlcl_error,
    _print_unexpected_error,
    _project_relative,
)
from adt_ai.shared import text_files

FORCED_CHIME_DEFAULT_THEME = "chime"
DISABLED_CHIME_THEME_VALUES = {"", "0", "false", "no", "off"}
# Every caller with no human at a terminal: agent shells, and CI runners. The
# worktree check below cannot stand in for the CI half, because CI clones the
# repo rather than linking a worktree, so it reads as a real checkout. That was
# harmless while CI ran on hardware nobody could hear and stopped being harmless
# when DEV moved onto a self-hosted runner on Jan's own Mac.
AGENT_ENV_VARS = (
    "CODEX_THREAD_ID",
    "CODEX_SHELL",
    "CODEX_INTERNAL_ORIGINATOR_OVERRIDE",
    "CODEX_CI",
    "CODEX_SANDBOX",
    "CLAUDE_CODE_SESSION_ID",
    "CI",
    "GITHUB_ACTIONS",
)


@dataclass(frozen=True)
class StartupContext:
    root                    : Path
    repo_root               : Path
    config                  : dict[str, object]
    config_search_paths     : list[Path]
    config_dirs             : list[Path]
    connection_search_paths : list[Path]
    connection_files        : list[Path]
    connections             : ConnectionResult
    startup_sql             : str | None = None

def _repo_root() -> Path:
    # Anchored on the package, not on this module's own depth: src/adt_ai/__init__.py
    # -> adt_ai -> src -> repo root. Counting parents from __file__ instead would
    # silently resolve to the wrong directory the moment this module moves.
    return Path(adt_ai.__file__).resolve().parents[2]

def _load_startup_context(args: argparse.Namespace) -> StartupContext:
    root = Path(args.root).expanduser().resolve()
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config_result = ConfigLoader(config_search_paths).load()
    text_files.apply_config(config_result.data)
    _remember_completion_config(args, config_result.data)
    connection_search_paths = _connection_search_paths(
        config_result.data, args.config_dir, root, repo_root
    )
    connection_file_candidates = _connection_file_candidates(
        config_result.data, args.config_dir, root, repo_root
    )
    connections = ConnectionLoader(
        connection_search_paths,
        wallet_roots = _wallet_roots(config_result.data, root, repo_root, connection_search_paths),
        key          = getattr(args, "key", None),
    ).load(candidates=connection_file_candidates)
    return StartupContext(
        root                    = root,
        repo_root               = repo_root,
        config                  = config_result.data,
        config_search_paths     = config_search_paths,
        config_dirs             = _existing_paths(config_search_paths),
        connection_search_paths = connection_search_paths,
        connection_files        = connections.files,
        connections             = connections,
        startup_sql             = _load_startup_sql(args.config_dir, root, repo_root),
    )

def _existing_paths(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.exists()]

def _remember_completion_config(args: argparse.Namespace, config: dict[str, object]) -> None:
    args._adt_completion_config = config

def _completion_config(args: argparse.Namespace) -> dict[str, object] | None:
    config = getattr(args, "_adt_completion_config", None)
    if isinstance(config, dict):
        return config
    root_value = getattr(args, "root", None)
    if not root_value:
        return None
    try:
        root = Path(root_value).expanduser().resolve()
        config = ConfigLoader(
            _config_search_paths(getattr(args, "config_dir", None), root, _repo_root())
        ).load().data
    except ConfigError:
        return None
    _remember_completion_config(args, config)
    return config

def _notify_completion(args: argparse.Namespace, exit_code: int) -> None:
    if getattr(args, "nobeep", False):
        return
    config = _completion_config(args)
    forced = _beep_requested(args)
    theme = _beep_theme_override(args) or _configured_chime_theme(config)
    if forced and not theme:
        theme = FORCED_CHIME_DEFAULT_THEME
    if not theme and not forced:
        return
    if not _chime_run_allowed(args):
        return
    try:
        chime = importlib.import_module("chime")
    except ImportError:
        return
    if theme:
        try:
            chime.theme(theme)
        except Exception:
            if not forced:
                return
    try:
        if exit_code == 0:
            chime.success(sync=False, raise_error=False) if forced else chime.success()
        else:
            chime.error(sync=False, raise_error=False) if forced else chime.error()
    except Exception:
        return

def _chime_run_allowed(args: argparse.Namespace) -> bool:
    """Whether this run may play a completion chime.

    ``-nobeep`` is an explicit per-run silence request, so it wins before config
    or ``-beep``. Otherwise beeps come only from a real ADT.ai checkout, so
    interactive runs play while agent worktrees stay silent (background,
    parallel, and scheduled runs don't beep). ``-beep`` forces the chime on for
    a single run regardless.
    """
    if getattr(args, "nobeep", False):
        return False
    if _beep_requested(args):
        return True
    if _agent_caller():
        return False
    return not _running_in_worktree()

def _agent_caller() -> bool:
    """True when ADT.ai is running under an agent shell rather than a terminal."""
    return any(os.environ.get(name) for name in AGENT_ENV_VARS)

def _running_in_worktree(repo_root: Path | None = None) -> bool:
    """True when ADT.ai runs from a linked git worktree rather than a checkout.

    A linked worktree marks its root with a ``.git`` *file* (``gitdir: ...``); a
    real checkout has ``.git`` as a directory. Detection is path-agnostic so it
    works for any interactive checkout versus any agent worktree.
    """
    root = repo_root if repo_root is not None else _repo_root()
    try:
        return (root / ".git").is_file()
    except OSError:
        return False

def _configured_chime_theme(config: Mapping[str, object] | None) -> str | None:
    if not config:
        return None
    value = config.get("chime_theme")
    if not value:
        return None
    theme = str(value).strip()
    if theme.lower() in DISABLED_CHIME_THEME_VALUES:
        return None
    return theme.lower()

def _beep_requested(args: argparse.Namespace) -> bool:
    value = getattr(args, "beep", False)
    return value is not False and value is not None

def _beep_theme_override(args: argparse.Namespace) -> str | None:
    value = getattr(args, "beep", False)
    if value is True or value is False or value is None:
        return None
    theme = str(value).strip()
    return theme.lower() if theme else None

def _config_search_paths(
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    project_paths = [Path(path).expanduser() for path in config_dirs or []]
    if not project_paths:
        project_paths = [root / "config", root]
    return [repo_root / "config", *project_paths]

def _load_startup_sql(
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> str | None:
    """Return the session-setup SQL every new connection runs.

    Two pieces, in this order: the automatic ``DBMS_SESSION.SET_IDENTIFIER``
    block driven by ``config/IDENTITY.yaml`` (``db_schema``) FIRST, then the
    resolved ``STARTUP.sql`` content, the identifier runs before the startup
    file is processed, so a personal ``STARTUP.sql`` can still override it.
    Either piece may be absent; with neither, resolution stays ``None``.

    Both connect paths consume this one composition: python-oracledb replays it
    through ``apply_startup`` and SQLcl receives it verbatim.
    """
    from adt_ai.shared.identity import load_identity, session_identifier

    startup = _load_startup_file(config_dirs, root, repo_root)
    identifier = session_identifier(
        load_identity(_config_search_paths(config_dirs, root, repo_root))
    )
    if identifier is None:
        return startup
    block = _identifier_block(identifier)
    if startup is None:
        return block
    return f"{block}\n{startup}"


def _identifier_block(identifier: str) -> str:
    """A ``SET_IDENTIFIER`` block safe on both connect paths.

    The ``SET FEEDBACK OFF``/``ON`` wrapper keeps SQLcl's ``PL/SQL procedure
    successfully completed.`` feedback out of captured payloads (the ADT #149
    leak class); on the python-oracledb path the wrapper is a client-only
    directive and is skipped by ``apply_startup``.
    """
    escaped = identifier.replace("'", "''")
    return (
        "SET FEEDBACK OFF\n"
        "BEGIN\n"
        f"    DBMS_SESSION.SET_IDENTIFIER('{escaped}');\n"
        "END;\n"
        "/\n"
        "SET FEEDBACK ON"
    )


def _load_startup_file(
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> str | None:
    """Return ``STARTUP.sql`` content for the nearest project location.

    Resolution is nearest-wins so a project ``config/STARTUP.sql`` overrides a
    repo-level copy; the repo-level file, the developer's gitignored copy of
    ``config/STARTUP.sample.sql``, is the last fallback and may be absent. The
    first existing file with non-whitespace content wins, there is no
    concatenation, since session settings must run exactly once per connection.
    An empty (comment-only) file resolves to ``None``, and the committed sample
    itself is never read.
    """
    from adt_ai.shared.startup import split_statements

    project_paths = [Path(path).expanduser() for path in config_dirs or []]
    if not project_paths:
        project_paths = [root / "config", root]
    for directory in [*project_paths, repo_root / "config"]:
        candidate = directory / "STARTUP.sql"
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        if split_statements(text):
            return text
    return None

def _connection_search_paths(
    config: dict[str, object],
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    configured_paths = _path_list(
        _config_value(config, ("connections", "path")),
        _config_value(config, ("connections_path",)),
        _config_value(config, ("connections_dir",)),
    )
    return [
        repo_root / "connections",
        repo_root / "config",
        *configured_paths,
        *[Path(path).expanduser() for path in config_dirs or []],
        root / "connections",
        root / "config",
        root,
    ]

def _wallet_roots(
    config: dict[str, object],
    root: Path,
    repo_root: Path,
    connection_search_paths: list[Path],
) -> list[Path]:
    configured_paths = _path_list(
        _config_value(config, ("connections", "wallet_path")),
        _config_value(config, ("wallet_path",)),
    )
    return [
        repo_root / "connections" / "wallets",
        *configured_paths,
        *[path / "wallets" for path in connection_search_paths],
        root / "connections" / "wallets",
    ]

def _connection_file_candidates(
    config: dict[str, object],
    config_dirs: list[str] | None,
    root: Path,
    repo_root: Path,
) -> list[Path]:
    """Ordered connection-file candidates; the first existing one wins.

    Default scheme (folder basename = ``root.name``):
      1. ``<project>/connections.yaml``                 generic name, project root only
      2. ``<project>/connections/<FOLDER>.yaml``        folder-named, project connections/ dir
      3. ``<repo>/connections/<FOLDER>.yaml``           folder-named, repo connections/ dir

    Overrides (searched first, highest priority): ``connections.path`` /
    ``-config-dir`` directories, and ``connections.file`` replaces the
    derived filenames everywhere (secrets-outside-repo support).
    """
    configured_file = (
        _config_value(config, ("connections", "file"))
        or _config_value(config, ("connections_file",))
        or _config_value(config, ("connection_file",))
    )
    filename = str(configured_file) if configured_file else None

    override_dirs = [
        *_path_list(
            _config_value(config, ("connections", "path")),
            _config_value(config, ("connections_path",)),
            _config_value(config, ("connections_dir",)),
        ),
        *[Path(path).expanduser() for path in config_dirs or []],
    ]

    candidates: list[Path] = []
    for directory in override_dirs:
        if filename:
            candidates.append(directory / filename)
        else:
            candidates.append(directory / "connections.yaml")
            candidates.append(directory / f"{root.name}.yaml")

    if filename:
        candidates.append(root / filename)
        candidates.append(root / "connections" / filename)
        candidates.append(repo_root / "connections" / filename)
    else:
        candidates.append(root / "connections.yaml")
        candidates.append(root / "connections" / f"{root.name}.yaml")
        candidates.append(repo_root / "connections" / f"{root.name}.yaml")

    return _dedup_paths(candidates)

def _dedup_paths(paths: list[Path]) -> list[Path]:
    seen: set[Path] = set()
    unique: list[Path] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return unique

def _path_list(*values: object | None) -> list[Path]:
    paths: list[Path] = []
    for value in values:
        if value is None:
            continue
        if isinstance(value, list | tuple):
            paths.extend(Path(str(item)).expanduser() for item in value)
        else:
            paths.append(Path(str(value)).expanduser())
    return paths

def _print_completion_timer(
    started_at: float,
    stdout: TextSink | None = None,
    completion_args: argparse.Namespace | None = None,
    exit_code: int = 0,
) -> None:
    # Footer spacing is enforced here, in the shared layer, for every command:
    # exactly two empty lines before TIMER (...\n\n\nTIMER). Whatever trailing
    # newlines the previous section left are normalized away, so no module needs
    # to tune its own blank-line count. Keep this knob-free, a per-call override
    # is what let export_apex and update drift apart in the first place.
    elapsed = int(time.monotonic() - started_at + 0.5)
    output = stdout or sys.stdout
    if isinstance(output, _StdoutTracker):
        output.normalize_trailing_newlines(3)
    else:
        output.write("\n\n")
    print(f"TIMER: {elapsed}s", file=output)
    print(file=output)
    if completion_args is not None:
        _notify_completion(completion_args, exit_code)

def _config_value(config: dict[str, object], key_path: tuple[str, ...]) -> object | None:
    value: object = config
    for key in key_path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


__all__ = [
    "AGENT_ENV_VARS",
    "ApexAppSelection",
    "ConfigError",
    "ConfigLoader",
    "ConnectionLoader",
    "ConnectionResult",
    "DISABLED_CHIME_THEME_VALUES",
    "DebugQueryGateway",
    "FORCED_CHIME_DEFAULT_THEME",
    "Mapping",
    "Path",
    "StartupContext",
    "TextIO",
    "TextSink",
    "_StdoutTracker",
    "_agent_caller",
    "_apex_actions",
    "_apex_explicit_actions",
    "_apex_recent_report_only",
    "_apex_scope",
    "_app_in_selection",
    "_beep_requested",
    "_beep_theme_override",
    "_chime_run_allowed",
    "_completion_config",
    "_config_search_paths",
    "_config_value",
    "_configured_chime_theme",
    "_connection_file_candidates",
    "_connection_search_paths",
    "_debug_sql",
    "_dedup_paths",
    "_display",
    "_existing_paths",
    "_flatten_arg_groups",
    "_flatten_compile_setting_groups",
    "_identifier_block",
    "_is_database_connection_error",
    "_is_user_database_error",
    "_load_startup_context",
    "_load_startup_file",
    "_load_startup_sql",
    "_notify_completion",
    "_parse_apex_app_selection",
    "_parse_apex_export_filter_groups",
    "_parse_apex_page_selection",
    "_path_list",
    "_print_completion_timer",
    "_print_config_error",
    "_print_connection_block",
    "_print_connection_versions",
    "_print_database_error",
    "_print_sqlcl_error",
    "_print_startup_debug",
    "_print_unexpected_error",
    "_project_relative",
    "_remember_completion_config",
    "_repo_root",
    "_running_in_worktree",
    "_split_config_values",
    "_wallet_roots",
    "adt_ai",
    "annotations",
    "argparse",
    "dataclass",
    "importlib",
    "os",
    "sys",
    "text_files",
    "time",
]
