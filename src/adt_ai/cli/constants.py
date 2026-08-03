from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TextIO

import yaml

from adt_ai import __version__
from adt_ai.calendar.runner import CalendarError, CalendarRequest, CalendarRunner
from adt_ai.dependencies.queries import PLSCOPE_SESSION_STATEMENT
from adt_ai.dependencies.runner import DependencyIndexRequest, DependencyIndexRunner
from adt_ai.dependencies.store import DependencyStore
from adt_ai.discovery.render import DEFAULT_ROW_LIMIT
from adt_ai.discovery.runner import (
    RESULT_BLOCK_END,
    RESULT_BLOCK_START,
    DiscoveryRequest,
    DiscoveryRunner,
)
from adt_ai.doctor.runner import ActionReporter, DoctorRequest, DoctorRunner, format_action_line
from adt_ai.export_apex.inventory import (
    ApexApplication,
    ApexDiscovery,
    ApexOwnerCount,
    ApexWorkspace,
)
from adt_ai.export_apex.runner import ApexExportRequest, ApexExportRunner
from adt_ai.export_data.inventory import DataTable
from adt_ai.export_data.runner import ExportDataRequest, ExportDataRunner
from adt_ai.export_db.runner import (
    ConsoleExportDbReporter,
    ExportDbRequest,
    ExportDbRunner,
    GatewayFactory,
    print_adt_header,
    print_adt_table,
)
from adt_ai.flow.files import write_all_dumps, write_dump
from adt_ai.flow.model import FlowApp, FlowEdge, FlowPage
from adt_ai.flow.runner import (
    ApexFlowError,
    ApexFlowRefreshRequest,
    ApexFlowRefreshResult,
    ApexFlowRefreshRunner,
)
from adt_ai.flow.store import ApexFlowStore
from adt_ai.rebuild.runner import (
    REVEAL_DEFAULT_LIMIT,
    BranchInfo,
    RebuildRequest,
    RebuildRunner,
    _current_branch,
    branch_commits,
    reveal_branches,
    switch_to_branch,
)
from adt_ai.recompile.inventory import (
    CompileError,
    MaterializedView,
    ObjectOverview,
    SynonymInfo,
)
from adt_ai.recompile.runner import (
    MViewAction,
    RecompileReporter,
    RecompileRequest,
    RecompileRunner,
)
from adt_ai.search_repo.runner import SearchRepoError, SearchRepoRequest, SearchRepoRunner
from adt_ai.shared.apex_owner import ApexOwnerResolutionError, resolve_configured_apex_owner_schema
from adt_ai.shared.config import ConfigError, ConfigLoader
from adt_ai.shared.connections import ConnectionError as ConnectionConfigError
from adt_ai.shared.connections import ConnectionLoader, ConnectionResult
from adt_ai.shared.db import OracleGateway, QueryGateway
from adt_ai.shared.progress import DROPBOX_PATH_RE, DottedProgressBar
from adt_ai.shared.queries import (
    APEX_VERSION_QUERY,
    DATABASE_VERSION_OLD_QUERY,
    DATABASE_VERSION_QUERY,
)
from adt_ai.validate.runner import ValidateRequest, ValidateRunner

PUBLIC_MODULES = (
    ("flow", "map APEX page navigation links (to/from, refresh)", ()),
    ("calendar", "show your Git activity across all branches as a calendar", ()),
    ("connection", "edit the connection file (add env/schema, set password)", ()),
    ("dependencies", "query or refresh the dependency index", ()),
    ("discovery", "run read-only SELECT discovery queries", ()),
    ("doctor", "check local setup and run explicit updates", ()),
    ("export_apex", "export APEX applications", ()),
    ("export_data", "export table data", ()),
    ("export_db", "export database objects", ()),
    ("rebuild", "rebuild the git commit cache", ()),
    ("recompile", "recompile invalid database objects", ()),
    ("search_repo", "search cached Git commit history", ()),
    ("validate", "validate APEXlang application source", ()),
)

