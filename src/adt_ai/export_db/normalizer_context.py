"""What a normalizer is told about the object it is rewriting.

Split out of `normalizers.py` when `#679` pushed that module past the 20 KB
context cap `tests/export_db/test_normalizer_structure.py` pins. It sits beside
`normalizer_clauses.py` and imports nothing from `normalizers`, so the object
normalizers can keep importing both names from there unchanged.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class NormalizationContext:
    object_type      : str
    object_name      : str
    object_owner     : str | None = None
    add_if_not_exists: bool = True
    keep_owner       : bool = False
    #: Whether a VIEW or MATERIALIZED VIEW keeps the `(col, col, ...)` list
    #: DBMS_METADATA emits after its name. False drops it and lets the query
    #: below imply the columns, which is old ADT's behaviour and the default.
    keep_view_column_names: bool = False
    #: How the object's own FILE spells its name, when one already exists on
    #: disk. `None` means nobody asked, and every rendering falls back to the
    #: lowercase default, which is what `display_name` below encodes, so a
    #: caller never has to know which of the two it got.
    object_display_name: str | None = None

    @property
    def display_name(self) -> str:
        """The object's name as it should be RENDERED into its own file.

        Every generated spelling of the object's own name goes through this
        rather than `object_name.lower()`: a user who renamed `app_users.sql`
        to `App_Users.sql` did it on purpose, and a file whose contents
        contradict its own name is the defect (`#679`). Reading is unaffected
        (an unquoted Oracle identifier is case-insensitive), so this is
        spelling, never behaviour.

        Deliberately NOT applied to the uppercase `DROP` preambles: those were
        never the lowercase default, they are old-ADT parity pins, and following
        the file there would rewrite DDL nobody asked about.
        """
        return self.object_display_name or self.object_name.lower()


Normalizer = Callable[[list[str], NormalizationContext], list[str]]


def qualified(name: str, context: NormalizationContext) -> str:
    """`name` with the object's owner in front, when `keep_owner` is set.

    Every normalizer that builds its own `CREATE` or `DROP` line rather than
    editing the one the dictionary returned goes through this, because those
    lines are assembled from `context.object_name` and would otherwise be the
    only unqualified statements in an otherwise qualified repository.

    The owner follows the case of `name`, so a lower-cased `CREATE` line and an
    upper-cased `DROP` stay internally consistent.
    """
    if not (context.keep_owner and context.object_owner):
        return name
    owner = context.object_owner
    return f"{owner.upper() if name.isupper() else owner.lower()}.{name}"
