"""Recognising the one-off SQL `-create` generated, ADT #508.

Two questions live here and they are the same question asked by two callers.
`patch/helpers.py` WRITES a DROP script for a deleted object and an ALTER script
per table version step; `patch/scripts.py` has to decide, twice, whether a file
it is holding is one of those. `_recover_previous_scripts` asks it before
carrying a script forward out of an older folder (`#503`), and
`reset_patch_scripts` asks it before emptying a folder on a forced create
(`#508`). Neither may guess.

The seam is deliberate rather than a byte count, though the byte count is what
forced the cut, the same 20 000 byte context guard that took `object_identity`
out of `helpers.py` for `#499` and `table_alter` for `#494`. What is left in
`helpers.py` is the writers, which need a repo, a commit window and a config to
do their work. What is here is a pure naming contract: three spellings and the
two slots they are written into, no filesystem, no git, no config beyond the
project's own `object_types`, `patch_map` and slot postfixes. A reader that
only needs to recognise a name has no business importing the generator.

**Filename AND slot, never either alone.** A generated name is a shape, and a
person writing a one-off can land on the same shape by accident: `foo.2.sql` is
a reasonable name for the second step of a hand-written migration, and
`drop.obsolete_backup_rows.sql` is a reasonable name for a script that drops
rows. Reading either as a helper is not a cosmetic mistake, it deletes the only
copy of somebody's DDL, `#309` having unlinked the original the moment the first
`-create` moved it into the patch. So the callers pair a name test with the slot
the generator actually writes to, and the tests below stay narrow on purpose.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from adt_ai.patch import settings
from adt_ai.patch.files import _patch_map
from adt_ai.patch.layout import object_layouts

# `<stem>.<commit number>.sql` from the commit walk, `<stem>.hash.sql` from hash
# mode. Anchored whole, and the stem has to carry something, so a bare `216.sql`
# a person wrote is not read as a helper.
_ALTER_HELPER_RE = re.compile(r"^.+\.(?:[0-9]+|hash)\.sql$")


def linked_group(object_type: str | None, config: dict[str, Any]) -> str:
    """The `patch_map` group whose install-script section links this type's files.

    `objects` when no group claims the type, the fallback section every install
    script can open (`create._payload_groups`). `create._database_patch_group`
    asks this of a file's type, so a helper slot and the file it serves can
    never be placed by two readings of the map.
    """
    for group, object_types in _patch_map(config).items():
        if object_type in {item.upper() for item in object_types}:
            return group
    return "objects"


def alter_helper_slot(config: dict[str, Any]) -> str:
    """The slot the ALTER writers write into: `after` the group linking tables.

    Read off the config rather than spelled `tables_after` (ADT #923). The slot
    the install script links is `<group><patch_postfix_after>`, and
    `scripts._known_slots` reads it the same way since ADT #430, so a fixed name
    was UNKNOWN on any project with another postfix or its tables in another
    group: the ALTER stayed in the source folder and the change never shipped.
    The table group rather than the sequence group for a sequence ALTER too, so
    one slot keeps holding every generated ALTER (`sequence_alter.py`), and ADT
    #753 runs each one ahead of that group's files.
    """
    return settings.slot_name(linked_group("TABLE", config), "after", config)


def drop_helper_slot(config: dict[str, Any]) -> str:
    """The slot the DROP writer writes into: `after` the `objects` group.

    `objects` is the one group every install script can open, declared or not,
    so the slot always links; its postfix is the project's, for the reason
    `alter_helper_slot` gives (ADT #923).
    """
    return settings.slot_name("objects", "after", config)


def drop_helper_filename(object_type: str, object_name: str) -> str:
    """The one spelling of a generated DROP helper's filename.

    Named rather than inlined so `scripts.py` can recognise what the generator
    wrote without keeping a second copy of the shape (ADT #503). The space in
    `PACKAGE BODY` folds to `_` because a filename carrying one is a filename
    every later reader has to quote.
    """
    return f"drop.{object_type.replace(' ', '_').lower()}.{object_name.lower()}.sql"


def is_drop_helper_filename(name: str, config: Mapping[str, Any]) -> bool:
    """Whether `name` is a filename the DROP writer produces (ADT #503).

    The question recovery asks before carrying a script forward, and the reason
    it is asked at all is that a helper is derived from the patch window on
    every run, so a copy of one is stale by construction and the run that needs
    it writes it again.

    Keyed on the project's own `object_types`, never a remembered list, so a
    project spelling its types differently is read by its own spelling and the
    shipped `PACKAGE BODY` folds exactly as the writer folds it. The type token
    has to be a configured type AND carry a name behind it, which is what keeps
    an ordinary one-off out: `drop_autonomous_logger_fks.sql` and
    `drop.obsolete_backup_rows.sql` are both scripts a person wrote and named
    for what they do, and dropping those would be the over-broad half of the fix.
    """
    if not name.startswith("drop.") or not name.endswith(".sql"):
        return False
    middle = name[len("drop.") : -len(".sql")]
    return any(
        middle.startswith(prefix) and len(middle) > len(prefix)
        for prefix in (
            f"{object_type.replace(' ', '_').lower()}."
            for object_type in object_layouts(config.get("object_types", {}))
        )
    )


def is_alter_helper_filename(name: str) -> bool:
    """Whether `name` is a filename the ALTER writers produce (ADT #508).

    The DROP test above reads a project's configured object types because a DROP
    names an object. An ALTER names a table file's stem and a commit number, and
    neither is drawn from anything a project configures, so this one is the
    shape alone. That makes it the looser of the two, which is exactly why the
    module docstring's slot pairing is not optional here.
    """
    return bool(_ALTER_HELPER_RE.match(name))
