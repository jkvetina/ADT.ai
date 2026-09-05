"""`export_db -baseline`: record what an environment HOLDS (ADT #452).

Everything reached only under that flag lives here and nothing else does, the
same seam `patch_hash_mode.py` draws for `-hash` and `-baseline` on the patch
side. `commands_exports` keeps the export; this keeps the measurement.

**The gap it closes.** Every baseline before this one was a belief. `patch
-baseline` records the DEV working tree and calls it the target; a deploy
advances it by what a patch claims it shipped. Neither has ever connected to the
environment being described. Jan, 2026-08-21, proposing the fix and then arguing
against his own first version of it: *"if I would know hashes on the DEV and at
the target env (UAT lets say), than I would be able to eliminate matching objects
and have a patch based on not just what changed on DEV, but what is
missing/different at the target."* ... *"How do we track hashes on other env? By
adding deployed hashes to that file? That does not reflect what we actually might
have there."*

So this mode exports through the ordinary pipeline, same discovery, same filters,
same `normalize_ddl`, and then HASHES each object at the path it would have been
written to instead of writing it. The result lands in the file `patch -target
<ENV> -hash` already reads, so nothing in `patch` changed to consume it.

**Three promises make the reading trustworthy**, and each is a way it could
otherwise lie.

*It is full by construction.* A narrowing flag is refused by name rather than
ignored, because a partial baseline reads on disk exactly like a complete one and
`patch -hash` would then treat every unlooked-at object as absent from the target.

*It writes nothing and advances nothing.* No object file, no `.fix` sidecar, no
`auto_delete` sweep, and neither the recent watermark nor the job signatures.
Those record what an export WROTE; stamping one here would make the next real
`export_db -recent` against this environment skip every object this run only
looked at. That is the promise reading the output cannot check, which is why it
carries its own tests.

*Inside its scope it replaces, outside it leaves alone.* The scope is this run's
schemas and the database half of the layout. Merging instead would keep a stale
entry for an object the target no longer holds and that object would never ship
again; replacing everything would wipe the APEX entries `export_db` never sees.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from adt_ai.cli.constants import PatchRequest, print_adt_header
from adt_ai.cli.context import _flatten_arg_groups, _project_relative
from adt_ai.cli.patch_hash_mode import BASELINE_STAMP_FORMAT, print_baseline_stats
from adt_ai.cli.patch_preview_render import patch_scan_commits
from adt_ai.export_db.files import ObjectWritePlan
from adt_ai.patch.hashes import replace_measured_scope, resolve_baseline_path
from adt_ai.patch.layout import database_schema, is_database_path
from adt_ai.shared.commit_discovery import GitCommitCache
from adt_ai.shared.file_list import print_file_rows

#: The flags that would narrow a `-baseline` run, each with the spelling it is
#: refused by. `-delete` is here for the other half of the promise: a measured
#: run writes and deletes nothing at all.
_REFUSED: tuple[tuple[str, Callable[[argparse.Namespace], bool]], ...] = (
    ("-recent", lambda args: args.recent is not None),
    ("-name",   lambda args: bool(_flatten_arg_groups(args.name))),
    ("-type",   lambda args: bool(_flatten_arg_groups(args.type))),
    ("-by",     lambda args: args.by is not None),
    ("-my",     lambda args: bool(args.my)),
    ("-delete", lambda args: bool(args.delete)),
)


def narrowing_flags(args: argparse.Namespace) -> list[str]:
    """Every refused flag this run carries, so one message names them all."""
    return [flag for flag, present in _REFUSED if present(args)]


def refusal(refused: list[str]) -> str:
    return (
        f"export_db: -baseline cannot be narrowed by {', '.join(refused)}: "
        "a baseline records everything this environment holds, so a "
        "partial one would read as a complete one."
    )


def measured_hashes(plans: list[ObjectWritePlan], root: Path) -> dict[str, str]:
    """This run's readings, keyed the way a baseline is keyed.

    Repo-relative POSIX paths, because that is what `hash_working_tree` records
    and the point of the mode is that the two are directly comparable. A plan
    landing outside the project is dropped rather than recorded under a key
    nothing can ever match.
    """
    hashes: dict[str, str] = {}
    for plan in plans:
        value = getattr(plan, "content_hash", None)
        # `is None` rather than falsy, and the difference is a defect the drift
        # spike caught. An EMPTY file hashes to the empty sentinel `""`, which
        # old ADT chose and `file_payload_hash` kept, and a real project has
        # them: a schema granting nothing still exports a zero-byte
        # `grants/<SCHEMA>.sql`. `hash_working_tree` records those as `path ->
        # ""`, so dropping them here would leave the DEV side holding a key the
        # baseline lacks, `diff_against_baseline` would call it NEW, and every
        # empty file would ship in every hash patch forever.
        if value is None:
            continue
        try:
            relative = Path(plan.path).resolve().relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        hashes[relative.as_posix()] = value
    return hashes


def write_measured_baseline(
    root: Path,
    config: dict[str, object],
    environment: str | None,
    schemas: list[str],
    measured: dict[str, str],
    *,
    override: str | None,
) -> None:
    """Fold what this run READ into the environment's baseline."""
    target_env = environment or "-"
    path = resolve_baseline_path(root, config, target_env, override)
    covered_schemas = {schema.upper() for schema in schemas}

    def covered(file: str) -> bool:
        return (
            is_database_path(file, config)
            and database_schema(file, config) in covered_schemas
        )

    written, counts = replace_measured_scope(
        path,
        measured,
        commits_at_hashes(root, config, measured),
        covered    = covered,
        target_env = target_env,
        stamp      = datetime.now().strftime(BASELINE_STAMP_FORMAT),
    )
    print_adt_header("WRITING BASELINE:")
    # One row, and flat: a baseline file is named rather than listed (ADT #504).
    print_file_rows([_project_relative(written, root)], nested=False)
    print_baseline_stats(counts, total=counts["TOTAL"])


def commits_at_hashes(
    root: Path, config: dict[str, object], measured: dict[str, str]
) -> dict[str, int]:
    """Which commit each measured file's CONTENT matches, where one does.

    A reverse lookup rather than "the newest commit that touched this path": the
    hash is the target's actual content, so the commit carrying exactly that
    content is the one this environment is standing on, and `foo.sql | 312 |
    abc...` then reads as "the target is at commit 312 for this file".

    **A blank is the useful half.** It means the target holds content matching no
    commit in the scanned window, which is drift, and it is the first line to
    look at. A root that is not a checkout, or a scan that raises, fills blanks
    rather than failing a run that has already done its work.
    """
    try:
        records = GitCommitCache().scan(
            PatchRequest(root=root, commit_limit=patch_scan_commits(config))
        )
    except Exception:
        return {}
    commits: dict[str, int] = {}
    for record in sorted(records, key=lambda item: item.number, reverse=True):
        for file, value in record.files.items():
            if file in measured and measured[file] == value and file not in commits:
                commits[file] = record.number
    return commits


__all__ = [
    "commits_at_hashes",
    "measured_hashes",
    "narrowing_flags",
    "refusal",
    "write_measured_baseline",
]
