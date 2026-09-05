from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypedDict

from adt_ai.shared.commit_discovery import PatchFolder

if TYPE_CHECKING:
    # Annotation only: `patch/staleness.py` imports `patch/files.py`, which
    # imports this module, so a runtime import here would close the cycle.
    from adt_ai.patch.staleness import StaleExport


class PatchError(Exception):
    """Raised when patch creation cannot proceed safely."""


class ViewMismatch(TypedDict):
    """One view whose column list differs between DEV and the deploy target.

    A `TypedDict` rather than a dataclass because the producer builds it as a
    literal and the renderer reads it by key; naming the four keys is what lets
    either side change one without the other silently keeping the old spelling.
    """

    schema: str
    view: str
    expected: list[str]
    actual: list[str]


@dataclass(frozen=True)
class PatchContentsGroup:
    """One schema's slice of `PATCH CONTENTS:`, in the order the patch runs it.

    A patch writes one install script per schema, so the script IS the grouping,
    and reading the files off it gives both halves at once: which schema owns a
    file, and where it sits in the install order. `database_files` below answered
    neither. It was one `sorted()` list with no schema on it, so a two-schema
    patch printed both schemas' files interleaved alphabetically under a single
    header, and a reader could not tell which schema would receive what (ADT
    #443). Jan, 2026-08-21: *"so it would be clearly visible which files you are
    going to process in which schema; and the order should be as the order in
    the patch itself"*.
    """

    schema: str
    app_id: int | None
    files: list[str]

    @property
    def label(self) -> str:
        """What the section header appends, old ADT's `schema_with_app`."""
        return self.schema if self.app_id is None else f"{self.schema}.{self.app_id}"

@dataclass(frozen=True)
class RefreshPlan:
    folder: str | None
    database_files: list[str]
    apex_components: dict[str, list[str]]
    # The console's own source since ADT #443. `database_files` stays as the flat
    # union for readers that only need the set (`tests/patch/test_runner_deploy`),
    # so the two can never disagree: the union is derived from these groups.
    groups: list[PatchContentsGroup] = field(default_factory=list)

@dataclass(frozen=True)
class AlterHelper:
    """One generated `ALTER TABLE` script, what `ALTER STATEMENTS:` lists.

    ``source`` is the exported table file the diff was taken from, so a reader
    can tell which table a helper belongs to without decoding its file name.
    """

    source: str
    path: str
    statements: int

@dataclass(frozen=True)
class GeneratedScripts:
    """What `-create` wrote into ``patch_scripts_dir`` on THIS run.

    ``alters`` feeds `ALTER STATEMENTS:`;
    ``paths`` is every generated helper, drops included, and exists so the
    `UNCOMMITTED FILES` warning can leave them out. A helper this run just wrote
    has no commit by construction, so listing it there would bury the one entry
    that is actionable (a source file the developer forgot to commit) under
    rows nobody can act on.
    """

    alters: list[AlterHelper]
    paths: list[str]
    #: Table files hash mode could generate no ALTER for, because the version the
    #: baseline recorded is not in the scanned history any more (ADT #447). It is
    #: reported rather than skipped: a silently missing `ALTER TABLE` deploys a
    #: `CREATE` against a table that already exists, and the developer is the
    #: only one who can say what the column change was.
    unresolved_tables: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class PatchScripts:
    """What `-create` did with ``patch_scripts_dir/<CODE>/`` (ADT #309).

    Those scripts MOVE into the patch folder now (`patch/scripts.py`), so this is
    the writer's own account of the move rather than something a later directory
    scan could re-derive: after the run, the source folder is gone.

    ``ignored`` and ``unknown`` are old ADT's two warnings (patch.py:1433-1440)
    and both name files LEFT BEHIND, in a real slot but belonging to no selected
    commit, and in a slot no `patch_map` group can produce. Neither is an error:
    they are the two ways a script silently fails to ship.
    """

    moved: list[str] = field(default_factory=list)
    recovered: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class ProcessedFile:
    """One row of the `PROCESSED FILES:` report (ADT #276, old ADT patch.py:1586).

    There is no leading-character field (ADT `#456`). Old ADT split the rows
    three ways with ``-``, ``>`` and ``!``, and Jan asked for one marker on every
    row, so the renderer writes the dash and no row carries a choice to make.
    What ``!`` used to say, no commit behind this file, is ``commit_number is
    None`` here and its own warning section on screen.

    There is no trailing marker either, since ADT `#465`. The row carried one of
    ``[DELETED]`` / ``[ALT:n]`` / ``[NEW]``, dot-padded to column 72, and Jan
    asked for the two that say something to become sections of their own and the
    third to go: *"create a sections dedicated for alter statements and deleted
    objects"*, then *"no need to bother with new files"*. ``deleted`` is what is
    left, and it feeds `DELETED OBJECTS:` rather than a bracket on this row.
    """

    path: str
    #: The OBJECT this path held is gone, so the patch ships a DROP helper for it
    #: instead of content. What `[DELETED]` used to say on the row itself.
    deleted: bool
    commit_number: int | None
    # `#277`: commits newer than the one being shipped that also touched this
    # file, newest first. Always empty under `-head`, where the newest version IS
    # what ships (old ADT patch.py:1635).
    newer: list[tuple[int, str]]
    #: The patch ships this file's CONTENT, which is a third answer rather than
    #: the opposite of ``deleted`` (ADT #511). A rename arrives as a delete at one
    #: path plus an add at another, so the old side of a GROUP move sits on
    #: neither side of that question: its object moved rather than left, so it
    #: earns no DROP, and its file is gone, so there is nothing to link or
    #: snapshot. This is `_write_snapshots`' own question, recorded here so the
    #: listing and the install script cannot answer it differently again.
    carried: bool = True

