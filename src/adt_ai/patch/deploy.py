from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from adt_ai.patch import queries, settings
from adt_ai.patch.deploy_progress import _COUNTABLE_ECHO_RE
from adt_ai.patch.layout import (
    database_object_name as _database_object_name,
)
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.layout import (
    database_schema as _database_schema,
)
from adt_ai.patch.layout import (
    deploy_log_folder as _deploy_log_folder,
)
from adt_ai.patch.models import (
    DeploymentPlanItem,
    DeploymentResult,
    PatchError,
    ViewMismatch,
)
from adt_ai.recompile.queries import build_compile_statement
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import PatchFolder, patch_id
from adt_ai.shared.row_values import row_value

# `row_value` since ADT #474 row G: `_row_get` here took a varargs key list and
# every call site passed one key in both cases, which the shared reader already
# answered. What it also did was fold the row's own keys, so a driver spelling a
# column `Object_Type` resolved; that fold moved into `row_value` with it, which
# is what makes this a deletion rather than a lost behaviour.


def _is_exact_patch_ref(folder: PatchFolder, ref: str) -> bool:
    """Does ``ref`` name THIS folder, rather than merely occur inside its name?

    The three spellings a user may select by, each compared whole: the full folder
    name (``#285``), the ``patch_code`` component after the ``yymmdd-seq-`` prefix
    (which is what old ADT compared (`patch.py:736`)) and the card id carried in
    that code (``#268``).
    """
    needle = ref.strip().upper()
    if not needle:
        return False
    if needle.isdigit():
        found = patch_id(folder.patch_code)
        return found is not None and int(found) == int(needle)
    return needle in (folder.folder.upper(), folder.patch_code.upper())


def _select_patch_folder(folders: list[PatchFolder], ref: str | None) -> PatchFolder:
    """The one patch folder ``ref`` names, or a refusal naming the candidates.

    ``folders`` arrives SUBSTRING-filtered and newest-first, and taking
    ``folders[0]`` off it deployed the wrong patch: `-patch
    260809-1-66_LAYER0_FIX` is a strict prefix of `260809-1-66_LAYER0_FIXTURES`,
    which sorts later, so the requested folder lost to the one that merely
    contained its name, and the run still reported `SUCCESS` (ADT #255, observed
    on a live DEV environment 2026-08-09).

    Selection is therefore EXACT, restoring old ADT's behaviour of building the
    path rather than searching for it, and an ambiguous ref raises instead of
    picking: old ADT silently took the most recent match (`patch.py:285-289`), and
    a deploy that applies a source nobody chose is silent by construction.
    Substring matching keeps its old-ADT home OUTSIDE selection, the
    `show_matching_patches` listing filter (`patch.py:326`), i.e.
    ``discover_patch_folders``, which is untouched.
    """
    if not ref:
        # No ref names no folder, so there is nothing to be exact about: the
        # newest patch is the selection, as before.
        if folders:
            return folders[0]
        raise PatchError("no patch folder found")
    exact = [folder for folder in folders if _is_exact_patch_ref(folder, ref)]
    if len(exact) == 1:
        return exact[0]
    if not folders:
        raise PatchError(f"no patch folder found matching {ref!r}")
    def names(selection: list[PatchFolder]) -> str:
        return ", ".join(folder.folder for folder in selection if folder.folder)

    if len(exact) > 1:
        # The ambiguous set only, a folder that merely CONTAINS the ref is not
        # one of the things being chosen between, and listing it here would send
        # the reader after a folder that was never in the running.
        raise PatchError(
            f"{ref!r} matches more than one patch folder: {names(exact)} "
            "- name one of them exactly"
        )
    raise PatchError(
        f"{ref!r} names no patch folder exactly, it only occurs inside: {names(folders)} "
        "- name a full folder name, its patch code, or its id"
    )

def _deployment_group(name: str, config: dict[str, Any] | None) -> str:
    """The group a generated script's own filename names.

    Read through `patch_group_file` since ADT #431, because that key moves the
    name the writer produced and this is its reader. They were not moved together
    at first: `install_APP_OWNER.sql` reported its schema as `INSTALL_APP_OWNER`,
    caught by the live smoke rather than by the suite. `None` keeps the old stem
    reading, which is what the default template resolves to anyway.
    """
    if config is None:
        return Path(name).stem
    return settings.group_from_script_name(name, config)

def _deployment_schema(name: str, config: dict[str, Any] | None = None) -> str:
    return _deployment_group(name, config).split(".", 1)[0].upper()

