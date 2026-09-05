"""The two commit/folder tables the `patch` console prints.

Split out of `runner.py` when ADT #276 pushed it past the 20 KB context guard
(`tests/contracts/test_context_file_size.py`). The seam is the one already
implied by the file: everything here turns records and folders into rows and
touches neither git nor the filesystem, while `runner.py` is the workspace that
builds and deploys. `runner` re-exports both names, so every existing importer
is untouched.
"""

from __future__ import annotations

import re
from datetime import date, datetime

from adt_ai.export_db.render import adt_table_line_width
from adt_ai.shared.commit_discovery import CommitRecord, PatchFolder
from adt_ai.shared.dates import within_recent_window
from adt_ai.shared.patch_folders import PATCH_FOLDER_RE

# `<number>) <summary>`, the row `create.py`'s `change_summary_comment` writes
# into every install script's `-- COMMITS:` header and `parse_patch_folder` reads
# back. `patch_deploy_render._FOLDER_COMMIT_RE` matches the same shape for the
# same reason; both stay tolerant of a row that carries no number, because a
# folder predating the current header, or one edited by hand, holds lines that
# name no commit and must therefore hide none.
_FOLDER_COMMIT_ROW_RE = re.compile(r"^(\d+)\)\s*(.*)$")

# `# | MESSAGE` and nothing else (ADT #352, Jan 2026-08-15: "Recent commits list
# remove COMMIT column, rename SUMMARY column to MESSAGE").
#
# `COMMIT` goes for the reason `AUTHOR`, `FILES` and `PATCH` went in `#269`: it
# spends fixed width the elastic subject column wants, and the subject is what a
# reader scans for. Selection is untouched, `-commit <hash prefix>` still
# resolves against the hash; it is simply no longer a column.
COMMIT_HASH_LENGTH = 8
PREVIEW_LINE_WIDTH = 78


def preview_rows(
    records: list[CommitRecord],
    limit: int | None = None,
) -> list[dict[str, object]]:
    """The newest ``limit`` commits, rendered newest first.

    The cap comes off the NEWEST end, so 40 rows out of a 100-commit scan are
    commits 61-100. That is what this docstring always claimed and what the code
    did not do until ADT #417: ``records`` arrive OLDEST first, because
    `CommitScanner.run_window` reads the store newest-first and reverses it for
    every consumer downstream, so `records[:limit]` was the oldest forty. The
    reversal below then rendered that slice descending, which reads like a
    newest-first listing and is not one. Measured on a 284-commit repo scanning
    100 and showing 40: the screen ran 224 down to 185 and the 60 newest commits,
    every one of them the outstanding work, never appeared at all.

    `folder_preview_rows` below keeps `[:limit]` and is correct doing so:
    `discover_patch_folders` hands it newest-first. Two functions, one slicing
    idiom, opposite input orders, which is how this survived a split (#273) and
    a cap being added (#351).

    **Commit `0` is never a row** (ADT #447). Hash mode carries the files git
    history does not account for on a synthetic record numbered `0`, so that the
    builder, which reads records and nothing else, can ship them; it is a
    carrier, not a commit, and a commit table is the one place it must not
    appear. Caught on the first live run against a real schema, where a 1050-file
    baseline diff printed `0   files with no commit in the scanned window` under
    `RECENT COMMITS:`. Filtered here rather than at the two call sites, because
    every commit table in the module renders through this function.
    """
    records = [record for record in records if record.number]
    selected = records[-limit:] if limit else records
    return preview_rows_from(
        [
            (record.number, record.commit_hash[:COMMIT_HASH_LENGTH], record.summary)
            for record in reversed(selected)
        ]
    )


