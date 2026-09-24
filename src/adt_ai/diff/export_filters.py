"""What ``-type`` / ``-name`` mean to SQLcl's exporter (ADT #780).

``#773`` filtered the `diff` SCREEN and deliberately left the artifact whole, so
a narrowed run still produced a zip anybody could review, share or deploy in
full. Jan overruled that once `diff` started scaffolding its own project:
*"If we are going to take this project burden, we can filter the type/name too!
At least is should be faster! And nobody asked you to have full schema changes
in zip!"* So both filters now reach the export, and the artifact is what was
asked for rather than the whole schema.

**The route in is a filters file, not an option.** ``project export`` takes
``-objects HR.COUNTRIES,HR.EMPLOYEES``, a list of literal, schema-qualified
names, which cannot carry ``-name ORD%``; it has no ``-type`` at all. What it
does read is ``.dbtools/filters/ddl.filters``, a comma-separated list of SQL
predicates applied to the dictionary queries the export runs, and the patterns
are already SQL LIKE. So a pattern goes in as the predicate it always was.

**A ``-name`` used to narrow four of the eleven dictionary queries** (ADT #790).
It went in as a bare ``object_name like ...``, and only `ALL_OBJECTS`,
`ALL_INDEXES`, `ALL_TRIGGERS` and `ALL_SYNONYMS` expose that column, so every
comment, every grant and every materialized-view log in the schema was still
exported, compared and zipped by a run that had asked for one object. Jan, after
``-name ABC%`` cost 51 seconds on a schema holding no ``ABC`` at all: *"Comment
is tight to the table or column, table_name is the object_name. Grant is tight to
an object, thats the object name. Index is tight to table/mview, thats the object
name... Exporting everything is stupid."*

So every family is narrowed now, each on the column carrying the object it hangs
off. Which column that is per query was **measured**, not guessed: ``project
export -debug`` prints every dictionary query with its filters applied, and one
run against the live SANDBOX schema showed where each candidate landed.

====================  =========================================================
query                 the column ``-name`` matches on
====================  =========================================================
`ALL_OBJECTS`         ``object_name``
`ALL_SYNONYMS`        ``object_name`` (the synonym's own name)
`ALL_INDEXES`         ``table_name`` (the table it indexes)
`ALL_TRIGGERS`        ``table_name`` (the table it fires on)
`ALL_TAB_PRIVS`       ``table_name`` (the object granted)
`ALL_TAB_COMMENTS`    ``name`` (the table commented)
`ALL_COL_COMMENTS`    ``name`` (the table whose column is commented)
`ALL_MVIEW_LOGS`      ``master`` (the table logged)
====================  =========================================================

Each is written ``export_type not in (<families>) or <column> like ...`` so
exactly one column narrows each family. The guard is load-bearing: a bare
``table_name like`` also reaches `ALL_INDEXES` and `ALL_TRIGGERS`, which already
carry ``object_name``, and an index would then have to match on BOTH its own name
and its table's. The same run proved ``export_type`` is readable in every one of
these queries, and that an **unqualified** column a query does not have is
skipped silently while a **qualified** one is a hard error
(``all_synonyms.synonym_name`` failed the export outright, because that query
aliases the column to ``object_name``).

Two more predicates carry the rest of the job:

* ``all_objects.object_type like ...`` narrows the types, qualified to the one
  view that has the column, so it never reaches the index, trigger or grant
  queries and quietly empty them.
* ``export_type not in (...)`` drops the whole families a ``-type`` did not ask
  for. Comments, grants, indexes, triggers and synonyms are separate queries
  over views with no ``object_type`` column at all, so nothing above can touch
  them and a ``-type TABLE`` run would otherwise still zip every grant.

The exclusion is written as NOT IN rather than IN on purpose. ``export_type``
also names scaffolding this module has no opinion about (``ALL_DEPENDENCIES``
orders the install script), and an include-list would drop each one the day
SQLcl adds it.

**The APEX application and the ORDS definition are excluded on every run**,
filtered or not. `#779` and `#780` took both off the SCREEN on the ground that
neither can name a difference, SQLcl writes the application's install script and
the schema's whole ORDS definition whether or not the two sides differ, but they
were still exported, still compared and still paid for on every run. Measured on
SANDBOX: 42 of the 163 files a side exports were the APEX application, and a
schema compared with ITSELF reported a phantom difference because a working copy
existed during one export and not the other. Jan: *"Remove the APEX + ORDS
compare, that is useless."* Comparing those surfaces for real is `#778`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.object_types import (
    ORACLE_OBJECT_TYPES,
    normalize_object_type_patterns,
)
from adt_ai.shared.sql_like import matches_sql_like

#: Where `project init` leaves the customisable filters, relative to the project.
#: The file ships with SQLcl's own exclusions in it (Liquibase's own tables, Data
#: Modeler's `DM$` objects, materialized-view index names), which is why this
#: module PREPENDS rather than writes: those exclusions are still wanted, and a
#: `-filter <file>` of our own would replace them wholesale.
FILTERS_PATH = Path(".dbtools") / "filters" / "ddl.filters"

#: ADT type name -> the `export_type` families that produce it. Only the types
#: SQLcl exports through a query of their own are here; everything else comes off
#: `ALL_OBJECTS` and is narrowed by `object_type` instead. `COMMENT` is in the
#: table although it is not an Oracle object type, because it is a row the `diff`
#: summary prints and therefore something `-type` can name.
#:
#: `ORDS` was a row here until `#790`. It named a family `-type ORDS` could ask
#: for, and the screen has refused to print an ORDS row since `#780`, so the flag
#: could only ever produce an empty listing and a slower run. The family is in
#: `UNCOMPARED_FAMILIES` below now, excluded on every run.
_EXPORT_FAMILIES = {
    "COMMENT"   : ("ALL_COL_COMMENTS", "ALL_TAB_COMMENTS"),
    "GRANT"     : ("ALL_TAB_PRIVS",),
    "INDEX"     : ("ALL_INDEXES",),
    "MVIEW LOG" : ("ALL_MVIEW_LOGS",),
    "SYNONYM"   : ("ALL_SYNONYMS",),
    "TRIGGER"   : ("ALL_TRIGGERS",),
}

#: The vocabulary left over, i.e. the types a `-type` reaches through
#: `all_objects`. A pattern matching one of these keeps `ALL_OBJECTS` in the
#: export; so does a pattern matching nothing at all, because SQLcl exports types
#: this vocabulary has never heard of (`CONTEXT`, `PROGRAM`, `JOB CLASS`) and the
#: safe answer for an unknown name is the family that can still narrow it.
_ALL_OBJECTS_TYPES = frozenset(ORACLE_OBJECT_TYPES) - set(_EXPORT_FAMILIES)

#: The `export_type` families a `-name` narrows, grouped by the column each of
#: their queries carries the hung-off object in. Measured through `project export
#: -debug`; see the table in the module docstring. A family absent from here has
#: no name to match on at all (`ALL_CONSTRAINTS` orders the install script,
#: `ALL_QUEUE_TABLES` names nothing a `-name` means) and is left alone.
_NAME_COLUMNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("object_name", ("ALL_OBJECTS", "ALL_SYNONYMS")),
    ("table_name",  ("ALL_TAB_PRIVS", "ALL_INDEXES", "ALL_TRIGGERS")),
    ("name",        ("ALL_TAB_COMMENTS", "ALL_COL_COMMENTS")),
    ("master",      ("ALL_MVIEW_LOGS",)),
)

#: Written on every run, filtered or not: neither payload can name a difference,
#: so exporting either is time spent on rows the screen already refuses to print
#: (`#779`, `#780`, and the measurement in the module docstring).
UNCOMPARED_FAMILIES = ("APEX", "APEX_APPLICATIONS", "ORDS_SCHEMA")


def build_predicates(
    *,
    names: Sequence[str] = (),
    types: Sequence[str] = (),
) -> list[str]:
    """The SQL predicates this run becomes, in the order they are written.

    Never empty: the APEX/ORDS exclusion is unconditional, so there is always a
    filters file to write, which is why `apply` no longer has an "asked for
    everything" early exit.
    """
    predicates: list[str] = [_not_in("export_type", UNCOMPARED_FAMILIES)]
    if names:
        # Upper-cased the way `export_db -name` binds a name, since Oracle's
        # `like` is case-sensitive and the screen filter is not: `-name orders`
        # exported nothing and then reported no differences (#923).
        patterns = [pattern.upper() for pattern in _wildcards(names)]
        predicates.extend(
            f"({_not_in('export_type', families)} or {_any_like(column, patterns)})"
            for column, families in _NAME_COLUMNS
        )
    if types:
        predicates.extend(_type_predicates(normalize_object_type_patterns(_wildcards(types))))
    return predicates


def _not_in(column: str, values: Sequence[str]) -> str:
    joined = ", ".join(f"'{_quote(value)}'" for value in sorted(values))
    return f"{column} not in ({joined})"


def apply(project_dir: Path, *, names: Sequence[str] = (), types: Sequence[str] = ()) -> bool:
    """Prepend the predicates to the project's filters file; say whether any were.

    Prepended rather than appended because the predicates are comma-separated and
    the shipped file ends in comments: a new clause after them would have to work
    out whether the last thing before it was a predicate needing a comma. At the
    top there is no such question, and SQLcl reads the list in no particular
    order anyway.

    A missing filters file is not an error. `project init` writes one, so its
    absence means SQLcl changed where it keeps them, and a comparison that still
    answers correctly (with the screen filter doing the narrowing, as it did
    before `#780`) is worth more than one that refuses over a file it only wanted
    to make faster.

    There is no longer an "asked for everything" early exit: `build_predicates`
    always carries the APEX/ORDS exclusion, so every run writes the file.
    """
    predicates = build_predicates(names=names, types=types)
    filters = project_dir / FILTERS_PATH
    if not filters.is_file():
        return False
    existing = filters.read_text(encoding="utf-8")
    block = "\n".join(f"{predicate}," for predicate in predicates)
    text_files.write_text(filters, f"{_HEADER}\n{block}\n\n{existing}")
    return True


#: A `-- ` comment inside a generated file is code rather than user-facing text,
#: and this one is worth its line: `project export -debug` prints the dictionary
#: queries with the filters applied, so a reader of that transcript meets these
#: predicates with no idea where they came from otherwise.
_HEADER = "-- ADT.ai: the -type / -name filters this diff was asked for"


def _type_predicates(patterns: Sequence[str]) -> list[str]:
    """The type half: narrow `all_objects`, and drop the families not asked for."""
    predicates: list[str] = []
    if _wants_all_objects(patterns):
        predicates.append(_any_like("all_objects.object_type", patterns))
    excluded = sorted(
        family
        for name, families in _EXPORT_FAMILIES.items()
        if not _matches_any(name, patterns)
        for family in families
    )
    if not _wants_all_objects(patterns):
        # Nothing asked for comes off `all_objects`, so the whole family goes.
        # Without this a `-type GRANT` run would export every object in the
        # schema and then print a screen holding one grant.
        excluded.append("ALL_OBJECTS")
    if excluded:
        predicates.append(_not_in("export_type", excluded))
    return predicates


def _wants_all_objects(patterns: Sequence[str]) -> bool:
    """Could any pattern name a type that comes off `all_objects`?

    True when a pattern matches the known vocabulary, and also when it matches
    NOTHING known: SQLcl exports object types ADT has no name for, and reading an
    unrecognised `-type` as "not an object" would silently return an empty
    artifact for a filter that is merely unfamiliar.
    """
    return any(
        _matches_any_of(_ALL_OBJECTS_TYPES, pattern)
        or not _matches_any_of(_EXPORT_FAMILIES, pattern)
        for pattern in patterns
    )


def _matches_any(name: str, patterns: Sequence[str]) -> bool:
    return any(matches_sql_like(name, pattern) for pattern in patterns)


def _matches_any_of(names: Iterable[str], pattern: str) -> bool:
    return any(matches_sql_like(name, pattern) for name in names)


def _any_like(column: str, patterns: Sequence[str]) -> str:
    """`(col like 'A' or col like 'B')`, parenthesised so OR cannot leak out.

    The filters file is a list of predicates ANDed together, so an unbracketed OR
    would bind against its neighbours and widen the export instead of narrowing
    it.
    """
    clauses = " or ".join(f"{column} like '{_quote(pattern)}'" for pattern in patterns)
    return f"({clauses})"


def _quote(value: str) -> str:
    """A single quote doubled, the one thing that could close the literal early.

    Oracle has no object type containing one and an object name needs quoting to,
    so this is a correctness backstop rather than a path anybody walks.
    """
    return value.replace("'", "''")


def _wildcards(patterns: Sequence[str]) -> list[str]:
    """`*` accepted for `%`, exactly as `DiffSummary.select` accepts it.

    The two filters have to read the same pattern the same way, or `-name ORD*`
    would narrow the screen and not the export.
    """
    return [str(pattern).replace("*", "%") for pattern in patterns]


__all__ = [name for name in globals() if not name.startswith("_")]