def _deployment_app_id(name: str, config: dict[str, Any] | None = None) -> int | None:
    parts = _deployment_group(name, config).split(".", 1)
    return int(parts[1]) if len(parts) == 2 and parts[1].isdigit() else None

def _skipped_deployment_result(item: DeploymentPlanItem) -> DeploymentResult:
    return _unrun_deployment_result(item, "SKIPPED")

def _not_deployed_result(item: DeploymentPlanItem) -> DeploymentResult:
    """A script the run never started, because an earlier one failed.

    Before ADT #254 these were simply absent from the report, so a two-schema patch
    that died on the first read as a one-schema patch, the run looked smaller than
    it was instead of unfinished.

    Spelled `NOT RUN` since ADT #284, not `NOT DEPLOYED`: the streamed table has to
    reserve the widest status before the first script runs, so twelve characters
    here widened STATUS on every deploy to describe a state only a failed one ever
    reaches. Seven fits old ADT's column (patch.py:517) and keeps the distinction
    from SKIPPED intact, SKIPPED is a target already at SUCCESS, NOT RUN is a
    script an earlier failure cut off.
    """
    return _unrun_deployment_result(item, "NOT RUN")

def _unrun_deployment_result(item: DeploymentPlanItem, status: str) -> DeploymentResult:
    return DeploymentResult(
        order   = item.order,
        file    = item.file,
        schema  = item.schema,
        app_id  = item.app_id,
        files   = item.files,
        commits = item.commits,
        status  = status,
        log_path= None,
    )

def _deployment_payload(path: Path, *, continue_on_error: bool) -> str:
    """The install script as SQLcl receives it: session defaults, then the script.

    The directives are prepended here and not merely trusted to be in the file,
    because `-deploy` replays whatever install script is already on disk while
    `-create` is what learned to emit them (ADT #254). Any folder built before
    that (or by old ADT, or by hand) deployed with DEFINE ON, and the first
    `&` inside an exported package body killed the run: SQLcl prompts for the
    substitution variable, `stdin=DEVNULL` gives it nowhere to read an answer,
    and the transcript ends on `Substitution cancelled` and a JLine exception
    with no ORA code for `WHENEVER SQLERROR` to trap. That is exactly how a live
    `patch/260809-1-APP65` died on `'&APP_ID.'` in `app_bkg_vessel.sql`
    (ADT #283); its install script carried no session directives at all, and
    `WHENEVER SQLERROR EXIT ROLLBACK` could not see a client-side prompt.

    They go at the very top, so the script's own copy and the project's
    `db_init` template still run afterwards and still win, the override
    contract `SESSION_DEFAULT_DIRECTIVES` documents in `patch/queries/objects.py`.
    """
    payload = [
        queries.SQLERROR_CONTINUE_DIRECTIVE
        if continue_on_error
        else queries.SQLERROR_EXIT_ROLLBACK_DIRECTIVE,
        *queries.SESSION_DEFAULT_DIRECTIVES,
    ]
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        if continue_on_error and stripped.upper().startswith(queries.WHENEVER_PREFIX):
            # The script carries `EXIT ROLLBACK` as its safe standalone default
            # (ADT #258). Left in, it would override the CONTINUE directive
            # prepended above and make `-continue` do nothing.
            continue
        if stripped.upper().startswith("PROMPT --"):
            stripped = f"PROMPT {stripped[9:].lstrip()}"
        payload.append(stripped)
    return "\n".join(payload) + "\n"

# SQLcl reports a refused statement as a block: the `Error starting at line`
# header, the statement it tried, then an `Error report -` stanza carrying the
# ORA code. Some failures never open that block at all, ADT #270's SP2-0556 is
# a single line with no header, and those runs fail on the *absence* of the
# success marker instead, so keying the excerpt to the block alone would render
# nothing for exactly the failure that motivated the logs-folder fix.
_ERROR_BLOCK_PREFIX = "Error starting at line"
_ERROR_CODE_RE = re.compile(r"\b(?:ORA|PLS|SP2|TNS|DPI|DPY)-\d{3,5}\b")
_ERROR_BLOCK_MAX_LINES = 12
_TAIL_FALLBACK_LINES = 12
_TAIL_LABEL = "(no error code in the output, last lines of the transcript)"