def outstanding_records(
    records: list[CommitRecord],
    folders: list[PatchFolder],
) -> list[CommitRecord]:
    """``records`` minus every commit a patch on disk already carries (ADT #417).

    Jan, 2026-08-20: the listing should hold *"only commits which needs to be
    addressed without the noise of already processed ones"*. The join is the
    commit NUMBER, which is safe because the store allocates it once and the scan
    window bounds the read rather than the numbering (`commit_discovery`
    `run_window`), so the `254)` an old install script recorded still names the
    same commit today.

    **Existence, not deployment.** A patch folder that was built and never
    deployed still hides its commits, which is the reading Jan's words carry.
    The cost is real and worth knowing: a patch left undeployed for weeks takes
    its commits off this screen while they are still outstanding work. `-commit`
    is the stated way back to one, and the `PATCH FOLDERS:` table under the same
    screen is where an undeployed patch is visible.

    **Which folders to pass is the caller's question, and it is not "all of
    them" (ADT #424).** This one takes a folder list rather than a workspace for
    that reason: `RECENT COMMITS:` passes every patch on disk, and a listing OF a
    named patch passes every patch EXCEPT that one, because a listing of an
    existing patch is mostly commits that patch already carries and a blanket
    filter empties it. `#417` read that constraint as a reason to leave the named
    listing unfiltered altogether, which left it offering commits every OTHER
    patch had shipped. `cli/patch_preview_render.records_for_header` owns the
    choice; a new caller decides it rather than inheriting a default.
    """
    if not folders:
        return records
    shipped = {
        int(match.group(1))
        for folder in folders
        for line in folder.commits or []
        if (match := _FOLDER_COMMIT_ROW_RE.match(line))
    }
    if not shipped:
        return records
    return [record for record in records if record.number not in shipped]


def folder_commit_entries(folder: PatchFolder) -> list[tuple[object, str, str]]:
    """A patch folder's own `-- COMMITS:` header as preview entries, newest first.

    `create.py` `change_summary_comment` writes `<number>) <summary>` and no hash
    (old ADT's shape, patch.py:1764), so COMMIT is genuinely unknown here and
    renders blank per the shared cell rule, never a guess or a padded number. The
    header is written oldest-first and is reversed to match the git-scan preview.

    Lived in `cli/patch_deploy_render.py` until ADT #417, when a second and third
    reader appeared: `-create` and `-patch` list the commits a patch holds for
    the same reason `-deploy` does. It belongs on this module's own seam, turning
    records and folders into rows and touching neither git nor the filesystem,
    and the word `deploy` in the old home's name had stopped being true of it.
    """
    entries: list[tuple[object, str, str]] = []
    for line in reversed(list(getattr(folder, "commits", []) or [])):
        match = _FOLDER_COMMIT_ROW_RE.match(line)
        if match:
            entries.append((int(match.group(1)), "", match.group(2)))
        else:
            entries.append(("", "", line))
    return entries


def preview_rows_from(
    entries: list[tuple[object, str, str]],
) -> list[dict[str, object]]:
    """`# | MESSAGE` for any commit source, in the order given.

    Split out of `preview_rows` (ADT #273) so the commit listing `-deploy` prints
    shares one column budget with the one a bare `patch` prints, instead of a
    second copy that drifts. The deploy path can also feed it the patch folder's
    own `-- COMMITS:` header, which records `<number>) <summary>` and no hash.

    The hash is still accepted in the entry tuple and simply not rendered: the
    deploy path has none to give and the scan path does, so dropping it from the
    signature would make one of the two callers lie about what it holds.

    The subject is the only elastic column, so it gets whatever the number
    column leaves inside the 78-character budget rather than old ADT's flat
    `summary_len = 36` (patch.py:39).
    """
    numbers = [str(number) for number, _commit, _summary in entries]
    number_width = max(len(value) for value in [*numbers, "#"])
    message_width = max(
        PREVIEW_LINE_WIDTH - adt_table_line_width([number_width, 0]),
        len("MESSAGE"),
    )
    return [
        {
            "#": number,
            "MESSAGE": summary[:message_width],
        }
        for number, _commit, summary in entries
    ]

