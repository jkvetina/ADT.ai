"""Which files of an APEXlang tree a `patch -deploy -app` import names alone.

APEX 26.2 lets SQLcl import a subset of a tree, `apex import -files <paths>`,
and a deploy into the application's own id uses it to import only the `.apx`
files the patch changed (ADT #1068). Jan, 2026-10-09: *"-app should stay as it
is, since it is useful for AI app prototypes and rapid development. To deploy
just components INTO the same app there should be no extra flag."* So a
retarget keeps the whole tree, and `files_only` decides the rest from facts the
deploy already holds.

Measured on the 26.2 PDB (`tests/tools/apexlang_files_probe.py`, 2026-10-09):
a page imported alone lands and leaves the others standing; a page whose file
is gone is removed only by a whole-tree import; `-files` naming a missing file
is refused with exit 0; and SQLcl still compiles the whole tree, writing only
the files named. So anything short of "every listed path is an `.apx` still on
disk" imports the whole tree, as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adt_ai.patch.apex_app_lock import release_at_least
from adt_ai.patch.apex_signature import ApexSignatures
from adt_ai.shared.apex_payloads import IGNORE_NAME

# SQLcl's `apex import -files`: "This option is supported in APEX 26.2 and
# later", read out of `dbtools-apex.jar`'s `Help.properties`.
FILE_IMPORT_FROM = (26, 2)


def tree_files(apexlang_root: Path) -> int:
    """How many files a whole-tree import is about to carry, payload links included.

    Counted off the tree rather than summed from a staging result, because the tree
    IS what the import reads since ADT #765. The ignore file the payload folder
    carries is ADT.ai's own bookkeeping and is not one of them. Moved here from
    `apex_deploy.py` by ADT #1068, beside the count of a narrowed import.
    """
    return sum(
        1
        for path in apexlang_root.rglob("*")
        if path.is_file() and path.name != IGNORE_NAME
    )


def files_only(
    root        : Path,
    tree        : Path,
    patch_files : list[str],
    *,
    in_place    : bool,
    signatures  : ApexSignatures,
    gateway     : Any,
) -> tuple[Path, ...]:
    """The files of ``tree`` an import may name alone, or ``()`` for the whole tree.

    Narrowing is safe only when every one of these holds, and each fallback is
    the import as it was before:

    * **into the same application.** A retarget keeps the whole tree (Jan, see
      the module docstring), and a fresh sandbox has nothing for a subset to
      land on anyway;
    * **the target already holds it, undrifted.** A subset can only update an
      application that is there, and a `-force` over drift means overwrite,
      which a subset would not do;
    * **every path the patch lists under the tree is an `.apx` still on disk.**
      ``patch_files`` is the folder's NEW, MODIFIED and DELETED paths alike, so
      a listed path that is gone is a deletion, and only a whole-tree import
      removes a page; a payload or deployment file changed beside the pages is
      not a component the subset import was measured on;
    * **APEX 26.2 or later**, asked last because it is the one fact that costs
      a query.
    """
    if not in_place or not signatures.on_target or signatures.refused:
        return ()
    try:
        prefix = tree.resolve().relative_to(root.resolve()).as_posix() + "/"
    except ValueError:
        return ()
    listed = sorted({path[len(prefix):] for path in patch_files if path.startswith(prefix)})
    files = tuple(tree / path for path in listed)
    if not files or any(file.suffix != ".apx" or not file.is_file() for file in files):
        return ()
    if not release_at_least(gateway, FILE_IMPORT_FROM):
        return ()
    return files


def files_lines(staged: Path, files: tuple[Path, ...]) -> list[str]:
    """The import log rows naming what a narrowed import carried, none for a tree.

    A reader comparing the application with its tree needs to know the import
    did not write all of it. One file per row, the label on the first, in the
    column width of the `DEPLOYED FROM` row above.
    """
    return [
        f"--   {'IMPORTED FILES' if index == 0 else '':<16} | "
        f"{file.relative_to(staged).as_posix()}"
        for index, file in enumerate(files)
    ]


__all__ = [name for name in globals() if not name.startswith("_")]
