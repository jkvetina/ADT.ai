"""A user's own script claiming a table's ALTER, before Oracle is ever asked (ADT #969).

Jan, 2026-09-25: patch `ADT_AI_PATCH_1` shipped a generated ALTER for
`a_aplikace_cis` (a `CHECK` constraint and a new column) AND linked the table
file live, its `CREATE TABLE IF NOT EXISTS` and `COMMENT ON` lines included.
He wants the table replaced by its ALTER instead, the way old ADT did: an ALTER
can need data work a diff cannot express (a `NOT NULL` backfill), or be a
rename the diff reads as drop-and-add, so a project keeping its own
hand-written ALTER for a table wants that script to run and nothing else
generated over it.

**The scan reads content, never a filename shape.** `patch/generated_helpers.py`
already answers "is this name one the generator writes" for `scripts.py`'s
recovery and reset questions, and that answer is deliberately narrow, filename
AND slot together, because a hand-written script can land on the same shape by
accident. This module asks a different question at a different time: BEFORE
`helpers._write_table_diff_helpers` / `_write_hash_table_diff_helpers` write
anything, does ANY file already sitting in this patch's own
`patch_scripts/<CODE>/` source tree carry an `ALTER TABLE` naming the table
about to be diffed. That has to include a file shaped exactly like a generated
helper (`<table>.<n>.sql`, `<table>.hash.sql`): a project's own workflow is to
take the file `-create` wrote on an earlier run, edit it under
`patch_scripts/<CODE>/` to add the data work, and expect the next `-create` to
leave it alone rather than overwrite it at the same path. Filtering out
helper-shaped names here would defeat exactly that. A file the generator
itself is about to write over is never scanned twice for the same run, because
the caller checks and skips generation in the same pass this module answers.

Comment lines are stripped before matching, so an `ALTER TABLE` a person only
wrote ABOUT in a `--` note claims nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")

#: `ALTER TABLE`, an optional schema qualifier, then the table name, quoted or
#: not. Case-insensitive throughout, matching Jan's own spelling of the rule.
_SCHEMA_PREFIX = r'(?:"?[A-Za-z0-9_$#]+"?\s*\.\s*)?'


def _alter_table_pattern(table_name: str) -> re.Pattern[str]:
    name = re.escape(table_name)
    return re.compile(
        rf'\bALTER\s+TABLE\s+{_SCHEMA_PREFIX}"?{name}"?(?=[\s(;]|$)',
        re.IGNORECASE,
    )


def scan_user_scripts(script_root: Path, root: Path) -> list[tuple[str, str]]:
    """Every `.sql` file under ``script_root``, comments stripped, repo-path kept.

    Read once per `-create` run rather than once per table: a patch with
    several changed tables would otherwise re-read and re-strip the same files
    once per table it is about to consider.  ``script_root`` is the exact
    folder `helpers._write_generated_patch_scripts` already resolved
    (`_patch_scripts_folder`), so this reads the same tree the generator is
    about to write into, never a second resolution of it.
    """
    if not script_root.is_dir():
        return []
    texts: list[tuple[str, str]] = []
    for path in sorted(script_root.glob("**/*.sql")):
        # defensive: `glob("**/*.sql")` only ever matches a directory literally
        # named `*.sql`, which nothing under `patch_scripts/<CODE>/` creates
        if not path.is_file():  # pragma: no cover
            continue
        raw = path.read_text(encoding="utf-8", errors="replace")
        texts.append((_LINE_COMMENT_RE.sub("", raw), path.relative_to(root).as_posix()))
    return texts


def claiming_scripts(scripts: list[tuple[str, str]], table_name: str) -> tuple[str, ...]:
    """Repo paths of every scanned script carrying an `ALTER TABLE` on ``table_name``.

    Sorted, so a table two scripts both claim reports them in one stable order
    rather than in the order the filesystem happened to hand them back.
    """
    pattern = _alter_table_pattern(table_name)
    return tuple(sorted(path for text, path in scripts if pattern.search(text)))


__all__ = ["claiming_scripts", "scan_user_scripts"]
