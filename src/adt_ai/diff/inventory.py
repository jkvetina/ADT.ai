"""Which side an object is on, read from the two export trees (ADT #790).

`diff` used to answer *what a change script would do* and print that verb in an
`ACTION` column: `REPLACE`, `ADD, DROP*`, `COMMENT`, and, where SQLcl emitted a
table plus its constraint, `CREATE, ADD`. Jan, on the fourth attempt at this
screen: *"What the action even means??? ... COMMENT COMMENT will tell me what?
SHIT. ... JOBS NULL tells me what? NOTHING."* The verb was never the question. A
person comparing two environments is asking which side is short of what.

**That question is answered by set membership, not by parsing DDL.** The two
`project export` trees ARE the two schemas' inventories, one file per object, and
they sit on disk in `runner._compare` for the length of the comparison. Comparing
them gives a status that cannot be misread:

* ``MISSING``  the source has it, the target does not.
* ``CHANGED``  both sides have it and the two files differ.
* ``EXTRA``    the target has it, the source does not.

Measured before it was worded (`#790`, SANDBOX/SANDBOX_DIFF, both directions):
an object present only on the SOURCE side comes back as a commented-out `DROP`
in the artifact, and one present only on the TARGET side as a `CREATE`, because
the artifact brings the source into line with the target. So source-only is what
the target is MISSING, which is the direction a reader means by the word.

Grants are compared one privilege at a time rather than one file at a time. A
grant is `(object, grantee, privilege)`, and SQLcl spells only the first two into
the file name, so a grant that exists on both sides with different privileges is
neither missing nor extra as a file while every privilege in it is exactly one or
the other.

**A grant has a DIRECTION, and the direction is what splits the two tables.** The
first cut split them by status and called the far end of the grant `PARTY`, which
Jan refused: *"PARTY ??? WTF is this? IT IS OWNER. I was wrong, we have incoming
and outgoing grants."* A grant INTO the compared schema (we are the grantee) is
somebody else's object, and the column worth its width is that object's `OWNER`;
a grant OUT of it (we are the owner) is our own object, and the column worth its
width is the `GRANTEE`. Two different questions about two different things, so
two tables, with MISSING/EXTRA demoted to a `STATUS` column inside each.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Where `project export` writes a schema's objects, relative to the project.
EXPORT_ROOT = Path("src") / "database"

#: The folder holding the object-grant scripts, in both trees and the artifact.
GRANTS_FOLDER = "object_grants"

MISSING = "MISSING"
CHANGED = "CHANGED"
EXTRA   = "EXTRA"

#: The order the LEGEND explains them in, and nothing else. This used to be the
#: listing's PRIMARY sort key, which is what made a filtered run look unsorted:
#: grouping by status restarts the object-type column at every group, so the one
#: column a reader scans ran `COMMENT, REF CONSTRAINT, TABLE, VIEW, COMMENT,
#: INDEX` down the page. Jan: *"Tables must be sorted, when -name filter is
#: applied, data are NOT sorted."* Status is a column now, so the tables sort by
#: what they show.
STATUS_ORDER = {CHANGED: 0, MISSING: 1, EXTRA: 2}

#: `GRANT ... ON` / `REVOKE ... ON`, the only place a privilege is written down.
_PRIVILEGES = re.compile(r"\b(?:GRANT|REVOKE)\s+(?P<privileges>.+?)\s+ON\b", re.I | re.S)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: `-- sqlcl_snapshot <path>:<sha>:null:drop`, the one line that names the
#: exporting project rather than the object.
_SNAPSHOT_HEADER = re.compile(r"^\s*--\s*sqlcl_snapshot\b.*$", re.M | re.I)


@dataclass(frozen=True)
class GrantRow:
    """One privilege set on one object for one grantee, and which side lacks it.

    Both ends are carried, and which one the screen prints is decided by
    `incoming`: the reader of an incoming grant wants the owner of the object
    somebody granted them, the reader of an outgoing grant wants who they granted
    their own object to. Storing only the far end, as `party` did, threw away the
    fact that makes that choice.
    """

    owner       : str
    grantee     : str
    object_type : str
    object_name : str
    privileges  : tuple[str, ...]
    status      : str
    #: True when the compared schema is the GRANTEE, so the grant comes in.
    incoming    : bool = False

    @property
    def counterparty(self) -> str:
        """The end of the grant the compared schema is not: its printed column."""
        return self.owner if self.incoming else self.grantee

    @property
    def privilege_label(self) -> str:
        """The first privilege, plus ` + N` for the rest.

        Jan asked for exactly this (`#790`): *"privilege as first one and ' + #'
        if there is more"*. A cell holding `SELECT, INSERT, UPDATE, DELETE` is
        what broke the row geometry on a real schema, and the count says there is
        more to read without spending the width to print it.
        """
        if not self.privileges:
            return ""
        first, *rest = self.privileges
        return f"{first} + {len(rest)}" if rest else first


@dataclass(frozen=True)
class Inventory:
    """The comparison of two export trees: object statuses, and grant rows."""

    #: `(folder, object name)` -> status, for everything but the grants.
    objects : dict[tuple[str, str], str]
    grants  : tuple[GrantRow, ...]

    @property
    def empty(self) -> bool:
        return not self.objects and not self.grants


def compare(
    source_dir: Path,
    target_dir: Path,
    *,
    schema: str = "",
    target_schema: str = "",
) -> Inventory:
    """Read both export trees and say which side each object is on.

    The two schema names are only ever used to decide which END of a grant to
    print and which way it points: the compared schema is on every grant row by
    definition, so naming it would spend a column saying one word down the page
    (Jan: *"we dont list the source schema, since that is obvious"*).

    BOTH names are needed, not just the source's. On a `-target-schema` run the
    two trees spell the compared schema differently, so a grant the target side
    owns would read as somebody else's object and land in the wrong table if only
    the source name were known.
    """
    source = _read(source_dir)
    target = _read(target_dir)

    objects: dict[tuple[str, str], str] = {}
    for key in sorted(set(source) | set(target)):
        if key[0] == GRANTS_FOLDER:
            continue
        in_source, in_target = key in source, key in target
        if in_source and not in_target:
            objects[key] = MISSING
        elif in_target and not in_source:
            objects[key] = EXTRA
        elif source[key] != target[key]:
            objects[key] = CHANGED
    ours = {name.upper() for name in (schema, target_schema) if name}
    return Inventory(objects=objects, grants=_grant_rows(source, target, ours))


def _read(export_dir: Path) -> dict[tuple[str, str], str]:
    """`(folder, name)` -> file text, for every object one side exported.

    The schema segment is dropped on purpose. It is the same word on every row of
    a normal comparison, and on a `-target-schema` run the two sides spell it
    differently, keeping it in the key would report every object as missing AND
    extra, which is the one answer that is never true.
    """
    root = export_dir / EXPORT_ROOT
    if not root.is_dir():
        return {}
    found: dict[tuple[str, str], str] = {}
    for path in sorted(root.rglob("*")):
        key = _key(root, path)
        if key is None:
            continue
        try:
            found[key] = _comparable(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:  # pragma: no cover - defensive, the tree was just written
            continue
    return found


def _key(root: Path, path: Path) -> tuple[str, str] | None:
    """`(folder, name)` for an exported object file, or `None` for anything else."""
    if not path.is_file():
        return None
    parts = path.relative_to(root).parts
    # `<schema>/<folder>/<name>` at least; anything shallower is scaffolding.
    if len(parts) < 3:
        return None
    return (parts[1].lower(), "/".join(parts[2:]).lower())


def _comparable(text: str) -> str:
    """The script with SQLcl's own snapshot header removed, and nothing else.

    Every generated file opens on `-- sqlcl_snapshot <path>:<sha>:…`, which
    carries the exporting project's path; the two sides export into directories
    of their own, so that one line differs on every file of every comparison and
    a raw byte compare would report the whole schema as `CHANGED`.

    Only that line goes. Stripping every `--` comment would have been the shorter
    rule and the wrong one: a comment inside a package body is part of the
    object, and two bodies differing only in their comments genuinely differ.
    """
    return _SNAPSHOT_HEADER.sub("", text).strip()


def _grant_rows(
    source: dict[tuple[str, str], str],
    target: dict[tuple[str, str], str],
    ours: set[str],
) -> tuple[GrantRow, ...]:
    rows: list[GrantRow] = []
    for key in sorted(set(source) | set(target)):
        if key[0] != GRANTS_FOLDER:
            continue
        parsed = _parse_grant(key[1], ours)
        if parsed is None:
            continue
        owner, grantee, object_type, object_name, incoming = parsed
        in_source = _privileges(source.get(key, ""))
        in_target = _privileges(target.get(key, ""))
        for status, privileges in (
            (MISSING, [p for p in in_source if p not in in_target]),
            (EXTRA,   [p for p in in_target if p not in in_source]),
        ):
            if privileges:
                rows.append(
                    GrantRow(
                        owner       = owner,
                        grantee     = grantee,
                        object_type = object_type,
                        object_name = object_name,
                        privileges  = tuple(privileges),
                        status      = status,
                        incoming    = incoming,
                    )
                )
    return tuple(rows)


#: SQLcl spells the whole grant into the file name and files it under the
#: grantor's schema: `OBJECT_GRANTS_AS_<ROLE>.<owner>.<TYPE>.<object>.TO_<grantee>`.
_GRANT_NAME = re.compile(
    r"^object_grants_as_[a-z]+\."
    r"(?P<owner>[^.]+)\.(?P<object_type>[^.]+)\.(?P<object_name>.+)\.to_(?P<grantee>[^.]+)$"
)


def _parse_grant(
    name: str, ours: set[str]
) -> tuple[str, str, str, str, bool] | None:
    """`(owner, grantee, object type, object name, incoming)`, or `None`.

    `incoming` is the whole point: the grant comes IN when the object belongs to
    somebody else, which is why `SYS` and `APEX_*` show up as owners, and goes OUT
    when the compared schema owns it. A schema granting to ITSELF reads as
    outgoing, because it is our object either way and the grantee is the column
    that carries information.
    """
    match = _GRANT_NAME.match(name.removesuffix(".sql"))
    if match is None:
        return None
    owner   = match["owner"].upper()
    grantee = match["grantee"].upper()
    return (
        owner,
        grantee,
        match["object_type"].replace("_", " ").upper(),
        match["object_name"].upper(),
        owner not in ours,
    )


def _privileges(script: str) -> tuple[str, ...]:
    """Every privilege the script grants or revokes, in statement order."""
    live = _BLOCK_COMMENT.sub(" ", script)
    found: list[str] = []
    for source in (live, script):
        for match in _PRIVILEGES.finditer(source):
            for privilege in match["privileges"].split(","):
                token = " ".join(privilege.split()).upper()
                if token and token not in found:
                    found.append(token)
        if found:
            break
    return tuple(found)


__all__ = [name for name in globals() if not name.startswith("_")]
