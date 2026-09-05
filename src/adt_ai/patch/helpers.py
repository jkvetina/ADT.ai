"""The one-off SQL `-create` generates for a patch, written into `patch_scripts_dir`.

Two kinds, both old-ADT behaviour: a DROP script for an object whose file the
patch window deleted (patch.py:1327-1352) and an ALTER script per adjacent pair of
table versions (patch.py:1354-1373). Neither goes into the install script here,
`templates._script_payload` links whatever ends up in that folder, which is the
seam this file sits on and the one ADT #18 found broken: the writer substituted
`{$PATCH_CODE}` into the folder path and the reader did not, so nothing written
here was ever injected.

Split out of `create.py` when it crossed the 20 KB context guard (ADT #287). The
seam is the question each half answers, what SQL does this patch need generated
for it, versus how is the install script assembled, not the byte count that
forced the split.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import queries
from adt_ai.patch.files import _patch_scripts_folder

# The naming contract for what this module writes moved to
# `patch/generated_helpers.py` with ADT #508, the third cut this file has taken
# at the 20 000 byte context guard (`#494` took `table_alter`, `#499` took
# `object_identity`). Re-exported rather than relocated at every call site:
# `scripts.py` and the tests reach for these by name.
from adt_ai.patch.generated_helpers import (  # noqa: F401 (re-exported for existing importers)
    ALTER_HELPER_SLOT,
    DROP_HELPER_SLOT,
    drop_helper_filename,
    is_alter_helper_filename,
    is_drop_helper_filename,
)
from adt_ai.patch.layout import (
    database_object_stem as _database_object_stem,
)
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.models import AlterHelper, GeneratedScripts

# Reading a repo path's object identity and whether it is still exported
# anywhere moved to `patch/object_identity.py` with ADT #499, the same 20 000
# byte context guard `table_alter.py` split out of this file for below. Named
# here rather than relocated at every call site: `create.py`, `summary.py` and
# `report.py` already import these from `helpers`, and the tests reach for
# them by name.
from adt_ai.patch.object_identity import (  # noqa: F401 (re-exported for existing importers)
    _object_exported_elsewhere,
    _object_identity,
    _path_is_deleted,
)

# Reading two `CREATE TABLE` versions into ALTER statements moved to
# `patch/table_alter.py` with ADT #494, the second time this family has been cut
# out at the 20 000 byte context guard (`#287` cut it out of `create.py`). Named
# here rather than relocated at every call site: `create.py` re-exports these
# four for existing importers, and the tests reach for them by name.
from adt_ai.patch.table_alter import (  # noqa: F401 (re-exported for existing importers)
    _parse_table_columns,
    _split_sql_columns,
    _table_alter_sql,
    _table_baseline,
    _table_versions,
)
from adt_ai.shared import text_files
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.shared.git_files import git_show
from adt_ai.shared.sql_identifiers import safe_identifier, safe_object_type


def _write_generated_patch_scripts(
    root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    patch_code: str,
    hash_previous: Mapping[str, str] | None = None,
    window: list[CommitRecord] | None = None,
) -> GeneratedScripts:
    """Write the one-off SQL, and report what was written.

    The return value feeds `TABLE CHANGES DETECTED:`, the `[ALT:n]` marker, and
    the exclusion the `UNCOMMITTED FILES` warning needs (ADT #276). It is the
    writer's own answer rather than a directory scan at report time:
    `tables_after/` accumulates helpers across patches, so a scan would credit
    this run with every helper any previous one generated.

    ``hash_previous`` switches the ALTER half to hash mode (ADT #447): the
    baseline hash per changed file, which names the version the target database
    actually holds. That is a better comparison base than the commit walk below,
    not a workaround for one, and it is the only base available when the patch
    selected no commit range to walk.
    """
    if not config.get("patch_add_scripts", True):
        return GeneratedScripts(alters=[], paths=[])
    script_root = _patch_scripts_folder(root, config, patch_code)
    drops = _write_drop_helpers(root, script_root, files, records, config)
    unresolved: list[str] = []
    if hash_previous is None:
        alters = _write_table_diff_helpers(root, script_root, files, records, config)
    else:
        alters, unresolved = _write_hash_table_diff_helpers(
            root,
            script_root,
            files,
            window if window is not None else records,
            config,
            hash_previous,
        )
    return GeneratedScripts(
        alters            = alters,
        paths             = [*drops, *(helper.path for helper in alters)],
        unresolved_tables = unresolved,
    )

def _write_hash_table_diff_helpers(
    root: Path,
    script_root: Path,
    files: list[str],
    window: list[CommitRecord],
    config: dict[str, Any],
    previous_hashes: Mapping[str, str],
) -> tuple[list[AlterHelper], list[str]]:
    """One ALTER step per table: what the target holds, to what this patch ships.

    Hash mode selects a file set rather than a commit range, so there are no
    in-patch version pairs to walk and `_table_baseline`'s "parent of the first
    selected commit" has no first selected commit to read. The baseline answers
    it directly instead: the version the target database holds is the one whose
    content hashed to the value the baseline recorded, so the previous body is
    read from the newest commit in the scanned history carrying exactly that
    hash. One function, one hash, no conversion, which is the whole reason
    `file_payload_hash` is used on both sides, and since ADT #454 the whole
    reason it canonicalizes: a working tree carrying CRLF has to reach the same
    value the commit store recorded, or no table ever finds its base.

    Two files earn no helper and neither is an error. A table absent from the
    baseline is new to the target, so the `CREATE TABLE` this patch already
    ships is the whole statement. A table whose recorded version is no longer in
    the scanned history is REPORTED, because that is a comparison ADT cannot
    make and a missing `ALTER` would fail the deploy silently.
    """
    table_files = {
        file
        for file in files
        if _database_object_type(file, config) == "TABLE"
    }
    written: list[AlterHelper] = []
    unresolved: list[str] = []
    for file in sorted(table_files):
        baseline_hash = previous_hashes.get(file)
        if not baseline_hash:
            continue
        previous = _body_at_content_hash(root, file, window, baseline_hash)
        if previous is None:
            unresolved.append(file)
            continue
        current_path = root / file
        if not current_path.is_file():
            continue
        # The working tree, because hash mode forces the `local` content mode:
        # what was compared against the baseline is what the patch ships, so it
        # is also what the ALTER has to reach.
        current = current_path.read_text(encoding="utf-8")
        # The stem, not the upper-cased name: this one is rendered into DDL, and
        # a generated patch script is a compatibility contract.
        table_name = _database_object_stem(file, config)
        # defensive: `file` already resolved a non-None TABLE type off the same `object_types`
        # layout, so `_database_object_stem` cannot itself resolve empty
        if not table_name:  # pragma: no cover
            continue
        sql = _table_alter_sql(table_name, previous, current)
        if not sql:
            continue
        folder = script_root / ALTER_HELPER_SLOT
        folder.mkdir(parents=True, exist_ok=True)
        helper = folder / f"{Path(file).stem}.hash.sql"
        text_files.write_text(helper, sql)
        written.append(
            AlterHelper(
                source     = file,
                path       = helper.relative_to(root).as_posix(),
                statements = len([line for line in sql.splitlines() if line.strip()]),
            )
        )
    return written, unresolved

def _body_at_content_hash(
    root: Path,
    file: str,
    window: list[CommitRecord],
    content_hash: str,
) -> str | None:
    """``file`` as it looked when its content hashed to ``content_hash``.

    Searched newest first, because the same content can appear at several
    commits and the newest is the one whose blob is cheapest to reach and
    likeliest still present. `None` when no scanned commit recorded that hash,
    which is the case the caller reports rather than guesses at.
    """
    for record in sorted(window, key=lambda item: item.number, reverse=True):
        if record.files.get(file) != content_hash or not record.commit_hash:
            continue
        content = git_show(root, record.commit_hash, file)
        if content is not None:
            return content.decode("utf-8")
    return None

def _write_drop_helpers(
    root: Path,
    script_root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
) -> list[str]:
    """Write a DROP script per object THIS patch window deletes.

    The window, not the filesystem, decides (ADT #290). `(root / file).exists()`
    alone answers "gone now", which is a different question: a file some LATER
    commit deleted, outside the selection, is equally absent and used to earn a
    helper this patch never made, shipping the object's content and a script
    dropping it in the same folder. Old ADT read the diff between the baseline
    commit and the window's last commit instead (patch.py:1333) and never had
    that failure.

    Deliberate divergence from old ADT, decided on ADT.ai's merits: an object the
    window BOTH created and deleted earns no helper either. The database this
    patch deploys onto sits at the pre-window state and has no such object, so
    the DROP is a no-op at best and hits an unrelated same-named object at worst.
    Old ADT emitted it, its `first_commit <= self.first_commit_id` gate
    (patch.py:1338-1341) reads like it covers this and does not: `relevant_comms`
    only holds ids from `relevant_commits` and `first_commit_id` only ever moves
    down from `min(relevant_commits) - 1`, so the comparison is false in every
    window that does not open at the edge of known history.

    **The unit is the OBJECT, never the file path (ADT #498).** A `DROP` names an
    object, so every question here has to be asked about the object: an
    `export_db -groups` move is a delete at one path plus an add at another, and
    both path-keyed tests above pass it through. That shipped a patch installing
    63 relocated objects and then dropping every one of them, `DROP TABLE` on two
    live tables included. So the created-by-window test compares identities, and
    the disk test asks whether the object is still exported ANYWHERE under its
    type folder rather than whether its old filename is still there.
    """
    deleted_by_window = {file for record in records for file in record.deleted_files}
    created_identities = {
        identity
        for record in records
        for file, status in record.file_statuses.items()
        if status == "A"
        and (identity := _object_identity(file, config)) is not None
    }
    on_disk: dict[tuple[str, ...], set[tuple[str, str, str]]] = {}
    written: list[str] = []
    for file in files:
        if file not in deleted_by_window:
            continue
        # `_object_identity` resolves through the configured `path_objects`
        # layout, so a path outside it answers None here. Requiring the first
        # part to be literally `database` on top of that was a condition old
        # ADT's drop loop never had (patch.py:1327-1352) and one the SHIPPED
        # default `<schema>/database/<object_type>/` can never satisfy, part 0 is
        # the schema, so every project on the default layout silently got no DROP
        # helper (ADT #287). Same hardcoded assumption ADT #196 lifted out of
        # `layout.py`, surviving at the one call site that sweep missed.
        #
        # It reads the name through the type's own extension, never `Path.stem`,
        # which strips one suffix and turned `core.spec.sql` into the `CORE.SPEC`
        # that `safe_identifier` refuses (ADT #471). This loop spelled the tuple
        # out a second time beside the import that already builds it (ADT #554).
        identity = _object_identity(file, config)
        if identity is None:
            continue
        _schema, object_type, object_name = identity
        if object_type == "GRANT":
            continue
        if identity in created_identities:
            continue
        if _object_exported_elsewhere(root, file, identity, config, on_disk):
            continue
        folder = script_root / DROP_HELPER_SLOT
        folder.mkdir(parents=True, exist_ok=True)
        helper = folder / drop_helper_filename(object_type, object_name)
        text_files.write_text(helper, _drop_helper_sql(object_type, object_name))
        written.append(helper.relative_to(root).as_posix())
    return written

def _drop_helper_sql(object_type: str, object_name: str) -> str:
    safe_object_type(object_type, role="object type")
    safe_identifier(object_name, role="object name")
    return queries.DROP_HELPER_TEMPLATE.format(
        object_type = object_type,
        object_name = object_name,
        statement   = f"DROP {object_type} {object_name}",
    )

def _write_table_diff_helpers(
    root: Path,
    script_root: Path,
    files: list[str],
    records: list[CommitRecord],
    config: dict[str, Any],
) -> list[AlterHelper]:
    """Write one ALTER script per version step a table takes in this patch.

    The step the window cannot see for itself is the FIRST one, and it is the one
    a real patch is usually built for (ADT #391). Pairing in-window versions
    against each other alone required the comparison version to be inside the
    patch, so a patch built for the commit that added a column held one version
    of that table, produced zero pairs and wrote nothing: no script, no
    `TABLE CHANGES DETECTED:`, no `[ALT:n]`. Old ADT diffed `commit_num - 1`
    against `commit_num` (patch.py:1371), the previous commit in global history
    rather than one the patch selected, so it never had the hole.

    `_table_baseline` restores that reach. It resolves per file, not per patch,
    because the pairs are per file: a window spanning ten commits still leaves a
    table only its first commit touches with a single version.
    """
    table_files = {
        file
        for file in files
        if _database_object_type(file, config) == "TABLE"
    }
    written: list[AlterHelper] = []
    for file in sorted(table_files):
        versions = _table_versions(root, file, records)
        if not versions:
            continue
        # Read through the configured extension, never `Path.stem`:
        # `_table_alter_sql` validates this with `safe_identifier`, so a project
        # configuring a compound extension for TABLE would meet ADT #471's crash
        # one object type over. The STEM, so the casing the file carries reaches
        # the generated DDL unchanged.
        table_name = _database_object_stem(file, config)
        # defensive: `file` already resolved a non-None TABLE type off the same `object_types`
        # layout, so `_database_object_stem` cannot itself resolve empty
        if not table_name:  # pragma: no cover
            continue
        # One `previous` per version, oldest first: the baseline in front, then
        # each version standing in for the one after it.
        previous_bodies = [
            _table_baseline(root, file, records),
            *(body for _, body in versions[:-1]),
        ]
        for previous, (number, current) in zip(previous_bodies, versions, strict=True):
            # No baseline means the target database has no such table, so the
            # `CREATE` this patch already ships is the whole statement needed.
            if previous is None:
                continue
            sql = _table_alter_sql(table_name, previous, current)
            if not sql:
                continue
            folder = script_root / ALTER_HELPER_SLOT
            folder.mkdir(parents=True, exist_ok=True)
            helper = folder / f"{Path(file).stem}.{number}.sql"
            text_files.write_text(helper, sql)
            written.append(
                AlterHelper(
                    source     = file,
                    path       = helper.relative_to(root).as_posix(),
                    statements = len([line for line in sql.splitlines() if line.strip()]),
                )
            )
    return written

__all__ = [
    "ALTER_HELPER_SLOT",
    "AlterHelper",
    "Any",
    "CommitRecord",
    "DROP_HELPER_SLOT",
    "GeneratedScripts",
    "Mapping",
    "Path",
    "_drop_helper_sql",
    "_parse_table_columns",
    "_path_is_deleted",
    "_split_sql_columns",
    "_table_alter_sql",
    "_table_versions",
    "_write_drop_helpers",
    "_write_generated_patch_scripts",
    "_write_table_diff_helpers",
    "annotations",
    "drop_helper_filename",
    "git_show",
    "is_alter_helper_filename",
    "is_drop_helper_filename",
    "queries",
    "safe_identifier",
    "safe_object_type",
    "text_files",
]
