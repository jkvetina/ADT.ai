"""The console shape of `-create`'s per-schema report (ADT #276 / #277).

`patch/report.py` decides WHAT each row says; this decides how it looks, and the
split is why the arithmetic can be tested without a console and the layout without
a git fixture.

Old ADT's one width that carries meaning is reproduced exactly: the marker column
lands on 72 (patch.py:1626), so a `[NEW]` or `[ALT:n]` reads down a column rather
than trailing whatever the path length happened to be (SOP §Legacy parity).

`PROCESSING SCHEMA <s>:` is gone (ADT #444). It stood over `PROCESSED FILES:
<s>`, naming the same schema one line above the section that names it, and
`-deploy` opens each schema's block with a single header already.

**Every warning is a section of its own** (ADT #451), and the six of them live in
`patch_create_warnings.py` since `#465` split this module at the 20 KB context
guard. The seam is that card's own: a warning family with one shape and one
printer, distinct from the report it hangs off.

**A row carries no trailing marker** (ADT #465). `[ALT:n]` and `[DELETED]` became
`ALTER STATEMENTS:` and `DELETED OBJECTS:` above the list, `[NEW]` was dropped,
and the dot column they were padded into went with them. Jan, 2026-08-21: *"extra
flags after dots instead of WARNING sections ... create a sections dedicated for
alter statements and deleted objects"*.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from adt_ai.cli.constants import (
    PatchWorkspace,
    folder_commit_entries,
    outstanding_records,
    preview_rows,
    preview_rows_from,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli.context import _project_relative
from adt_ai.cli.patch_create_warnings import (
    print_changed_objects,
    print_no_database_clock,
    print_outdated,
    print_script_warnings,
    print_uncommitted,
    print_unresolved_tables,
)
from adt_ai.cli.patch_preview_render import (
    RECENT_COMMITS_HEADER,
    RELEVANT_COMMITS_HEADER,
    patch_show_commits,
)
from adt_ai.patch.models import DatabasePatchResult, SchemaReport
from adt_ai.patch.object_folders import object_folder_resolver
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.file_list import nested_files, print_file_rows
from adt_ai.shared.object_list import print_object_rows
from adt_ai.shared.patch_folders import PatchFolder
from adt_ai.shared.progress import schema_label

# `MARKER_COLUMN = 72` stood here (old ADT patch.py:1626) until ADT #465 took the
# trailing markers off the rows entirely. It is worth recording why the column
# was never the good idea it looked like: the padding was
# `max(72 - len(path) - len(marker), 1)`, so a path over about 56 characters
# collapsed the dot run to one and pushed its own marker PAST the column every
# shorter row had lined up on. The alignment it existed for was the first thing
# a real project path broke.

# `ROW_MARKER = "-"` stood here until ADT #504 gave every file list one renderer.
# The dash is `shared/file_list.ROW_MARKER` now, beside the indent it is spelled
# with: a marker constant this module owned could only ever be the marker THIS
# module printed, which is how fifteen sections came to agree by hand.

# The two object-change sections `#465` split out of the row markers. Not
# warnings: an ALTER and a DROP are what the patch was asked to do, so they get
# plain headers rather than the `WARNING - ` family, and unlike that family they
# close themselves (see `_print_object_changes`).
ALTER_HEADER = "ALTER STATEMENTS:"
DELETED_HEADER = "DELETED OBJECTS:"


def print_folder_commits(folder: PatchFolder) -> None:
    """One patch's own commits, from the `-- COMMITS:` header it carries.

    The third reader of that header (ADT #417), beside `-deploy` and the build
    listing below. This one exists because a `-patch <CODE>` LOOK run whose scan
    matched nothing returns before the preview is ever reached, so the screen
    answered "what is in this patch" with its files and never its commits, which
    is the half the reader asked for. Measured on a real patch: `-patch
    APP112_EVENT_TYPE_WIDEN` printed `PATCH CONTENTS:` alone while the folder's
    header recorded commit 254 the whole time.

    Silent for a folder that records none, rather than a header over an empty
    table.
    """
    entries = folder_commit_entries(folder)
    if not entries:
        return
    # One empty line, the plain default (ADT #451). `#444` gave it two, reading
    # Jan's *"RELEVANT COMMITS: section does not have 2 empty line in between
    # UPDATING DEPENDENCIES: section (which is not printed every time)"* as a
    # property of the header rather than of the one section that had been sitting
    # above it, and made it unconditional on the argument that a gap depending on
    # what came before is a gap that moves. That is the reading he rejected,
    # 2026-08-21: *"you are printing 2 new lines above RELEVANT COMMITS: anytime,
    # even when there is no dependency update! It sucks, the looks of headers
    # must be consistent!"* Consistency was the goal both times; what changed is
    # which shape it means, and every other section on this screen opens on one.
    print_adt_header(RELEVANT_COMMITS_HEADER)
    print_adt_table(preview_rows_from(entries), columns=["#", "MESSAGE"])


def print_create_commit_listings(
    workspace: PatchWorkspace,
    config: dict[str, object],
    result: DatabasePatchResult,
    records: list[CommitRecord],
) -> None:
    """The two commit tables a finished `-create` closes with (ADT #417).

    The patch's own commits, then the commits still to be addressed. Both
    headers already existed and neither is minted here: `-deploy` prints
    `RELEVANT COMMITS:` over these same entries, and `RECENT COMMITS:` is what
    `_preview_header` returns when nothing narrowed a listing.

    The patch's commits are read back off the folder this run just wrote rather
    than re-derived from ``records``: that header is what a later `-deploy` will
    read, so listing anything else here would let two screens disagree about one
    patch. A folder the discovery pass cannot see yet, or one carrying no header
    rows, simply prints no first table.
    """
    folders = workspace.discover()
    built = next(
        (folder for folder in folders if folder.folder == result.folder.name),
        None,
    )
    if built:
        print_folder_commits(built)
    outstanding = outstanding_records(records, folders)
    if not outstanding:
        # Every commit in the window is now carried by a patch, which is a real
        # answer and the one this build was working towards. An empty table would
        # be a header and a column rule over nothing.
        return
    print_adt_header(RECENT_COMMITS_HEADER)
    print_adt_table(preview_rows(outstanding, patch_show_commits(config)))


def print_create_screen(
    workspace: PatchWorkspace,
    config: dict[str, object],
    result: DatabasePatchResult,
    records: list[CommitRecord],
    root: Path,
) -> None:
    """The finished `-create` screen, in the order Jan reads it (ADT #443).

    Commits, then one block per schema, then the artifact. Old ADT closed with
    the commit tables and ADT.ai inherited that, which reads backwards: the
    commits are the INPUT a reviewer checks before looking at what the build made
    of them. Jan, 2026-08-21: *"before you even print PROCESSING SCHEMA section I
    want to see RELEVANT COMMITS section"*.

    `RECENT COMMITS:` travels with `RELEVANT COMMITS:` rather than staying behind:
    they are one pair from one function since `#417`, and `PATCH FILES:` has to be
    the closing section, so leaving the outstanding table below would have
    stranded it between the schema blocks and an artifact it says nothing about.

    Assembling the screen here rather than in `commands_patch` is what `#443`
    fixed structurally: that module is the command's control flow and was 400
    bytes over the 20 KB context guard with the order spelled inline, while this
    module already owned every section the order arranges.
    """
    print_create_commit_listings(workspace, config, result, records)
    print_create_report(result, config)
    # Two empty lines above it, like every other header on the screen, and asked
    # for by name: Jan, `#443`, *"a dedicated section at the bottom (with 2 empty
    # lines above it) where you will list these schema driving files in 1 list"*.
    # It took a `lead_gap=True` argument to get them until `#468`, and that
    # argument is what made this one call render THREE whenever the section above
    # it had closed itself with a blank. The renderer normalizes the gap now, so
    # the wider spacing this section wanted is what every section has.
    print_adt_header("PATCH FILES:")
    # A plain list of project-relative paths, nothing else (ADT #415). Rows
    # arrive sorted by group (`_write_patch_files` iterates `sorted(...)`), so
    # reading the values without their key changes no order.
    #
    # **The one section that does NOT group**, asked for by name (ADT #507):
    # *"Create exception for PATCH FILES: and keep whole filenames on a single
    # line"*. `#504` grouped it under the patch folder, which reads as one folder
    # line and one file under it; `#507` splits a directory per row, which would
    # have made three rows of a section whose whole content is one path per
    # schema. That path IS the answer here, so it stays on one line and `#415`'s
    # shape comes back exactly. `nested=False` is the renderer's own flag, so this
    # is an argument at the call site and not a second way to build a row.
    print_file_rows(
        [_project_relative(path, root) for path in result.sql_files.values()],
        nested = False,
    )
    print()


def print_create_report(result: DatabasePatchResult, config: dict[str, object]) -> None:
    # `config` arrived with ADT #504: every section below lists file paths, and
    # whether they group under their folder is a project setting (`nested_files`)
    # read against a project layout (`path_objects`). Threaded rather than read
    # from a module global, so a test can render either shape without patching.
    #
    # No blank line is emitted between sections here: `print_adt_header` opens
    # with one, and adding a second is the per-call spacing override the console
    # contract forbids, the same one that put four empty lines before `TIMER`
    # in ADT #269.
    nested = nested_files(config)
    folder_of = object_folder_resolver(config)
    for report in result.reports:
        # No `PROCESSING SCHEMA <s>:` header. `#443` took its object count off,
        # and `#444` took the line itself: it stood over `PROCESSED FILES: <s>`,
        # naming the same schema one line above the section that names it. Jan,
        # 2026-08-21: *"it is redundant when we are printing PROCESSED FILES:
        # APP_OWNER for each schema and it will be more aligned with -deploy
        # mode"* - `-deploy` opens each schema's block with `PATCH CONTENTS: <s>`
        # and nothing above it, so a build opening with `PROCESSED FILES: <s>` is
        # the same shape read the same way. `SchemaReport.object_count` stays on
        # the model: its arithmetic is asserted by
        # `tests/patch/test_create_processing_report.py` and it is what separates
        # a schema's own object files from the templates and scripts `files` also
        # carries. The APEX group went with it, `PROCESSED FILES:` appends
        # `<schema>.<app_id>`, so which app a block belongs to is still on screen.
        _print_object_changes(report, nested, folder_of)
        print_adt_header("PROCESSED FILES:", schema_label(report.schema_label))
        # File rows and nothing else (ADT #451). `#277`'s newer-commit block used
        # to hang under the row it belonged to, which is the shape Jan read back
        # on 2026-08-21: *"PROCESSED FILES list the files, no nested warnings
        # here"*. It is not lost, `print_outdated` prints the same two
        # facts under a header that says what they are.
        #
        # `carried_files`, not `files`: a dropped path is reported by `DELETED
        # OBJECTS:` two headers above and printed in both places until ADT #511.
        # The split lives on the model beside the deleted listings it is the
        # complement of, so this section reads one list and filters nothing.
        print_file_rows(
            [item.path for item in report.carried_files],
            nested    = nested,
            folder_of = folder_of,
        )
        print_outdated(report, config)
        print_uncommitted(report, config)
    # The run-scoped warnings, after the per-schema loop, because each is about
    # the patch rather than about one schema's block. `OBJECTS CHANGED:` leads
    # them: it is the only one that says the patch will ship something OTHER than
    # what the database holds, which is a bigger claim than an unresolved table
    # version or an unmoved per-patch script (ADT #468).
    print_changed_objects(result)
    print_no_database_clock(result)
    print_unresolved_tables(result, config)
    print_script_warnings(result, config)


def _print_object_changes(
    report: SchemaReport, nested: bool, folder_of: Callable[[str], str | None]
) -> None:
    """What this patch DOES to objects, before the list of what it carries.

    Two sections, both of them Jan's on 2026-08-21 (ADT #465): *"create a
    sections dedicated for alter statements and deleted objects"*. They replace
    the `[ALT:n]` and `[DELETED]` brackets that used to trail the rows in
    `PROCESSED FILES:` below, so a fact that needed decoding from a bracket is
    now a header that says it in words.

    `ALTER STATEMENTS:` is `TABLE CHANGES DETECTED:` renamed, same rows, the
    generated `tables_after/*.sql` helpers. Jan settled the rename on chips the
    same day; the old header said what ADT noticed rather than what it wrote.

    **Each block closes with its own blank**, which `TABLE CHANGES DETECTED:`
    never did: `print_adt_header` opens a section with one blank, so a block that
    also writes one is separated from the next by two, and that is what every
    other pair of sections on this screen shows (`_print_patch_files` and
    `_print_connection_versions` both do it). Jan, same run: *"missing blank line
    below TABLE CHANGES DETECTED section (2 blank lines rule)"*.

    The two sections stopped sharing a loop in ADT #506, when the second stopped
    listing paths. `ALTER STATEMENTS:` genuinely lists FILES, the generated
    helpers a reviewer opens; `DELETED OBJECTS:` lists objects and says so.
    """
    if report.alter_files:
        print_adt_header(ALTER_HEADER)
        print_file_rows(report.alter_files, nested=nested, folder_of=folder_of)
        print()
    _print_deleted_objects(report, nested, folder_of)


def _print_deleted_objects(
    report: SchemaReport, nested: bool, folder_of: Callable[[str], str | None]
) -> None:
    """`DELETED OBJECTS:`, printing objects rather than paths (ADT #506).

    The header has said OBJECTS since `#465` while the rows said files, which is
    the half Jan read back on 2026-08-24 against an `export_db` run printing the
    same things as `TYPE | NAME`. `export_db` has a section under this exact
    name and it lists objects, so one header was being spelled two ways by two
    commands on the same screen.

    A dropped path holding no database object, a per-patch script or a template,
    has no type or name to render and keeps its plain path row underneath: it is
    genuinely gone, and this section is the only place the run says so. Those
    rows go through `#504`'s file renderer, so the one section printing both
    shapes still has one spelling of each.
    """
    if not report.deleted_objects and not report.deleted_scripts:
        return
    print_adt_header(DELETED_HEADER)
    print_object_rows(report.deleted_objects)
    if report.deleted_scripts:
        print_file_rows(report.deleted_scripts, nested=nested, folder_of=folder_of)
    print()


# `_schema_header` stood here until ADT #444 removed the `PROCESSING SCHEMA <s>:`
# section it built. Deleted rather than left unreferenced: the module docstring's
# note about old ADT's `APP_OWNERAPP 100:` formatting defect went with it, that
# being a divergence from a string neither tool prints any more.


# `_file_row` stood here until ADT #504 routed every list through
# `shared/file_list.py`. Two facts it recorded outlive it and belong with the
# rows it used to build.
#
# **One leading character for every row** (`#456`). Old ADT split them three
# ways, `-` for an object file shipped from a commit, `>` for an injected
# template or script that carries one, `!` for anything with no commit behind it,
# and ADT.ai inherited that. Jan, 2026-08-21: *"in processed files you print some
# files with '-' and some with '!', I asked to print all with '-'"*. That is now
# a property of the shared renderer, so it cannot come back one section at a time.
#
# **Nothing trails a row either**, since `#465`. A row used to end in a dot run
# and a bracket, and Jan read that on his own screen the same day: *"extra flags
# after dots instead of WARNING sections, I asked you to remove all weird shit to
# warnings"*. Both markers that said something have a section of their own above.


# `_print_newer` stood here until ADT #451 moved its block into
# `WARNING - OUTDATED FILES:`. It drew `^`, one `NEW ....... n)` row per newer
# commit, a `CURRENT ... n)` row and a closing `--`, all indented under the file
# in the middle of `PROCESSED FILES:`. The dotted labels went with it: under a
# header that says the file is outdated, the newer commits need no word marking
# them as newer, and the one being shipped is named on the explanation line.
#
# `_commit_line` followed it to `patch_create_warnings.py` in `#465`: the one
# section that printed a nested commit row is the outdated warning, so the row
# builder belongs beside it rather than in the module that no longer calls it.


__all__ = [name for name in globals() if not name.startswith("__")]
