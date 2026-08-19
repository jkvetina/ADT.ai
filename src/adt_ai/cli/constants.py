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
    DiscoveryRequest,
    DiscoveryRunner,
    write_file_results,
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
from adt_ai.shared.progress import DROPBOX_PATH_RE, DottedProgressBar, print_module_banner
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
    ("ut", "run utPLSQL test suites", ()),
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
    # Renamed by ADT #292: `-commits` -> `-window`, `-full` -> `-fullapp`.
    #
    # `-full` NEEDS this entry to make the break real. argparse resolves an
    # unambiguous prefix, and `-full` is a prefix of `-fullapp`, so on Python
    # 3.11 `patch -full 100` was still silently accepted as the renamed flag --
    # the exact opposite of the loud failure a hard break is for. CI caught it:
    # py3.13 rejected it and py3.11 did not, because argparse's abbreviation
    # handling differs between them. Rejecting on the raw argv, before argparse
    # sees it, is version-independent.
    #
    # `-commits` is listed for the same reason of principle rather than need --
    # it is not a prefix of `-window`, so argparse already errors, but a
    # removed name belongs in the removed list whatever today's parser does with
    # it, and it makes both renames produce the same message.
    # ADT #309 closed the set Jan postponed: `-refresh` -> `-contents`,
    # `-hash` -> `-rollout`, and `-ref` dropped outright with no successor.
    # None of the three old names is a prefix of its new one, so argparse
    # already errors on them, but a removed name belongs in the removed list
    # whatever today's parser does with it, and listing them keeps every
    # rejection message identical.
    # ADT #345 withdrew `-rebuild`, which parsed and did nothing: it reached
    # `PatchRequest.rebuild` and no reader. Unlike the renames above it has no
    # successor spelling on `patch`, because the behaviour its help advertised
    # was never `patch`'s to begin with. `adtai rebuild` owns the per-branch
    # commit cache, and a `patch` run writes only its own internal scan file.
    # The 2026-08-15 batch (#351, #353, #356) withdrew four more. `-window`
    # became three config keys, `-files` and `-contents` became what naming a
    # patch prints, and `-deldiff` became automatic cleanup. `-deldiff` and
    # `-contents` need this entry the way `-full` did: both are unambiguous
    # prefixes of nothing that survives, but `-de` and `-co` abbreviations
    # resolve differently across Python versions once a neighbour is added, and
    # a removed name belongs on the removed list whatever today's parser does.
    # ADT #345, the same withdrawal on the other command the audit caught.
    # `calendar -list` selected a day-row format the task-centric report had
    # already replaced, so it filled `CalendarRequest.list_mode` and changed
    # nothing. `docs/calendar.md` called it inert and kept it, which is the
    # accepted-but-unused compatibility flag SOP §Command surface rules out.
    "calendar": ("-list", "--list"),
    # ADT #317 removed `-dense` outright: it restyled the rows of a section that
    # no longer prints by default. The per-test listing it collapsed is now
    # `-verbose`, and what a default run shows is the `RUNNING TESTS:` bar --
    # there is no successor flag, because the shape `-dense` produced (one
    # counted line per suite) was `SUMMARY PER SUITE:` one section early.
    #
    # Listed here rather than merely deleted for the reason `patch -full` proved:
    # a name is only reliably gone when the rejection happens on the raw argv.
    # `-dense` is not a prefix of any surviving `ut` flag, so argparse would
    # error on it today, but that is a property of today's flag set, and the
    # next flag added to this command could silently make an old name resolve
    # again.
    "ut": ("-dense", "--dense"),
    # ADT #343 removed `-checksum`: the fingerprint is no longer something you
    # ask for, it is collected on every export and cached in
    # `config/internal/apex_apps.yaml`, so there is no successor flag either.
    #
    # Listed here rather than merely deleted for the reason `patch -full`
    # proved: argparse resolves an unambiguous prefix, and `-checksum` is not a
    # prefix of any surviving `export_apex` flag only until the next flag is
    # added. Rejecting on the raw argv does not depend on today's flag set.
    "export_apex": ("-checksum", "--checksum"),
}
# `patch` also used to reject `-head` and `-local` here. That entry was the
# honest form of "never built" while they were unimplemented (SOP §Command
# surface: implement the behaviour, or leave the flag unsupported with a
# parser-error test). ADT #280 implemented both (plus `-nosnap`) so keeping
# them rejected would now be the opposite defect.