def _deployment_error_excerpt(output: str, *, max_lines: int = 40) -> list[str]:
    """The lines of the transcript that say why the deploy failed.

    Bounded on purpose (ADT #272): a deploy transcript runs to thousands of lines
    and the console is not where it belongs, the full text is already spooled to
    the log, so this is the pointer, not the copy. ``[]`` for a clean run.

    Two passes, and the second one is why this function is not a list of patterns.
    A recognised failure (an `Error starting at line` block, or a bare ORA/PLS/SP2
    code) yields its own lines. Anything else falls back to the **tail** of the
    transcript, because whatever killed the run is at the end of the output
    whatever it happens to be called.

    That fallback is the fix for the deploy this card was filed about. The first
    pass shipped keyed to error codes alone, verified against transcripts its
    author invented, and Jan's real one carried no code at all: SQLcl hit
    ``'&APP_ID.'`` in a linked package, prompted for a substitution variable with
    nowhere to read an answer from, and died on a JLine exception. The stanza
    rendered "no error text" for the exact run it exists to explain. SQLcl, JDBC,
    the JVM and the OS each fail in their own vocabulary, so a code list can never
    be complete, position is the only property every failure shares.
    """
    lines = output.splitlines()
    wanted: set[int] = set()
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(_ERROR_BLOCK_PREFIX):
            for offset in range(index, min(index + _ERROR_BLOCK_MAX_LINES, len(lines))):
                # A progress marker means the block ended and the next file
                # started; never swallow the run that followed the failure.
                if offset > index and _COUNTABLE_ECHO_RE.search(lines[offset]):
                    break
                wanted.add(offset)
        elif _ERROR_CODE_RE.search(stripped):
            wanted.add(index)
    if wanted:
        ordered = sorted(wanted)
        excerpt = [lines[index].rstrip() for index in ordered[:max_lines]]
        dropped = len(ordered) - max_lines
        if dropped > 0:
            excerpt.append(f"... truncated, {dropped} more error line(s) in the log")
        return excerpt
    if _deployment_succeeded(output):
        return []
    # Blank padding is not evidence: a cancelled SQLcl prompt leaves several
    # whitespace-only lines right where the diagnosis should be, and they would
    # otherwise crowd out the real text within the tail budget.
    body = [line.rstrip() for line in lines if line.strip()]
    if not body:
        return []
    return [_TAIL_LABEL, *body[-min(_TAIL_FALLBACK_LINES, max_lines):]]

# A diagnostic Oracle or SQL*Plus emitted, anywhere in the transcript (ADT #312).
# The success marker is a `PROMPT` in the install script, so SQLcl echoes it
# CLIENT-SIDE whether or not the statements above it worked, a mid-script error
# that did not abort the run still ends the transcript on `-- SUCCESS`. Scanning
# only for `Error starting at line` saw one of the three shapes: SP2- opens no
# error block at all (ADT #270), and a bare ORA- line need not open one either.
#
# Anchored at line start, which is the whole of the false-positive defence: an
# error CODE quoted mid-line is script text, a comment, a PROMPT, a
# raise_application_error mapping, while a line that BEGINS with the signature
# is Oracle speaking. Old ADT used exactly this regex (jkvetina/ADT#3).
#
# `Errors for ` is the compile half (ADT #559), and it is a HEADING rather than a
# code because the code is out of reach: a package body that fails to compile
# prints `Package Body <NAME> compiled` with no `Warning:` prefix, and SHOW
# ERRORS then indents the `PLS-00201` under an `Errors for PACKAGE BODY <NAME>:`
# heading, so an anchored `^PLS-` alternative matches nothing. The database left
# the object INVALID and the transcript still ended `SUCCESS`, exit 0, which is
# the defect this alternative closes. Captured evidence, never a remembered
# spelling: `tests/fixtures/deploy_transcripts/`.
_DEPLOYMENT_ERROR_RE = re.compile(
    r"(?m)^(?:SP2-\d{4}:|ORA-\d{5}:|Error starting at line|Errors for )"
)

def _deployment_succeeded(output: str) -> bool:
    if _DEPLOYMENT_ERROR_RE.search(output):
        return False
    tail = output.splitlines()[-10:]
    success_markers = {"-- SUCCESS", "PROMPT -- SUCCESS", "SUCCESS"}
    return any(line.strip().upper() in success_markers for line in tail)

def _spool_path(config: dict[str, Any], folder: Path, target_env: str, file: str) -> Path:
    log_folder = folder / _deploy_log_folder(config, target_env)
    return log_folder / f"{Path(file).stem}.log"


def reset_deployment_spool(
    config: dict[str, Any],
    folder: Path,
    target_env: str,
    file: str,
) -> None:
    """Clear a spool file an earlier, interrupted deploy left behind.

    The install script's own SPOOL directive opens in APPEND mode
    (`SPOOL_START_DIRECTIVE`), so a run that never reached its `WHENEVER
    OSERROR EXIT ROLLBACK` cleanup leaves this path on disk. The next run's
    SQLcl reopens it and appends after the leftover content, and
    `_write_deployment_log` renames whatever it finds there as this run's own
    log -- folding a prior run's output into a correctly-named log that is
    supposed to record only this one. Removing any existing spool file before
    SQLcl opens it keeps one run to one log file.
    """
    _spool_path(config, folder, target_env, file).unlink(missing_ok=True)


