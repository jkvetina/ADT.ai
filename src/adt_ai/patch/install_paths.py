"""Where `patch -install` writes, and which schemas it writes for (ADT #804).

Jan, 2026-09-13, after `-install` wrote a script into every exported schema
folder with no way to pick them: *"Even if I create install script in some
stupid feature branch, it should be in config/install/<schema>.sql so I can see
the diff and I dont have super long names and million files there."* One folder,
one file per schema, the same name on every branch, so a regenerated script
reads as a diff rather than as a new file.

Split out of `patch/files.py`, which already sits at the repo's context-file
guard. Nothing here reads a file's contents; it answers paths and selections.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from adt_ai.patch.models import PatchError
from adt_ai.shared.internal_paths import config_dir
from adt_ai.shared.schema_selection import split_schema_values
from adt_ai.shared.sql_like import matches_sql_like

if TYPE_CHECKING:
    from adt_ai.patch.files import InstallTarget

#: The folder under `config/` holding one `<SCHEMA>.sql` per exported schema.
INSTALL_FOLDER = "install"

#: The script's name before ADT #804, written beside each schema's objects.
LEGACY_INSTALL_FILE = "INSTALL.sql"

#: What a `path_objects` layout naming no `<schema>` is written as, the label
#: `docs/patch_install.md` already gives that layout.
UNSCOPED_INSTALL_NAME = "DATABASE"


class SchemaSelectionError(PatchError):
    """A `-schema` value that matches no exported schema (ADT #807).

    A subclass rather than a plain `PatchError` so the CLI can tell a mistyped
    value from a patch that broke while running: the first is an
    `ARGUMENT INVALID` refusal exiting 2, the second stays on `PATCH FAILED:`.
    Jan, 2026-09-13, after `-schema GSN` landed on the wrong screen: *"This
    should be a normal error."*
    """

    def __init__(self, unmatched: Sequence[str], available: Sequence[str]) -> None:
        self.description = f"No exported schema matches -schema {', '.join(unmatched)}."
        self.details = f"Exported schemas: {', '.join(available) or 'none'}"
        super().__init__(f"{self.description} {self.details}")


def install_script_path(root: Path, schema: str) -> Path:
    """`config/install/<SCHEMA>.sql` for one install target."""
    return config_dir(root) / INSTALL_FOLDER / f"{schema or UNSCOPED_INSTALL_NAME}.sql"


def select_install_targets(
    targets: Sequence[InstallTarget],
    schemas: Any,
) -> list[InstallTarget]:
    """The targets `-schema` names, `%` patterns expanded; every target when it names none.

    A value matching no exported schema refuses the run and names what is on
    disk, rather than writing nothing and exiting clean: a typo would otherwise
    read as a project with nothing to install.
    """
    requested = [value.upper() for value in split_schema_values(schemas)]
    if not requested:
        return list(targets)
    available = [target.schema for target in targets if target.schema]
    chosen: set[str] = set()
    unmatched: list[str] = []
    for pattern in requested:
        wildcard = pattern.replace("*", "%")
        if "%" in wildcard:
            hits = [schema for schema in available if matches_sql_like(schema, wildcard)]
        else:
            hits = [schema for schema in available if schema == pattern]
        if not hits:
            unmatched.append(pattern)
        chosen.update(hits)
    if unmatched:
        raise SchemaSelectionError(unmatched, available)
    return [target for target in targets if target.schema in chosen]


def move_legacy_install_scripts(root: Path, targets: Sequence[InstallTarget]) -> None:
    """Carry a pre-#804 `<schema>/database/INSTALL.sql` into `config/install/`, silently.

    Jan, 2026-09-13: *"if you detect the database/INSTALL.sql file, you move it
    without asking or printing it."* Every target is swept, not only the ones
    this run regenerates, so a project's old scripts move in one run. A script
    already at the new place wins, because the old copy is the older of two
    generated files, and the old one is removed.
    """
    for target in targets:
        legacy = target.root / LEGACY_INSTALL_FILE
        if not legacy.is_file():
            continue
        destination = install_script_path(root, target.schema)
        if destination.exists():
            legacy.unlink()
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        legacy.replace(destination)


__all__ = [
    "INSTALL_FOLDER",
    "LEGACY_INSTALL_FILE",
    "UNSCOPED_INSTALL_NAME",
    "SchemaSelectionError",
    "install_script_path",
    "move_legacy_install_scripts",
    "select_install_targets",
]