@dataclass(frozen=True)
class SchemaReport:
    schema: str
    app_id: int | None
    files: list[ProcessedFile]
    alter_files: list[str]
    uncommitted: list[str]
    #: The schema's dropped objects as `(TYPE, NAME)`, what `DELETED OBJECTS:`
    #: lists (ADT #465, keyed on the object since #506). Established here rather
    #: than re-walked at render time, which would ask the same question twice.
    #:
    #: **The unit is the object, never the path.** A group move is a delete at
    #: one path plus an add at another (`#498`), so a listing keyed on paths
    #: reports a move as a deletion; and the header has said OBJECTS since
    #: `#465` while the rows said files, which is the half `#506` closed.
    deleted_objects: list[tuple[str, str]] = field(default_factory=list)
    #: Dropped paths that resolve to no database object at all: a per-patch
    #: script, a template, anything outside `path_objects`. They still belong
    #: under the header, and they have no type or name to print, so they keep
    #: the plain `  - <path>` row they always had.
    deleted_scripts: list[str] = field(default_factory=list)
    # The schema's own object files, what old ADT counted in the section header
    # (`len(self.relevant_files[schema_with_app])`, patch.py:1257). `files` also
    # carries the injected templates and scripts, which are not what the count is
    # about.
    object_count: int = 0

    @property
    def schema_label(self) -> str:
        """The group key as `PROCESSED FILES:` names it, old ADT's `schema_with_app`."""
        return self.schema if self.app_id is None else f"{self.schema}.{self.app_id}"

    @property
    def carried_files(self) -> list[ProcessedFile]:
        """The rows `PROCESSED FILES:` prints: the files this patch SHIPS (ADT #511).

        A path the patch carries nothing for is not one of them, and there are
        two of those. A dropped object ships a DROP helper and says so under
        `DELETED OBJECTS:` two headers above, which is where `#465` put it when it
        took `[DELETED]` off these rows; the old side of a group move ships
        nothing at all. A rename produces one of each, so the listing showed both
        sides of one object: Jan, 2026-08-24, *"in PROCESSED FILES section you are
        listing both sides ... List just the target file."*

        `files` keeps both rows, because `deleted_objects`, `deleted_scripts` and
        the install script's own `[DELETED]` line are built from that list. This
        is the listing's view of it, established here beside them rather than as
        a filter the renderer spells for itself.
        """
        return [item for item in self.files if item.carried]

@dataclass(frozen=True)
class PatchFileSelection:
    """What went into the patch.

    It carried a second field, `missing_grants`, for as long as `_patch_files`
    injected a grant script per changed schema and had to report the schemas it
    could not resolve one for (ADT #339). Nothing is injected since ADT #501, so
    there is nothing left to fail to find, and the file list is the whole answer
    again.
    """

    files: list[str]