def folder_commit_numbers(folder: PatchFolder) -> set[int]:
    """The commit numbers a folder's own `-- COMMITS:` header records.

    The one thing a patch folder says about who built it, beyond the day in its
    name. `create.py` `change_summary_comment` writes `<number>) <summary>` and
    `parse_patch_folder` reads it back, so this parses data already on disk
    rather than asking a folder to carry anything new (ADT #467).

    A row naming no number contributes nothing rather than raising: a folder
    predating the current header, or one edited by hand, holds lines like that.
    """
    return {
        int(match.group(1))
        for line in folder.commits or []
        if (match := _FOLDER_COMMIT_ROW_RE.match(line))
    }


def folders_for_authors(
    folders: list[PatchFolder],
    records: list[CommitRecord],
    authors: list[str] | None,
) -> list[PatchFolder]:
    """The folders carrying at least one commit by one of ``authors`` (ADT #467).

    `-by`/`-my` reached the commit scan and stopped there, so `PATCH FOLDERS:`
    listed every patch on disk under a screen the run had narrowed. Jan, reporting
    the author bug on 2026-08-22: *"I hope this was implemented in patch folders
    too!"*.

    The match is `_filter_records`' own, a lowercase substring against
    `record.author`, because one flag must not select two different sets on one
    screen. **ANY, never ALL**: a patch is rarely one person's work, and a folder
    you contributed to is a folder `-my` should show.

    **A folder this cannot attribute is dropped**, which covers the one carrying
    commit numbers outside the scan window and the one whose install script names
    no commit at all. Keeping it would leave a patch that may be entirely somebody
    else's on a screen claiming to be filtered, and the filter is what the reader
    is trusting; `patch_scan_commits` is the knob that widens the window, and it
    already governs how far back the commit table reaches.
    `folders_within_window` below makes the opposite call, and says why.
    """
    if not authors:
        return folders
    needles = [value.lower() for value in authors]
    mine = {
        record.number
        for record in records
        if any(needle in record.author.lower() for needle in needles)
    }
    return [folder for folder in folders if folder_commit_numbers(folder) & mine]


def folders_within_window(
    folders: list[PatchFolder],
    recent_days: int | float | None,
    *,
    now: datetime | None = None,
) -> list[PatchFolder]:
    """The folders built inside a ``-recent`` window (ADT #467).

    A folder is `yymmdd-seq-CODE`, so its day is in its name and needs no commit
    join and no filesystem read. The name is also the durable fact: it survives a
    copy, a restore and an archive round trip, where an mtime does not.

    The arithmetic is `shared/dates.within_recent_window`, the same one the commit
    table and `search_repo` use, so `-recent 1` means today on both halves of this
    screen and in every other command.

    **A name that does not parse survives**, the opposite call to
    `folders_for_authors` above, because the questions differ. "Whose is this" has
    no safe default when nothing answers it; dropping a folder because its NAME is
    spelled unusually would hide something plainly on disk. Neither rule is the
    general one, each is argued from what its own filter promises.
    """
    if recent_days is None:
        return folders
    kept = []
    for folder in folders:
        day = folder_day(folder)
        if day is None or within_recent_window(day, recent_days, now=now):
            kept.append(folder)
    return kept


def folder_day(folder: PatchFolder) -> date | None:
    """The day a folder's `yymmdd-` prefix records, or ``None`` when it has none.

    The century comes from `strptime`'s `%y` rule rather than a hardcoded `20`
    prefix, so the name is read back the way `strftime("%y%m%d")` wrote it. Same
    reasoning `patch_folder_match_targets` states for its `YYYYMMDD` spelling of
    this field.
    """
    match = PATCH_FOLDER_RE.match(folder.folder)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("day"), "%y%m%d").date()
    except ValueError:
        return None