APEX_EXPORT_ACTIONS = (
    "full", "split", "readable", "embedded", "apexlang", "rest",
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
        # Set by print_adt_header through shared/announce.py and retired by the
        # blank line that closes the section. A section header is the one
        # announcement that ends its own line, so it is the one the cursor
        # position cannot see.
        self._announced_header = False
        # Set by a redrawable row that has reached its own end, and cleared by
        # the next thing printed. The cursor is still mid-line, so the open-line
        # rule below would read it as an announcement; the row is a result.
        self._open_line_finished = False
        # Has anything printed under the current header yet? A header may lay
        # down its own trailing blank before the first row (`start_export`
        # does), and that blank is part of the header block, not the end of the
        # section it opened.
        self._section_has_body = False
        # Set by run_schema_sections() once every schema segment of a
        # multi-schema command has printed its own TIMER footer, so the
        # runtime's shared teardown does not print a second, grand-total one.
        self.final_timer_emitted = False

    @property
    def trailing_newlines(self) -> int:
        return len(self._pending_newlines)

    @property
    def announced(self) -> bool:
        """Does the screen say what the process is doing?

        Two shapes count. A line the cursor is still sitting on is a label
        waiting for its own result, which is what `FixedWidthProgressPrinter.
        begin`, the dotted bar and a streamed table half-row all leave behind:
        nothing has to opt in, because leaving the line open IS the
        announcement. And a section header, which ends its line like any
        finished row and so says so explicitly through `mark_announced()`.

        **A header announces the whole section under it, not just the next
        call.** `#360` cleared the flag on every real write, so a result row
        printed under a header retired that header's claim and the next
        database call read as unannounced. The guard sits on the gateway and
        fires per `fetch_all`/`execute`, so that rule demanded a printed label
        for all 118 of them, and the sweep supplied 32 new ones. Jan, 2026-08-16
        (`#372`): *"I did not asked you to ADD NEW HEADERS, I asked you to print
        PRECEEDING header!"* The header is the announcement; its rows are the
        answer to it, and the blank line under the last of them is where the
        section ends and the claim with it (see `_expire_header_at_section_end`).

        Read from the cursor rather than from the text: `#359` tried to classify
        the last printed line and could not tell a header from a data row, which
        is exactly the case that kept shipping (`#360`).

        Both counters matter. `_pending_newlines` holds the newlines still
        retractable for the TIMER footer, and `commit_pending()` moves them to
        `_committed_trailing_newlines` without moving the cursor back up, so a
        row that closed and then flushed would otherwise read as still open.

        **An open line announces the work that will CLOSE it.** A redrawable row
        holds the cursor mid-line whatever it says, so `ut` covered a 9.9 second
        coverage read with a bar reading `100%  0:00:00` (`#379`, and see
        `shared/announce.py`). `mark_finished()` is how such a row says so.
        """
        if not self.had_output:
            return False
        if self._announced_header:
            return True
        if self._open_line_finished:
            return False
        return not self._pending_newlines and not self._committed_trailing_newlines

    def mark_finished(self) -> None:
        """The open line has finished its own work (shared/announce.py).

        Only a redrawable row needs it: every other open line is closed by the
        result that answers it, and a closed line already reads as a result.
        """
        self._open_line_finished = True

    def mark_announced(self) -> None:
        """Record a header as the newest thing on screen (shared/announce.py).

        The section starts empty: whatever stood under the previous header is
        not this one's body, and the blank a header lays down before its first
        row must not read as that row.
        """
        self._announced_header = True
        self._section_has_body = False

    def _expire_header_at_section_end(self) -> None:
        """A blank line under a printed row closes the section, and the claim.

        Two trailing newlines mean a line ended and an empty one followed, which
        is the console's only punctuation for "that subject is finished". Read
        from the counters rather than from the text, for the reason the property
        above gives, and both are summed because `commit_pending()` moves them
        from one to the other: a section that flushed its own trailing blank
        through `print_adt_table` holds it in `_committed_trailing_newlines` and
        nowhere else.

        **The body flag is what tells the two blanks apart.** A header may print
        its own blank before the first row -- `EXPORTING <n> OBJECTS:` does, and
        the DBMS_METADATA setup and comment pre-read that follow it print
        nothing, so that header is all the announcement they get. A blank there
        opens the section; a blank after a row closes it.

        Without any of this the latch was write-once: `mark_announced()` set it,
        the banner every command opens with goes through `print_adt_header`, and
        so `announced` answered `True` from the first line of every run and
        `AnnouncedGateway.guard()` could never fire again (`#372`, inert from
        `d30f088` until this commit).
        """
        if not self._section_has_body:
            return
        if len(self._pending_newlines) + self._committed_trailing_newlines >= 2:
            self._announced_header = False

    def write(self, text: str) -> int:
        if not text:
            return 0
        self.had_output = True
        stripped = text.rstrip("\n")
        if not stripped:
            self._pending_newlines += text
            self._expire_header_at_section_end()
            return len(text)

        trailing_count = len(text) - len(stripped)
        body = text[:-trailing_count] if trailing_count else text
        self._section_has_body = True
        # Whatever a finished row was claiming, this write replaces it: a bar
        # that starts moving again is a new wait with its own open line.
        self._open_line_finished = False
        self._flush_pending()
        self.wrapped.write(body)
        # Flush the visible body immediately. A header line printed right before a
        # long silent operation (e.g. rebuild's per-commit hashing) carries its
        # line-ending newline in _pending_newlines, so without this flush the body
        # stays in the TTY line buffer, invisible until the first progress line
        # commits the pending newline. Flushing only the already-written body keeps
        # the trailing newlines retractable for the shared footer normalizer.
        self.wrapped.flush()
        self._committed_trailing_newlines = 0
        self._pending_newlines = "\n" * trailing_count
        self._expire_header_at_section_end()
        return len(text)

    def normalize_trailing_newlines(self, count: int) -> None:
        # Already-committed trailing newlines count toward the total in BOTH
        # cases. This used to ignore them whenever anything was still pending,
        # so a section that committed its own trailing blank through
        # commit_pending() (print_adt_table does, to flush the table) and
        # then printed one more blank got the committed pair *underneath* the
        # normalized three: `patch -patch 65` printed four empty lines before
        # TIMER where the contract allows two (ADT #269).
        #
        # write() resets the committed count to 0 on any real body text, so
        # outside that window the two branches were always equal anyway, the
        # split was the bug, not a distinction worth keeping.
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