PUBLIC_COMMANDS = tuple(
    command
    for module_name, _description, aliases in PUBLIC_MODULES
    for command in (module_name, *aliases)
)

REMOVED_COMPATIBILITY_FLAGS = {
    "flow": (
        "-dump", "--dump", "-format", "--format", "-out", "--out",
        "-remove", "--remove", "-schema", "--schema",
    ),
}

APEX_EXPORT_ACTIONS = (
    "full", "split", "readable", "embedded", "apexlang", "checksum", "rest",
    "files", "files_ws",
)


class AdtArgumentError(Exception):
    pass


class AdtArgumentParser(argparse.ArgumentParser):
    def __init__(self, *args, **kwargs) -> None:
        kwargs.setdefault("allow_abbrev", False)
        super().__init__(*args, **kwargs)

    def format_help(self) -> str:
        help_text = super().format_help()
        return help_text if help_text.endswith("\n\n") else f"{help_text}\n"

    def error(self, message: str) -> None:
        raise AdtArgumentError(message)


class _StdoutTracker:
    def __init__(self, wrapped: TextIO) -> None:
        self.wrapped = wrapped
        self._pending_newlines = ""
        self._committed_trailing_newlines = 0
        self.had_output = False
        # Set by run_schema_sections() once every schema segment of a
        # multi-schema command has printed its own TIMER footer, so the
        # runtime's shared teardown does not print a second, grand-total one.
        self.final_timer_emitted = False

    @property
    def trailing_newlines(self) -> int:
        return len(self._pending_newlines)

    def write(self, text: str) -> int:
        if not text:
            return 0
        self.had_output = True
        stripped = text.rstrip("\n")
        if not stripped:
            self._pending_newlines += text
            return len(text)

        trailing_count = len(text) - len(stripped)
        body = text[:-trailing_count] if trailing_count else text
        self._flush_pending()
        self.wrapped.write(body)
        # Flush the visible body immediately. A header line printed right before a
        # long silent operation (e.g. rebuild's per-commit hashing) carries its
        # line-ending newline in _pending_newlines, so without this flush the body
        # stays in the TTY line buffer — invisible until the first progress line
        # commits the pending newline. Flushing only the already-written body keeps
        # the trailing newlines retractable for the shared footer normalizer.
        self.wrapped.flush()
        self._committed_trailing_newlines = 0
        self._pending_newlines = "\n" * trailing_count
        return len(text)

    def normalize_trailing_newlines(self, count: int) -> None:
        if self._pending_newlines:
            self._pending_newlines = "\n" * count
            return
        self._pending_newlines = "\n" * max(count - self._committed_trailing_newlines, 0)

    def flush(self) -> None:
        # Keep trailing newlines retractable: a mid-stream flush (e.g. the
        # progress bar's print(flush=True)) must not commit the line-ending
        # newlines, or normalize_trailing_newlines() can no longer trim them
        # and the shared footer ends up with an extra blank line. Pending
        # newlines are emitted when real content follows or at finalize().
        self.wrapped.flush()

    def commit_pending(self) -> None:
        self._flush_pending()
        self.wrapped.flush()

    def finalize(self) -> None:
        self.commit_pending()

    def _flush_pending(self) -> None:
        if self._pending_newlines:
            self.wrapped.write(self._pending_newlines)
            self._committed_trailing_newlines = len(self._pending_newlines)
            self._pending_newlines = ""

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)


class _StderrTracker(_StdoutTracker):
    def __init__(self, wrapped: TextIO, stdout_tracker: _StdoutTracker) -> None:
        super().__init__(wrapped)
        self._stdout_tracker = stdout_tracker

    def write(self, text: str) -> int:
        if text:
            self._stdout_tracker.commit_pending()
        return super().write(text)

__all__ = [name for name in globals() if not name.startswith("__") or name == "__version__"]
