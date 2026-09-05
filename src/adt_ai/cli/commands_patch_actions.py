"""The `patch` supporting actions: `-install` and `-archive`.

Split out of `commands_patch.py` when ADT #276 pushed it past the 20 KB context
guard (`tests/contracts/test_context_file_size.py`), the same call `#285` made
for `patch_preview_render.py` and `#273` for `patch_deploy_render.py`: a module
that crosses the guard is split, never registered as debt.

The seam is the parser's own: these are `SUPPORTING ACTIONS` (old ADT
patch.py:61-66). Each returns early with its own exit code and none of them
touches the commit scan, the content modes, or the patch build, which is what is
left behind in `commands_patch.py`.

The header listed `-deldiff` and `-refresh` here until ADT #361. Both were
withdrawn out from under it, `-refresh` by `#309` (renamed `-contents`, then
withdrawn by `#353`) and `-deldiff` by `#356`, so the first line of the module
named two actions it no longer holds while its own comment forty lines down
explained where one of them went.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from adt_ai.cli.constants import (
    PatchWorkspace,
    print_adt_header,
    print_adt_table,
)
from adt_ai.cli.context import _display, _project_relative
from adt_ai.patch.object_folders import object_folder_resolver
from adt_ai.patch.preview import (
    folder_preview_rows,
    folders_for_authors,
    folders_within_window,
)
from adt_ai.patch.staleness import require_fresh_dependency_graph
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.file_list import nested_files, print_file_rows
from adt_ai.shared.patch_folders import PatchFolder, named_patch_refs
from adt_ai.shared.progress import schema_label

#: The NARROWED folder listing: capped at `patch_show_patches` and cut down by
#: `-my`/`-by`/`-recent`, which is what makes RECENT true of it (ADT #510).
RECENT_PATCH_FOLDERS_HEADER = "RECENT PATCH FOLDERS:"

#: The `-archive` listing: every folder still on disk, no cap and no filter. A
#: separate string precisely because RECENT would be false of it, and a header
#: claiming "recent" over an unnarrowed table is how a reader stops being able to
#: tell the two apart, which is `_preview_header`'s own rule read the other way
#: round. Jan settled the split on chips, 2026-08-24.
ALL_PATCH_FOLDERS_HEADER = "ALL PATCH FOLDERS:"

#: The receipt over the folders a run TOOK. Since ADT #513 it stands over the
#: same `FOLDER | STATUS` rows as the two listings above, and it prints only when
#: the run named something, so it can never head an empty table.
ARCHIVING_PATCHES_HEADER = "ARCHIVING PATCHES:"

# `run_delete_diff_tables` lived here until ADT #356 withdrew `-deldiff`. It is
# gone rather than kept as a private helper: the sweep now runs from
# `commands_patch_deploy._sweep_diff_tables` and `export_db`, both through
# `shared/diff_tables.py`, and a second implementation reachable from nowhere is
# how two answers to "which tables are these" start drifting apart. Removing it
# also breaks the import cycle this module had with `commands_patch_deploy`,
# which is the module that now imports THIS one.


def run_install_script(root: Path, workspace: PatchWorkspace, config: dict[str, Any]) -> int:
    # The install order comes from config/internal/dependencies.db, so an absent or
    # out-of-date graph is refused before a single line is written, a
    # plausible-looking script in the wrong order is the failure this
    # prevents, and it only surfaces in SQLcl.
    require_fresh_dependency_graph(root, config)
    results = workspace.create_install_script(config)
    if not results:
        # Nothing found: name the layout that was searched so the reader can
        # act on it, instead of a section header standing over an empty table.
        print(f"No database objects found under: {config.get('path_objects') or 'database'}")
        print()
        return 0
    # One segment per schema, each in the same order: what went into the
    # script, then where the script landed. The script body is a file, not
    # console output, dumping it buries both facts.
    for result in results:
        # The schema folds into the phrase so the colon still closes the
        # line: `OBJECTS OVERVIEW: APP_OWNER` put the rule under the label
        # and left the schema hanging past it (ADT #237).
        suffix = f" FOR {schema_label(result.schema)}" if result.schema else ""
        print_adt_header(f"OBJECTS OVERVIEW{suffix}:")
        print_adt_table(
            [
                {"OBJECT TYPE": object_type, "FILES": count}
                for object_type, count in sorted(result.overview.items())
            ],
            columns = ["OBJECT TYPE", "FILES"],
        )
        print_adt_header(f"INSTALL SCRIPT{suffix}:")
        print(f"  {_project_relative(result.path, root)}")
        print()
    return 0


def print_patch_plan(
    workspace: PatchWorkspace,
    config: dict[str, Any],
    ref: str | None,
) -> None:
    """List the database files and APEX components a patch holds.

    This was the `-contents` action until ADT #353, and `-refresh` before `#309`.
    Jan, 2026-08-15: *"attr -contents should be also removed. What a stupidity,
    BUT when I am asking for specific patch like -patch ABC Then I want to see
    this there."* So it stopped being a mode and became what naming a patch
    means: `-patch`, `-create` and `-deploy` all print it, and no flag turns it
    on or off.

    The file listing Jan asked for in the same breath is this same section.
    `-files` never showed files, it filtered the commit list, so there was no
    listing to preserve here, only one to finally provide.

    Silent when the ref resolves to nothing: the caller has already said so in
    its own words (`NO PATCH MATCHED`), and an empty section under that would be
    noise standing where an answer should be.
    """
    plan = workspace.refresh_plan(ref=ref, config=config)
    if not plan.folder and not plan.database_files and not plan.apex_components:
        return
    # No `Patch folder:` line. `-contents` printed one when it was a standalone
    # action with nothing else on screen naming the folder; now this section
    # always sits under a header that already names the RESOLVED folder, so the
    # line would be the third spelling of one fact. Jan deleted its two siblings
    # for exactly that (`#274` `Next patch folder:`, `#284` `Patch folder:`).
    #
    # One section per schema since ADT #443, each named by the schema it will
    # deploy into and listing its files in the order the install script links
    # them. The flat single-header form could not answer either question on a
    # multi-schema patch: both schemas' files landed under one `PATCH CONTENTS:`
    # sorted alphabetically, so the screen mixed them together and put them in an
    # order the deploy does not use. Jan, 2026-08-21: *"so it would be clearly
    # visible which files you are going to process in which schema; and the order
    # should be as the order in the patch itself"*.
    #
    # The schema rides the header's `append` rather than a header string of its
    # own (`PROCESSED FILES: APP_OWNER`'s shape), so this mints no new console
    # string, which `tests/contracts/console_surface.txt` measures in both
    # directions.
    #
    # The rows group under their folder since ADT #504, through the one renderer
    # every file list uses. The install ORDER survives it: a folder is emitted
    # where it first appears and collects the files under it, so the sequence a
    # reader follows is unchanged and each folder is named once instead of on
    # every row.
    nested = nested_files(config)
    folder_of = object_folder_resolver(config)
    for group in plan.groups:
        print_adt_header("PATCH CONTENTS:", schema_label(group.label))
        print_file_rows(
            [_display(file) for file in group.files],
            nested    = nested,
            folder_of = folder_of,
        )
    if not plan.groups:
        # A folder whose install scripts could not be read still answers with
        # what its own header recorded, rather than printing nothing at all.
        print_adt_header("PATCH CONTENTS:")
        print_file_rows(
            [_display(file) for file in plan.database_files],
            nested    = nested,
            folder_of = folder_of,
        )
    for app_id, components in plan.apex_components.items():
        # Flat, and not a file list: an APEX component is named by its export
        # slug, so there is no folder to group it under (ADT #504).
        print(f"APP {app_id}")
        print_file_rows(components, nested=False)
    print()


def preview_folders(
    workspace: PatchWorkspace,
    records: list[CommitRecord],
    authors: list[str] | None = None,
    recent: int | float | None = None,
) -> list[PatchFolder]:
    """The patch folders a run's own filters leave standing (ADT #467).

    `-by`/`-my` and `-recent` reached the commit scan and stopped there, so the
    folder listing showed every patch on disk under a screen the run had
    narrowed. Jan, reporting it on 2026-08-22: *"I hope this was implemented in
    patch folders too!"*.

    One helper rather than the two filters spelled at each call site, because
    there are two sites and they must not drift: the bare `patch` listing, and the
    inventory a `-name` matching nothing prints instead of an answer. A run that
    narrowed by author is asking the same question in both places. The `-archive`
    listing is deliberately NOT one of them and does not come through here: that
    run returns before the commit scan, so there are no records to join against.

    ``authors`` and ``recent`` arrive already resolved, off the `PatchRequest` the
    caller built, rather than being re-derived from `args` here. `-my` costs a
    `git config` read and the answer is in hand by the time anything prints.

    ``records`` is the SCANNED set, before `records_for_header` drops what a patch
    already carries: that reduction answers "what is still outstanding", and
    feeding it here would take a folder's own commits out of the join that exists
    to attribute the folder.

    Lived in `patch_preview_render` until ADT #510 moved it beside the renderer it
    feeds, that module being 108 bytes under the 20 KB context guard with one
    section split across two files.
    """
    folders = folders_for_authors(workspace.discover(), records, authors)
    return folders_within_window(folders, recent)


def print_patch_folders(
    folders: list[PatchFolder],
    header: str,
    limit: int | None = None,
) -> None:
    """The `FOLDER | STATUS` listing, under whichever header names its scope.

    One renderer for all three call sites (ADT #510). The header and the table
    were spelled out at each of them, and a third copy is how three of them start
    disagreeing on whatever nobody compared, which is the failure `#474`, `#504`
    and `#506` each found once and fixed only locally.

    **It lives here rather than in `patch_preview_render`, and the import
    direction is why**: that module imports `print_patch_plan` from this one, so
    the helper would close a cycle sitting there. This is also the module with
    room, `patch_preview_render` being 19 KB against the 20 KB context guard, so
    routing its call site here shrinks the file under pressure instead of growing
    it. Same seam `print_patch_plan` already sits on: a patch section that more
    than one screen prints.

    ``limit`` is the caller's, not a default: `patch_show_patches` narrows the
    two screens that are ALSO narrowed by author and window, and the `-archive`
    listing is capped by nothing at all.
    """
    print_adt_header(header)
    print_adt_table(folder_preview_rows(folders, limit))


def run_archive_patches(
    args: argparse.Namespace,
    workspace: PatchWorkspace,
    config: dict[str, Any],
) -> int:
    # A bare `-archive` names no patch, so it takes none and the receipt does not
    # print at all (ADT #513). Jan, 2026-08-24: *"When no name was passed (just
    # -archive), then dont archive anything, dont show archiving patches section
    # either."* What is left is the listing below, which is what the run is now
    # for: read the inventory, then name what you want gone.
    #
    # The predicate is `named_patch_refs`, the same reader `archive_patches` uses
    # to decide what leaves the disk, so this screen cannot say one thing while
    # the disk did another. A plain `if args.archive:` here would be that drift:
    # `-archive ""` is a non-empty list holding no ref.
    if named_patch_refs(args.archive):
        result = workspace.archive_patches(config, refs=args.archive)
        # The listing's own `FOLDER | STATUS`, through the one renderer all three
        # tables now use. Jan, 2026-08-24: *"you show the same columns as in
        # RECENT PATCH FOLDERS section (the current columns in ARCHIVING PATCHES
        # section sucks)"*.
        #
        # This reverses `#346`, which had restored old ADT's own
        # `ID | PATCH CODE | FOLDER` here (`ADT--OLD/patch.py:2184-2188`). Both
        # shapes are his call and the newer one wins; it is recorded as a decision
        # so the next legacy-parity audit reads the missing three columns as this
        # card rather than as `#346` regressing. `patch_id` is untouched, an
        # all-digit ref still resolves through it.
        #
        # No cap, for the same reason the listing under it has none: this names
        # what the run took, and a run that took more than a screenful still has
        # to say so.
        print_patch_folders(result.folders, ARCHIVING_PATCHES_HEADER)
        print()
    # Then what is LEFT (ADT #510). Jan, 2026-08-24: *"you will print the usual
    # "PATCH FOLDERS:" section, but without any limits (always all rows), so we
    # can easily target all patches for archival."*
    #
    # `discover()` re-reads the disk on every call and archiving removes the
    # folder, so this call, made after `archive_patches`, returns the survivors:
    # exactly the set the next `-archive` pattern has to name. A run whose refs
    # matched nothing archived nothing and so lists everything, which is the case
    # that most needs the answer, since the reader has just mistyped a pattern.
    #
    # No cap and no author/window filter, and the second half is a fact about the
    # run rather than a choice: `-archive` returns before the commit scan, so
    # there are no records for `folders_for_authors` to join a folder against.
    print_patch_folders(workspace.discover(), ALL_PATCH_FOLDERS_HEADER)
    return 0


__all__ = [name for name in globals() if not name.startswith("__")]
