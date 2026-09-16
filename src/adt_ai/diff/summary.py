"""What differs between the two schemas, and which side is short of it (ADT #790).

`diff` used to end at the zip path, which told a reader nothing about the one
question a pre-deployment check exists to answer: *what would this change?*
Jan, on the console that printed a path and a timer: *"HOW IS THIS TELLING ME
WHAT CHANGED"* (`#763`). So the screen started reading the artifact and printing,
per object, the DDL verb its change script would run.

**The verb was the wrong answer, and `#790` replaces it.** `ACTION` said
`REPLACE`, `COMMENT`, `ADD, DROP*`, and, where SQLcl emitted a table plus its
constraint, `CREATE, ADD`; a script carrying no statement at all left the cell
blank. Jan, on the fourth attempt at this screen: *"What the action even means???
I am comparing 2 schemas, source and target. You must clearly say whats different
from perspective of one of these only. ... COMMENT COMMENT will tell me what?
SHIT. ... JOBS NULL tells me what? NOTHING."*

So the column is `STATUS` now, `MISSING`, `CHANGED` or `EXTRA`, and it is read
from set membership rather than parsed back out of SQL. `diff/inventory.py` owns
that comparison and the live measurement behind the direction.

**The screen is derived from the two export trees rather than from the
artifact**, which is the same move one layer down: the trees are what SQLcl
compared, they hold one file per object, and every row they produce can name
which side it is on. A row nobody can explain is what this module now exists not
to print. The artifact is still the deliverable; it is no longer the narrator.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from adt_ai.diff.inventory import STATUS_ORDER, GrantRow, Inventory
from adt_ai.shared.object_types import (
    normalize_object_type_pattern,
    normalize_object_type_patterns,
    singular_object_type,
)
from adt_ai.shared.sql_like import matches_sql_like

#: The folders whose type name is NOT their folder name read back a word at a
#: time. Everything else is `singular_object_type(folder)`, which turns `tables`
#: into `TABLE` and `package_bodies` into `PACKAGE BODY` without a row here, so a
#: SQLcl object type ADT has never seen reads like every other one instead of
#: arriving as a raw plural folder. That split is what `#780` fixed: the old
#: hand-written map spelled its types singular and the fallback left the folder
#: plural, so one column printed `COMMENT` beside `JOBS`.
_OBJECT_TYPES = {
    "materialized_view_logs" : "MVIEW LOG",
    "object_grants"          : "GRANT",
}

#: The type name the `object_grants` folder maps to, kept as a name because three
#: things read it: the inventory, the `-type` filter, and the reporter's split. It
#: is Oracle's own pseudo-type spelling, the one `-type GRANT` and `export_db`'s
#: overview already use; it read `OBJECT GRANT` until Jan asked for the prefix
#: gone (`#780`).
GRANT_OBJECT_TYPE = "GRANT"


@dataclass(frozen=True)
class DiffChange:
    object_type : str
    object_name : str
    #: `MISSING`, `CHANGED` or `EXTRA`, always one of the three. There is no empty
    #: case: the row exists precisely because the two trees disagreed about this
    #: object, and that disagreement IS the status.
    status      : str


@dataclass(frozen=True)
class DiffSummary:
    changes : tuple[DiffChange, ...] = ()
    grants  : tuple[GrantRow, ...] = ()
    #: Whether `-type`/`-name` produced this view. It is carried rather than
    #: derived because an empty filtered summary and an empty unfiltered one mean
    #: opposite things, and only one of them may say the schemas match.
    filtered: bool = False

    @property
    def in_sync(self) -> bool:
        """Nothing differs at all, so the two schemas already match.

        This is the answer `DIFF COMPLETE:` never gave: before `#763` an
        identical pair and a hundred-object drift printed the same screen.

        A filter is never in sync, however empty it comes back: the schemas may
        differ in forty ways and simply not in the way that was asked about.
        """
        return not self.changes and not self.grants and not self.filtered

    @property
    def objects(self) -> tuple[DiffChange, ...]:
        """Everything the `CHANGED OBJECTS:` listing covers, grants excluded."""
        return self.changes

    @property
    def incoming_grants(self) -> tuple[GrantRow, ...]:
        """Grants INTO the compared schema, sorted by the column they lead with.

        The leading column is `OWNER`, so that is the primary key: a table is
        sorted when the column a reader scans runs in order, which is the whole
        of Jan's complaint about the first cut.
        """
        return tuple(
            sorted(
                (grant for grant in self.grants if grant.incoming),
                key=lambda grant: (
                    grant.owner, grant.object_type, grant.object_name, grant.status
                ),
            )
        )

    @property
    def outgoing_grants(self) -> tuple[GrantRow, ...]:
        """Grants OUT of the compared schema, sorted by object then grantee.

        This table leads with `OBJECT TYPE`, because the objects are ours and the
        grantee is the answer rather than the subject.
        """
        return tuple(
            sorted(
                (grant for grant in self.grants if not grant.incoming),
                key=lambda grant: (
                    grant.object_type, grant.object_name, grant.grantee, grant.status
                ),
            )
        )

    @property
    def statuses(self) -> tuple[str, ...]:
        """Every status this screen actually printed, in the LEGEND's own order.

        Only what is on screen: a legend defining `EXTRA` for a comparison that
        produced no extra row explains nothing and costs a line.
        """
        found = {change.status for change in self.changes}
        found.update(grant.status for grant in self.grants)
        return tuple(
            status
            for status in sorted(found, key=lambda name: STATUS_ORDER.get(name, len(STATUS_ORDER)))
        )

    def select(
        self,
        types: Sequence[str] = (),
        names: Sequence[str] = (),
    ) -> DiffSummary:
        """The subset `-type` / `-name` asked for, as a summary in its own right.

        **The export is narrowed too, so this mostly has nothing left to drop**
        (`#780`, widened to every dictionary family by `#790`):
        `diff/export_filters.py` writes the patterns into the throwaway project's
        filters file, and the trees come back holding only what was asked for.
        This still runs, because a family SQLcl narrows on a PARENT object's
        name, a comment, a grant, an index, can return a row whose own name
        does not match the pattern, and because the counts and the listings
        have to agree about what a run is showing.

        Patterns are SQL LIKE, the language every other filter on this tool
        takes, with `*` accepted for `%` as `schema_selection` accepts it. A type
        matches the name the listing prints, which is Oracle's own singular
        spelling (`PACKAGE SPEC`, `GRANT`), so both `-type PACKAGE` (specs,
        Oracle's own reading) and `-type PACKAGE%` (both halves) answer as they
        read.
        """
        if not types and not names:
            return self
        type_patterns = normalize_object_type_patterns(_wildcards(types))
        name_patterns = _wildcards(names)
        changes = tuple(
            change
            for change in self.changes
            if _matches_type(change.object_type, type_patterns)
            and _matches_name(change.object_name, name_patterns)
        )
        grants = tuple(
            grant
            for grant in self.grants
            if _matches_type(GRANT_OBJECT_TYPE, type_patterns)
            and _matches_name(grant.object_name, name_patterns)
        )
        return DiffSummary(changes=changes, grants=grants, filtered=True)

    @property
    def total(self) -> int:
        return len(self.changes) + len(self.grants)

    def counts(self) -> tuple[tuple[str, int], ...]:
        """`(object type, count)`, grouped and sorted.

        One row per object type, which is exactly the shape `export_db` prints
        under `OBJECTS OVERVIEW:`. The schema and the destructive tally used to
        be columns here and are not (Jan, `#769`): the overview answers *how much
        moved, and of what*, and the two extra columns pushed that answer into a
        table shaped like nothing else the tool prints.

        The type reads `TABLE 3`, in the singular `CHANGED OBJECTS:` and the two
        grant tables below also print (Jan, `#803`, taking back `#780`'s plural).
        """
        grouped: dict[str, int] = {}
        for change in self.changes:
            grouped[change.object_type] = grouped.get(change.object_type, 0) + 1
        if self.grants:
            grouped[GRANT_OBJECT_TYPE] = len(self.grants)
        return tuple(sorted(grouped.items()))


def summarize(inventory: Inventory | None) -> DiffSummary | None:
    """Turn a comparison of the two export trees into the screen's model.

    `None` in, `None` out: a comparison whose trees could not be read must not
    turn a successful run into a failure, and it must not be reported as
    `NO DIFFERENCES:` either, which is what an empty summary would say.
    """
    if inventory is None:
        return None
    changes = tuple(
        sorted(
            (
                DiffChange(
                    object_type = _object_type(folder),
                    object_name = _object_name(name),
                    status      = status,
                )
                for (folder, name), status in inventory.objects.items()
            ),
            key=_change_order,
        )
    )
    # The grants are left in tree order here: each of the two tables sorts itself
    # by ITS OWN leading column, which differ, so one sort at this level would be
    # wrong for one of them.
    return DiffSummary(changes=changes, grants=tuple(inventory.grants))


def _change_order(change: DiffChange) -> tuple[str, str, str]:
    """`(OBJECT TYPE, OBJECT NAME, STATUS)`, the columns in the order they print.

    Status led this key until `#790`'s second pass, and grouping by it is what
    made the listing read as unsorted: the object-type column restarted at every
    status block, so a reader scanning the leftmost column saw it run backwards
    three times. Sorting by the leading column is what "sorted" means to whoever
    is looking at the table.
    """
    return (change.object_type, change.object_name, change.status)


def _object_type(folder: str) -> str:
    return _OBJECT_TYPES.get(folder, singular_object_type(folder.replace("_", " ")))


def _object_name(name: str) -> str:
    return name.removesuffix(".sql").upper()


def _wildcards(patterns: Sequence[str]) -> list[str]:
    """`*` accepted for `%`, the rewrite `schema_selection` already documents.

    The pattern language is SQL LIKE everywhere on this tool, and `fnmatch` would
    read a `*` as a literal. No Oracle object name contains one, so accepting it
    costs nothing and spares a reader the one filter that means something else.
    """
    return [str(pattern).replace("*", "%") for pattern in patterns]


def _matches_type(object_type: str, patterns: Sequence[str]) -> bool:
    """Does this type match any `-type` pattern?

    Two spellings are tried: the name the listing prints, and that name resolved
    through the shared vocabulary, which is what makes `-type PACKAGE` mean the
    specification the way Oracle means it (`PACKAGE SPEC` resolves to `PACKAGE`).
    """
    if not patterns:
        return True
    canonical = normalize_object_type_pattern(object_type)
    return any(
        matches_sql_like(object_type, pattern) or matches_sql_like(canonical, pattern)
        for pattern in patterns
    )


def _matches_name(object_name: str, patterns: Sequence[str]) -> bool:
    if not patterns:
        return True
    return any(matches_sql_like(object_name, pattern) for pattern in patterns)


__all__ = [name for name in globals() if not name.startswith("_")]
