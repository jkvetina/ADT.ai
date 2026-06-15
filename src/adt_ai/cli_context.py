from __future__ import annotations

from adt_ai.cli_constants import *

class DebugQueryGateway:
    def __init__(self, wrapped: QueryGateway) -> None:
        self.wrapped = wrapped

    def fetch_all(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        return self.wrapped.fetch_all(sql, params)

    def execute(
        self,
        sql: str,
        params: Mapping[str, object] | None = None,
    ) -> None:
        print()
        print("QUERY:")
        print(_debug_sql(sql, params or {}))
        print()
        self.wrapped.execute(sql, params)

    def sqlcl_request(self, request: str, root: Path) -> str:
        print()
        print("SQLCL REQUEST:")
        print(request)
        print()
        return self.wrapped.sqlcl_request(request, root)


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


def _is_user_database_error(error: Exception) -> bool:
    text = str(error)
    if any(marker in text for marker in ("DPY-", "DPI-", "ORA-", "TNS-")):
        return True
    module = type(error).__module__
    return module.startswith("oracledb")


def _is_database_connection_error(error: Exception) -> bool:
    text = str(error)
    connection_markers = (
        "DPY-",
        "DPI-",
        "TNS-",
        "ORA-01017",
        "ORA-12154",
        "ORA-12514",
        "ORA-12541",
        "ORA-12545",
        "ORA-12560",
        "Connect failed",
        "connection",
        "listener",
        "tnsnames.ora",
        "wallet",
    )
    return any(marker.lower() in text.lower() for marker in connection_markers)


def _display(value: object) -> str:
    return DROPBOX_PATH_RE.sub("Dropbox/", str(value))


def _print_database_error(error: Exception) -> None:
    # A failing query attaches its SQL to the exception (OracleGateway). When the
    # SQL is present the failure happened *after* connecting, so it is a query
    # error, not a connection failure — show the offending query and a query
    # banner. Otherwise classify by message markers (TNS/wallet/credential codes).
    sql = getattr(error, "adt_sql", None)
    is_connection = sql is None and _is_database_connection_error(error)
    header = "DATABASE CONNECTION FAILED" if is_connection else "DATABASE QUERY FAILED"
    print(file=sys.stderr)
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)
    if sql is not None:
        print("Query:", file=sys.stderr)
        print(_display(sql), file=sys.stderr)
        print(file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    if is_connection:
        print(
            "Check the connection file and wallet under ADT.ai connections/wallets, then rerun.",
            file=sys.stderr,
        )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _print_config_error(error: Exception) -> None:
    print(file=sys.stderr)
    print("CONFIGURATION NOT FOUND", file=sys.stderr)
    print("-----------------------", file=sys.stderr)
    print(_display(error), file=sys.stderr)
    print(file=sys.stderr)
    print(
        "Run ADT.ai from a project folder that has a connection file, or pass "
        "-config-dir / -root to point at one. See USAGE.md and `adtai doctor -init`.",
        file=sys.stderr,
    )
    print("Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _load_startup_context(args: argparse.Namespace) -> StartupContext:
    root = Path(args.root).expanduser().resolve()
    repo_root = _repo_root()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config_result = ConfigLoader(config_search_paths).load()
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
    setattr(args, "_adt_completion_config", config)


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
    config = _completion_config(args)
    theme = _configured_chime_theme(config)
    if not theme:
        return
    if not _chime_run_allowed(args):
        return
    try:
        chime = importlib.import_module("chime")
    except ImportError:
        return
    chime.theme(theme)
    if exit_code == 0:
        chime.success()
    else:
        chime.error()


def _chime_run_allowed(args: argparse.Namespace) -> bool:
    """Whether this run may play a completion chime.

    Beeps come only from a real ADT.ai checkout, so interactive runs play while
    agent worktrees stay silent (background, parallel, and scheduled runs don't
    beep). ``-beep`` forces the chime on for a single run regardless.
    """
    if getattr(args, "beep", False):
        return True
    return not _running_in_worktree()


def _running_in_worktree(repo_root: Path | None = None) -> bool:
    """True when ADT.ai runs from a linked git worktree rather than a checkout.

    A linked worktree marks its root with a ``.git`` *file* (``gitdir: ...``); a
    real checkout has ``.git`` as a directory. Detection is path-agnostic so it
    works for any interactive checkout versus any agent worktree.
    """
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[2]
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
    return str(value)


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
    """Return ``STARTUP.sql`` content for the nearest project location.

    Resolution is nearest-wins so a project ``config/STARTUP.sql`` overrides the
    shipped repo default; the repo copy is the last fallback. The first existing
    file with non-whitespace content wins — there is no concatenation, since
    session settings must run exactly once per connection. An empty (comment-only)
    file resolves to ``None`` so the shipped template never contributes content.
    """
    from adt_ai.startup import split_statements

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


def _with_schema_folders(
    config: dict[str, object],
    schema_export: dict[str, dict[str, object]],
) -> dict[str, object]:
    schema_folders = {
        schema: subfolder
        for schema, export in schema_export.items()
        if (subfolder := export.get("subfolder"))
    }
    if not schema_folders:
        return config
    existing = config.get("schema_folders", {})
    merged = dict(existing) if isinstance(existing, dict) else {}
    merged.update(schema_folders)
    return {**config, "schema_folders": merged}


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


def _flatten_arg_groups(groups: list[list[str]] | None) -> list[str] | None:
    if not groups:
        return None
    return [
        part.strip()
        for group in groups
        for item in group
        for part in item.split(",")
        if part.strip()
    ]


def _apex_scope(
    apex_config: Mapping[str, object],
    workspace: str | None = None,
    group: str | None = None,
    app_ids: list[str] | None = None,
) -> dict[str, object]:
    return {
        "workspace": workspace or _string_or_none(apex_config.get("workspace")),
        "group": group or _string_or_none(apex_config.get("group")),
        "app_ids": app_ids or _split_config_values(apex_config.get("app")),
    }


def _apex_actions(
    args: argparse.Namespace,
    _config: Mapping[str, object] | None = None,
) -> dict[str, bool]:
    actions = dict.fromkeys(APEX_EXPORT_ACTIONS, False)
    if getattr(args, "all_formats", False):
        return {action: True for action in APEX_EXPORT_ACTIONS}
    for action in APEX_EXPORT_ACTIONS:
        if getattr(args, action, False):
            actions[action] = True
    return actions


def _string_or_none(value: object) -> str | None:
    if value is None or value == "":
        return None
    return str(value)


def _split_config_values(value: object) -> list[str] | None:
    if value is None:
        return None
    values = value if isinstance(value, list | tuple) else [value]
    normalized = [
        part.strip()
        for item in values
        for part in str(item).replace(" ", ",").split(",")
        if part.strip()
    ]
    return normalized or None


def _is_enabled(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    return bool(value)


def _has_job_recent_conflict(recent_days: int | None, object_types: list[str]) -> bool:
    if recent_days is None:
        return False
    return any(_matches_adt_like("JOB", object_type) for object_type in object_types)


def _matches_adt_like(value: str, pattern: str) -> bool:
    return fnmatch.fnmatchcase(value.upper(), pattern.upper().replace("%", "*"))


def _print_startup_debug(context: StartupContext) -> None:
    print()
    print("STARTUP:")
    _print_debug_value("config_dirs", context.config_dirs)
    _print_debug_value("connection_files", context.connection_files)
    print()


def _print_debug_value(key: str, value: object) -> None:
    if value is None or value == "":
        return
    if isinstance(value, list):
        rendered = " | ".join(_display(item) for item in value)
    else:
        rendered = _display(value)
    if not rendered:
        return
    print(f"  {key:<18} {rendered}")


def _debug_sql(sql: str, params: Mapping[str, object]) -> str:
    rendered = sql.strip()
    for key in sorted(params, key=len, reverse=True):
        rendered = rendered.replace(f":{key}", _debug_value(params[key]))
    return rendered


def _debug_value(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"'{escaped}'"
    return str(value)


def _print_connection_block(
    gateway: QueryGateway,
    connection: object | None,
    *,
    schema: str | None = None,
    environment: str | None = None,
    debug: bool = False,
) -> None:
    resolved_schema = schema or str(getattr(connection, "schema", "APP"))
    resolved_environment = environment or str(getattr(connection, "environment", "DEV"))
    print_adt_header(f"CONNECTING TO SCHEMA {resolved_schema}, {resolved_environment}:")
    _print_connection_versions(gateway, connection, debug=debug)


def _print_connection_versions(
    gateway: QueryGateway,
    connection: object | None,
    *,
    debug: bool = False,
) -> None:
    # in -debug mode surface probe failures instead of silently swallowing them
    ignore_errors = not debug
    versions: dict[str, str] = {}
    apex_version = _fetch_version(gateway, APEX_VERSION_QUERY, ignore_errors=ignore_errors)
    if apex_version:
        versions["APEX"] = apex_version

    database_version = _fetch_version(gateway, DATABASE_VERSION_QUERY, ignore_errors=ignore_errors)
    if not database_version:
        database_version = _fetch_version(
            gateway, DATABASE_VERSION_OLD_QUERY, ignore_errors=ignore_errors
        )
    if database_version:
        versions["DATABASE"] = database_version

    thick = getattr(connection, "thick", False)
    if thick:
        versions["THICK"] = _oracle_client_version() or "Y"

    if not versions:
        return
    for key in sorted(versions):
        print(f"{key:>18} | {versions[key]}")
    print()


def _oracle_client_version() -> str:
    try:
        version = importlib.import_module("oracledb").clientversion()
    except Exception:
        return ""
    if not version:
        return ""
    if isinstance(version, tuple) and len(version) >= 2:
        return f"{version[0]}.{version[1]}"
    return str(version)


def _fetch_version(
    gateway: QueryGateway,
    sql: str,
    ignore_errors: bool = False,
) -> str | None:
    try:
        rows = gateway.fetch_all(sql)
    except KeyboardInterrupt:
        raise
    except Exception:
        if ignore_errors:
            return None
        raise
    if not rows:
        return None
    row = rows[0]
    return str(row.get("VERSION") or row.get("version") or "") or None


def _print_completion_timer(
    started_at: float,
    stdout: TextIO | None = None,
) -> None:
    # Footer spacing is enforced here, in the shared layer, for every command:
    # exactly two empty lines before TIMER (...\n\n\nTIMER). Whatever trailing
    # newlines the previous section left are normalized away, so no module needs
    # to tune its own blank-line count. Keep this knob-free — a per-call override
    # is what let export_apex and update drift apart in the first place.
    elapsed = int(time.monotonic() - started_at + 0.5)
    output = stdout or sys.stdout
    if isinstance(output, _StdoutTracker):
        output.normalize_trailing_newlines(3)
    else:
        output.write("\n\n")
    print(f"TIMER: {elapsed}s", file=output)
    print(file=output)


def _config_value(config: dict[str, object], key_path: tuple[str, ...]) -> object | None:
    value: object = config
    for key in key_path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value

__all__ = [name for name in globals() if not name.startswith("__")]