@dataclass(frozen=True)
class DatabasePatchResult:
    folder: Path
    sql_files: dict[str, Path]
    files: list[str]
    reports: list[SchemaReport] = field(default_factory=list)
    # What happened to `patch_scripts_dir/<CODE>/`, moved, recovered on a
    # re-create, or left behind as ignored/unknown (ADT #309). The last two are
    # what the console warns about; old ADT printed the same two lists
    # (patch.py:1433-1440).
    scripts: PatchScripts = field(default_factory=lambda: PatchScripts())
    # Hash-mode tables whose recorded version is out of the scanned history's
    # reach, so no ALTER could be generated for them (ADT #447). Empty on every
    # commit-built patch, which has a version walk to read instead.
    unresolved_tables: list[str] = field(default_factory=list)
    # Objects the database moved past after they were exported, so this patch
    # ships the previous body for them (ADT #261, reported rather than refused
    # since #468). Carried on the result rather than raised, which is what makes
    # them a warning section instead of a stopped build.
    changed_objects: list[StaleExport] = field(default_factory=list)
    # Owners whose mirrored DDL times carry no recorded database UTC offset, so
    # the comparison above could not be made for them at all (ADT #394, #468).
    unclocked_schemas: list[str] = field(default_factory=list)

@dataclass(frozen=True)
class DeploymentPlanItem:
    order: int
    file: str
    schema: str
    app_id: int | None
    files: int
    commits: int
    path: Path

@dataclass(frozen=True)
class DeploymentResult:
    order: int
    file: str
    schema: str
    app_id: int | None
    files: int
    commits: int
    status: str
    log_path: Path | None
    # How far the install script actually got: ``deployed`` counts the `FILE:`/
    # `SCRIPT:` markers SQLcl echoed back before it stopped (ADT #254), but only
    # ones matching a genuinely live, non-template `@` reference, since a label
    # whose paired `@` line was hand-disabled still echoes and must not count
    # (ADT #321). ``None`` means the run reported no progress at all, a script
    # that died before its first file, or a patch folder generated before the
    # markers existed, and renders as a blank cell, never ``0/0``.
    # ``deployed_total`` is the same live-reference count, calculated straight
    # off the finished install script, never off a printed label alone.
    deployed: int | None = None
    deployed_total: int | None = None
    # The bounded slice of the SQLcl transcript that says why this script failed
    # (ADT #272). Empty on every non-ERROR row. Carried on the result rather than
    # re-read from ``log_path`` so the renderer needs no second file read, and so
    # a caller that passes its own gateway still gets the diagnosis.
    error_excerpt: tuple[str, ...] = ()
    # Wall-clock seconds this one script spent in SQLcl (ADT #273). Old ADT timed
    # every script and rendered a `timer` column (patch.py:518,572,592); the
    # rewrite dropped it, so a two-schema deploy said nothing about which half was
    # slow. ``None`` for a script the run never executed, SKIPPED, NOT RUN --
    # which renders as a blank cell rather than a `0s` that claims a measurement
    # nobody took.
    seconds: float | None = None

@dataclass(frozen=True)
class DeploymentRunResult:
    folder: PatchFolder
    plan: list[DeploymentPlanItem]
    results: list[DeploymentResult]
    status: str
    view_mismatches: list[ViewMismatch]
    #: The invalid objects the post-deploy pass ran `ALTER ... COMPILE` against,
    #: as `(schema, type, name)`. Attempted, not fixed: read `still_invalid` for
    #: the outcome.
    recompiled: list[tuple[str, str, str]]
    #: The ones still invalid when `USER_OBJECTS` was read again after that pass
    #: (`#658`). A subset of `recompiled` and the reader both of them lacked: the
    #: run reported a recompile it never checked, and `docs/patch_deploy.md` said
    #: the recompile was part of the outcome. It does NOT flip the run's status,
    #: because the query is schema-wide rather than patch-scoped, so an unrelated
    #: object that has been invalid for a month would fail every deploy.
    still_invalid: list[tuple[str, str, str]] = field(default_factory=list)
    #: Applications the patch ships that `-app` could not import, because no
    #: APEXlang tree was exported for them (ADT #592). A note rather than a
    #: refusal: the patch may legitimately carry an application nobody has taken
    #: an APEXlang export of, and the `NOTES:` section names the export that
    #: would change that. Empty on every run that passed no `-app`.
    apex_notes: list[str] = field(default_factory=list)
    #: One `ApexScanReport` per application this deploy landed (`#676`), each
    #: already written to its own log in the patch folder. Unlike `still_invalid`
    #: this DOES flip the run's status, because it is patch-scoped: the scan
    #: reads the application the patch just deployed, so a finding is this
    #: deploy's. Empty when `deploy_verify_scan` is off or no application landed.
    apex_scans: list[Any] = field(default_factory=list)