def _write_deployment_log(
    config: dict[str, Any],
    folder: Path,
    target_env: str,
    file: str,
    status: str,
    output: str,
    *,
    retain_output: bool = False,
) -> Path:
    log_folder = folder / _deploy_log_folder(config, target_env)
    log_folder.mkdir(parents=True, exist_ok=True)
    log_path = log_folder / settings.deploy_log_name(
        config,
        moment = datetime.now(),
        stem   = Path(file).stem,
        status = status,
    )
    spool_path = _spool_path(config, folder, target_env, file)
    if spool_path.exists():
        # The script's own SPOOL already wrote this session; stamp the outcome
        # into its name instead of writing a second copy beside it, old ADT's
        # shape (patch.py:585-589), and what keeps one run to one log file.
        spool_path.replace(log_path)
        if retain_output:
            spooled = log_path.read_text(encoding="utf-8", errors="replace")
            if output.strip() not in spooled:
                text_files.write_text(log_path, spooled.rstrip() + "\n" + output)
    else:
        text_files.write_text(log_path, output)
    return log_path

def _invalid_objects(gateways: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Every invalid object in each touched schema, as `(schema, type, name)`.

    Named because it is asked TWICE: once for the recompile's own worklist and
    once after the pass, to see what the pass did not fix (`#658`). One reader
    for one question, so the second call cannot drift from the first.
    """
    found: list[tuple[str, str, str]] = []
    for schema, gateway in sorted(gateways.items()):
        for row in gateway.fetch_all(queries.INVALID_OBJECTS_QUERY):
            object_type = str(row_value(row, "OBJECT_TYPE") or "").upper()
            object_name = str(row_value(row, "OBJECT_NAME") or "").upper()
            if not object_type or not object_name:
                continue
            found.append((schema, object_type, object_name))
    return found


def _recompile_invalid_objects(gateways: dict[str, Any]) -> list[tuple[str, str, str]]:
    """One `ALTER ... COMPILE` per invalid object, returning what was attempted.

    Attempted, not fixed. `ALTER ... COMPILE` on a body whose dependency is still
    missing leaves the object invalid and reports it only through `USER_OBJECTS`,
    so this list has never been an outcome and `docs/patch_deploy.md` read as if
    it were. The caller re-reads `_invalid_objects` afterwards for the half that
    is (`#658`).
    """
    invalid = _invalid_objects(gateways)
    for schema, object_type, object_name in invalid:
        gateways[schema].execute(_compile_statement(object_type, object_name))
    return invalid

def _compile_statement(object_type: str, object_name: str) -> str:
    return build_compile_statement(object_type, object_name)

def _verify_view_columns(
    files: list[str],
    config: dict[str, Any],
    gateways: dict[str, Any],
    dev_gateway_factory: Callable[[str], Any],
) -> list[ViewMismatch]:
    mismatches: list[ViewMismatch] = []
    for file in files:
        if _database_object_type(file, config) != "VIEW":
            continue
        schema = _database_schema(file, config)
        target_gateway = gateways.get(schema)
        if target_gateway is None:
            continue
        # One reader for "which object is this file" (ADT #471). `VIEW` carries a
        # bare `.sql` in the shipped config, so `Path.stem` answered correctly
        # here; a project configuring a compound extension for it would not have
        # been so lucky, and there is no reason for a second spelling.
        view = _database_object_name(file, config)
        # defensive: `file` already resolved a non-None VIEW type off the same `object_types`
        # layout, so `_database_object_name` cannot itself resolve empty
        if not view:  # pragma: no cover
            continue
        expected = _view_column_names(dev_gateway_factory(schema), view)
        actual = _view_column_names(target_gateway, view)
        if expected != actual:
            mismatches.append(
                {
                    "schema": schema,
                    "view": view,
                    "expected": expected,
                    "actual": actual,
                }
            )
    return mismatches

def _view_column_names(gateway: Any, view: str) -> list[str]:
    rows = gateway.fetch_all(queries.VIEW_COLUMNS_QUERY, {"view_name": view})
    ordered = sorted(rows, key=lambda row: int(row_value(row, "COLUMN_ID") or 0))
    return [
        str(row_value(row, "COLUMN_NAME") or "").upper()
        for row in ordered
        if row_value(row, "COLUMN_NAME")
    ]
