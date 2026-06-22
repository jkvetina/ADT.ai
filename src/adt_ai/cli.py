from __future__ import annotations

from adt_ai.cli_startup import print_startup_failure as _print_startup_failure

try:
    from adt_ai import cli_commands_exports as _commands_exports
    from adt_ai import cli_commands_flow as _commands_flow
    from adt_ai import cli_commands_history as _commands_history
    from adt_ai import cli_commands_recompile as _commands_recompile
    from adt_ai import cli_constants as _constants
    from adt_ai import cli_context as _context
    from adt_ai import cli_context_connection as _context_connection
    from adt_ai import cli_context_errors as _context_errors
    from adt_ai import cli_parser as _parser
    from adt_ai import cli_runtime as _runtime
except Exception as _startup_error:  # noqa: BLE001 - friendly banner instead of raw traceback
    _STARTUP_ERROR: BaseException | None = _startup_error

    def main(*args, **kwargs) -> int:
        _print_startup_failure(_STARTUP_ERROR)
        return 1

    def console_main() -> None:
        raise SystemExit(main())
else:
    _STARTUP_ERROR = None

    _EXPORT_MODULES = (
        _constants,
        _parser,
        _context,
        _commands_history,
        _commands_recompile,
        _commands_flow,
        _commands_exports,
        _runtime,
    )
    _PATCH_MODULES = _EXPORT_MODULES[2:]
    _PATCH_MODULES = (*_PATCH_MODULES, _context_connection, _context_errors)
    _PATCHABLE_NAMES = (
        "DoctorRunner",
        "DROPBOX_PATH_RE",
        "RecompileRunner",
        "SearchRepoRunner",
        "_oracle_client_version",
        "_repo_root",
        "_running_in_worktree",
        "date",
    )

    for _module in _EXPORT_MODULES:
        for _name, _value in vars(_module).items():
            if not _name.startswith("__") or _name == "__version__":
                globals()[_name] = _value

    def _sync_patches() -> None:
        for _name in _PATCHABLE_NAMES:
            if _name not in globals():
                continue
            for _module in _PATCH_MODULES:
                if hasattr(_module, _name):
                    setattr(_module, _name, globals()[_name])

    def main(*args, **kwargs):
        _sync_patches()
        return _runtime.main(*args, **kwargs)

    def console_main() -> None:
        raise SystemExit(main())

    def _chime_run_allowed(*args, **kwargs):
        _sync_patches()
        return _context._chime_run_allowed(*args, **kwargs)

    def _print_connection_versions(*args, **kwargs):
        _sync_patches()
        return _context._print_connection_versions(*args, **kwargs)

    __all__ = [name for name in globals() if not name.startswith("__") or name == "__version__"]
