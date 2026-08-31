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
    # ADT #309 closed the set Jan postponed: `-refresh` -> `-contents` and
    # `-ref` dropped outright. Its third rename, `-hash` -> `-rollout`, was
    # reversed by `#447` and is off this list again.
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
    # ADT #447 withdrew `-rollout`/`-locked` and took `-hash` OFF this list, the
    # first time a name has come back: hash mode was rebuilt around one baseline
    # file, so the flag takes a FILE rather than a commit number and `-locked`
    # has nothing left to switch off.
    # ADT #592 folded `-fullapp` into `-app`, so the name renamed by `#292` is
    # itself now a removed name. It needs this entry for the reason `-full` did
    # and then some: `-fullapp` is no longer a prefix of anything the parser
    # declares, but `-app` IS a suffix nobody abbreviates to, and a removed name
    # belongs on the removed list whatever today's parser does with it.
    # ADT #598 withdrew `-fetch`, which is neither a rename nor a dead flag: the
    # behaviour stays and loses its own spelling, because the run that wants a
    # fetch is the run asking for the newest version of a file. `-head` does it
    # now, so the entry is what makes the old name fail loudly instead of being
    # read as an abbreviation of `-force` on some future Python.
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


# The console runtime's stream wrappers moved to `cli/stream_tracker.py` with
# ADT #494: this file was 171 bytes under the 20 000 byte context guard, and a
# stateful writer that decides where the cursor is never belonged beside the
# parser tables anyway. Re-exported rather than relocated at every call site,
# because `_StdoutTracker` is what `cli/runtime.py`, `cli/context.py` and four
# test files reach for by name and the underscore is not a hint to stop.
from adt_ai.cli.stream_tracker import (  # noqa: E402,F401 (re-exported for existing importers)
    _StderrTracker,
    _StdoutTracker,
)

__all__ = [name for name in globals() if not name.startswith("__") or name == "__version__"]