# `FOLDER | STATUS` and nothing else. `#268` dropped `PATCH` (`patch_code`, the
# folder name minus its `yymmdd-seq-` prefix), `SQL` (`<code>.sql`), `COMMITS`,
# `FILES` and `TARGETS`: two spellings of `FOLDER` plus three counts, which
# together pushed the row past the 80 columns a terminal has and folded every
# patch onto two lines.
#
# `ID` went the same way on 2026-08-22 (ADT #467). Jan: *"remove ID column from
# PATCH FOLDERS table, it is useless"* (the table was called that then; it is
# `RECENT PATCH FOLDERS:` and `ALL PATCH FOLDERS:` since `#510`). It was the
# third spelling of `FOLDER`,
# and the one `#268` added on his own request twelve days earlier: `patch_id`
# derives it from the patch code, and the patch code is the tail of the value in
# the column beside it, so the row spelled one fact twice.
#
# **`patch_id` stays, in `shared/patch_folders.py`.** An all-digit `-name` or
# `-archive` ref still resolves through it: the number stopped being a column,
# not a selector. It kept one column here, in `archive_report_rows` below, until
# ADT #513 retired that table in favour of these same rows, which is why this
# module no longer imports it and why this is now the ONE folder row on screen.
def folder_preview_rows(
    folders: list[PatchFolder],
    limit: int | None = None,
) -> list[dict[str, object]]:
    # Newest first, newest at the TOP (ADT #510). Jan, 2026-08-24: "And lets
    # change the order, newest at the top."
    #
    # This reverses `#285`, where he asked for the opposite ("order is wrong, I
    # want oldest first, newest bottom") on the argument that a terminal scrolls
    # so the bottom row is the one still on screen. Both are his call and the
    # newer one wins; it is recorded here as a decision so the next legacy-parity
    # audit does not read the missing `reversed()` as `#285` regressing.
    #
    # `limit` (ADT #351, `patch_show_patches`) cuts the NEWEST end, for the same
    # reason `preview_rows` does: a capped listing has to keep the patches you
    # might still deploy, not the oldest ones on disk. That slice was already
    # written against a newest-first list and is untouched by the flip.
    if limit:
        folders = folders[:limit]
    # **The renderer now AGREES with `discover_patch_folders` rather than reading
    # its order back.** That sort stays newest-first because it is load-bearing
    # for SELECTION: `_select_patch_folder` (patch/deploy.py) and `refresh_plan`
    # both take `folders[0]` as the winning match, so a later display flip must
    # come back here and must not touch the shared sort, which would make
    # `-deploy` pick the oldest colliding folder, `#255`'s wrong-patch deploy
    # reintroduced from the opposite direction. The two happening to match today
    # is a coincidence of what Jan wants on screen, not a shortcut to take.
    return [
        {
            "FOLDER": folder.folder,
            "STATUS": folder.latest_status or "",
        }
        for folder in folders
    ]


# `archive_report_rows` stood here until ADT #513, building the `-archive`
# receipt as `ID | PATCH CODE | FOLDER`, old ADT's own three
# (`ADT--OLD/patch.py:2184-2188` builds `ref`/`patch_code`/`folder`;
# `lib/util.py:553` uppercases a header and spaces its underscores, which is
# where `PATCH CODE` came from). `#346` had restored that shape after ADT.ai
# replaced it with an invented `FOLDER | ARCHIVE` plus a free-text list, and Jan,
# 2026-08-15: "you were creative and did not respected old ADT outcomes at all".
#
# He then asked for the receipt to carry the LISTING's columns instead, 2026-08-24:
# *"you show the same columns as in RECENT PATCH FOLDERS section (the current
# columns in ARCHIVING PATCHES section sucks)"*. Both shapes are his call and the
# newer one wins, so the function is gone rather than kept for a caller that no
# longer exists: `folder_preview_rows` above is now the one row shape every patch
# folder table on the screen is built from.


__all__ = [name for name in globals() if not name.startswith("__")]
