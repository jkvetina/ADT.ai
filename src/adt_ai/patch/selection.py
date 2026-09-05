"""WHICH files a patch involves, in which group, in what order.

Split out of `create.py` when ADT #430 pushed it past the 20 KB context guard
(`tests/contracts/test_context_file_size.py`), the third half that module has shed
after `helpers.py` (#254), `templates.py` (#254) and `summary.py` (#258). The seam
is the question each half answers: this decides what goes into a patch, while
`create.py` decides what the generated scripts SAY about it.

`create.py` re-exports everything below, so every existing importer keeps working
unchanged.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.patch import settings as _settings
from adt_ai.patch.files import (
    _apex_page_id,
    _is_apex_end_environment,
    _is_apex_page,
    _is_apex_set_environment,
    _patch_map,
)
from adt_ai.patch.full_app import (
    is_full_app as _is_full_app,
)
from adt_ai.patch.full_app import (
    ships_in_patch as _ships_in_patch,
)
from adt_ai.patch.layout import (
    apex_app_id as _apex_app_id,
)
from adt_ai.patch.layout import (
    apex_app_root as _apex_app_root,
)
from adt_ai.patch.layout import (
    database_object_type as _database_object_type,
)
from adt_ai.patch.layout import (
    database_schema as _database_schema,
)
from adt_ai.patch.layout import (
    is_apex_path as _is_apex_path,
)
from adt_ai.patch.layout import (
    is_database_path as _is_database_path,
)
from adt_ai.patch.layout import (
    is_rest_path as _is_rest_path,
)
from adt_ai.patch.models import PatchFileSelection
from adt_ai.shared.commit_discovery import CommitRecord


def _refresh_database_files(files: list[str], config: dict[str, Any]) -> list[str]:
    # The layout answers this, never a literal first path part, the same
    # hardcode ADT #287 removed from the drop-helper writer, swept here in the
    # same pass per SOP §Console output contract ("fix parity gaps repo-wide").
    # On the shipped `<schema>/database/<object_type>/` default the old test made
    # `patch -refresh` list nothing at all.
    return sorted(file for file in files if _is_database_path(file, config))

def _refresh_apex_components(files: list[str], config: dict[str, Any]) -> dict[str, list[str]]:
    # The application id comes off the folder `apex_path_app` named, never off a
    # fixed segment index: under the shipped `<schema>/apex/` layout parts[1] is
    # the literal `apex` and parts[0] the schema (ADT #429).
    components: dict[str, set[str]] = {}
    for file in files:
        app_id = _apex_app_id(file, config)
        page_id = _apex_page_id(file)
        if app_id is None or page_id is None:
            continue
        components.setdefault(str(app_id), set()).add(f"PAGE:{page_id}")
    return {
        app_id: sorted(values, key=_component_sort_key)
        for app_id, values in sorted(components.items())
    }

def _component_sort_key(value: str) -> tuple[str, int | str]:
    prefix, _, suffix = value.partition(":")
    return prefix, int(suffix) if suffix.isdigit() else suffix

def _patch_files(
    root: Path,
    records: list[CommitRecord],
    config: dict[str, Any],
    *,
    full_app_ids: list[int] | None,
) -> PatchFileSelection:
    # `apex_files_ignore` (#430), the why in `patch/settings.py`. APEX paths only,
    # a database file matching one of those patterns by coincidence is not what
    # the key is about. The two environment scripts it drops are re-added below by
    # `_apex_copy_files`, which is `apex_files_copy`'s whole job: old ADT kept the
    # same file out of the CHANGE set and in the snapshot folder.
    def _wanted(path: str) -> bool:
        if _is_apex_path(path, config):
            return not _settings.is_ignored_apex_file(path, config)
        return _is_database_path(path, config)

    files = {path for record in records for path in record.usable_files if _wanted(path)}
    files.update(
        path for record in records for path in record.deleted_files if _wanted(path)
    )
    # A rename reaches the selection as two unrelated paths, because
    # `changed_files` runs `diff-tree` without `-M` and plumbing ignores
    # `diff.renames`, so git answers `D <old>` plus `A <new>`. Both sides stay
    # here on purpose (ADT #511): `_write_drop_helpers` owns whether an object
    # earns a DROP (`#498`, and `#503` stopped the recovery second-guessing it),
    # and that decision is made from this list. What the old side is NOT is a
    # file the patch carries, which is `create._database_patch_payload`'s
    # question and `SchemaReport.carried_files`', not this one.
    # `-app`, through the one reader in `patch/full_app.py` (ADT #576, renamed by
    # #592). The membership test that stood here read `full_app_ids or []`, which
    # folded the flag's three states into two: a bare flag arrived as an empty set and
    # the whole filter was skipped, so the patch listed every component of the
    # application it had just been asked to ship whole.
    files = {path for path in files if _ships_in_patch(path, config, full_app_ids)}
    # No grant script is injected here. GRANT is an ordinary `object_types` entry
    # (`grants/`, in `patch_map`), so a committed grant change arrives through
    # `_wanted` above like every other object file. Until ADT #501 a grant script
    # was pulled in for every schema a commit touched, whether or not the script
    # itself had moved, and the file then sat in the install script under neither
    # `NEW FILES:` nor `MODIFIED FILES:`, having no commit to read a status off
    # (Jan, 2026-08-24: "The grants file never changed, yet they are part of each
    # patch. IT IS CONFUSING."). What that bought was a privilege re-run on every
    # deploy; what it cost was a patch whose own header did not describe it.
    files.update(_apex_copy_files(root, files, config, full_app_ids=full_app_ids))
    # Read once for the whole selection rather than per path: the answer is a
    # sqlite read, and `_patch_group` runs on every file in the patch.
    owners = apex_owner_schemas(root)
    return PatchFileSelection(
        files = sorted(files, key=lambda path: _patch_sort_key(path, config, owners)),
    )

def _is_apex_application_path(path: str, config: dict[str, Any]) -> bool:
    """An APEX artifact that installs through an application import.

    `apex/workspace/rest/` sits under the APEX export root but holds ORDS calls
    the schema owner runs directly, so it takes the database route: grouped by
    schema, sectioned by `patch_map`, linked by a plain install script. Reading
    it as an application artifact opened an app-import script for application id
    `0` (ADT #314).
    """
    return _is_apex_path(path, config) and not _is_rest_path(path, config)

def _patch_sort_key(
    path   : str,
    config : dict[str, Any],
    owners : Mapping[int, str] | None = None,
) -> tuple[str, int, int, str]:
    if _is_apex_application_path(path, config):
        return (_patch_group(path, config, owners), *_apex_patch_sort_key(path))
    order = _database_patch_sort_key(path, config)
    return (_patch_group(path, config, owners), *order)

def _database_patch_sort_key(path: str, config: dict[str, Any]) -> tuple[int, int, str]:
    object_type = _database_object_type(path, config) or ""
    order = _patch_object_order(config)
    return (*order.get(object_type, (999, 999)), path)

def _apex_patch_sort_key(path: str) -> tuple[int, int, str]:
    if _is_apex_set_environment(path):
        return (0, 0, path)
    if _is_apex_end_environment(path):
        return (4, 0, path)
    if _is_apex_page(path):
        return (3, _apex_page_id(path) or 0, path)
    # defensive: `_apex_page_id` only ever returns non-None for a path its own regex already
    # anchors on `.sql$`, which is exactly what `_is_apex_page` checks above, so the two
    # conditions can never disagree
    if not (Path(path).exists()) and _apex_page_id(path):  # pragma: no cover
        return (2, _apex_page_id(path) or 0, path)
    return (1, 0, path)

def apex_owner_schemas(root: Path) -> dict[int, str]:
    """Each recorded application's parsing schema, out of `apex.db` (ADT #602).

    The project already holds this: nothing can IMPORT an application it never
    exported, and `export_apex` writes the owner into the store, so the schema an
    APEX install script has to connect as is a fact rather than a setting.

    Read only when the store is already there. `ApexStore.load` creates the file
    and its schema on open, and a database-only `-create` has no business growing
    a `config/internal/apex.db` as a side effect of building a patch.
    """
    from adt_ai.shared.apex_store import ApexStore, apex_store_path

    path = apex_store_path(root)
    if not path.is_file():
        return {}
    with ApexStore.open(path) as store:
        return {
            app_id: owner
            for app_id, entry in store.applications().items()
            if (owner := str(entry.get("owner") or ""))
        }


def apex_owner_for(owners: Mapping[int, str] | None, app_id: int | None) -> str:
    """``app_id``'s recorded owner, or that of the application it derives from.

    A sandbox id is the source application's id with a task number concatenated
    onto it (`patch/apex_import.derive_sandbox_app_id`), so `1000141` is app
    `1000`'s sandbox and runs as app `1000`'s parsing schema: that is where the
    tree came from and where the copy lands. The store records the source
    application and never the throwaway, so a direct hit is tried first and the
    longest recorded prefix second, which is what stops `100` answering for
    `1000`'s sandbox when both are exported.

    Empty when nothing recorded matches, which is the caller's cue to fall back.
    """
    if not owners or not app_id:
        return ""
    direct = owners.get(app_id)
    if direct:
        return direct
    text = str(app_id)
    sources = [
        source for source in owners
        if len(str(source)) < len(text) and text.startswith(str(source))
    ]
    return owners[max(sources, key=lambda source: len(str(source)))] if sources else ""


def apex_patch_schema(
    config : dict[str, Any],
    owners : Mapping[int, str] | None = None,
    app_id : int | None = None,
) -> str:
    """The connection schema APEX install scripts deploy through.

    One reader for the project's own answer (ADT #592), because `-drop` connects
    where an APEX install script connects and a second spelling of that lookup is
    how the two start disagreeing about which schema a project's APEX work runs
    as.

    **The application's own parsing schema wins** (ADT #602). The shipped
    `apex_schema` default is the literal `APEX`, a schema no ordinary connection
    file names, so a project that never set the key built a patch group nothing
    could connect as and the deploy died on `Schema not configured: <ENV>.APEX`
    before its first script. Jan, 2026-08-30: *"You know schema from connection
    file. This must not be stored in config file at all, that is a major
    fuckup."* The key stays as the fallback for an application the store has no
    row for, and the literal `APEX` behind it, which is what an unresolved
    `{$...}` token has always fallen back to: a template placeholder is not a
    schema name.
    """
    recorded = apex_owner_for(owners, app_id)
    schema = recorded or str(config.get("schema_apex") or config.get("apex_schema") or "APEX")
    return "APEX" if "{$" in schema else schema.upper()


def _patch_group(
    path   : str,
    config : dict[str, Any],
    owners : Mapping[int, str] | None = None,
) -> str:
    if _is_apex_application_path(path, config):
        app_id = _apex_app_id(path, config)
        return f"{apex_patch_schema(config, owners, app_id)}.{app_id or '0'}"
    return _database_schema(path, config)

def install_script_name(
    path   : str,
    config : dict[str, Any],
    owners : Mapping[int, str] | None = None,
) -> str:
    """The install script a patched file is linked from, `APP_OWNER.sql`.

    The public spelling of the grouping `_write_patch_files` already writes by,
    exposed for the deploy-side baseline merge (ADT #447): a `-continue` run
    where one script failed advances only the files the scripts that SUCCEEDED
    carried. Derived rather than recorded, and both go through
    `settings.group_script_name`, so `patch_group_file` moves them together.

    ``owners`` travels with it for the same reason (ADT #602): the group name an
    APEX file carries is the application's own schema, so a caller re-deriving
    the grouping without it would answer a different name than the build wrote.
    """
    return _settings.group_script_name(_patch_group(path, config, owners), config)

def _apex_copy_files(
    root: Path,
    files: set[str],
    config: dict[str, Any],
    *,
    full_app_ids: list[int] | None,
) -> list[str]:
    # Keyed by the application's own folder, not by its id: the folder name is
    # whatever `apex_path_app` rendered (`122_DIGITAL-APPROVAL`), so an id
    # cannot be spelled back into a path (ADT #429).
    app_roots = {
        root_parts
        for path in files
        if (root_parts := _apex_app_root(path, config)) is not None
        and (app_id := _apex_app_id(path, config)) is not None
        and not _is_full_app(app_id, full_app_ids)
    }
    copied: list[str] = []
    for app_root in sorted(app_roots):
        for raw in config.get("apex_files_copy") or []:
            relative = str(raw).strip("/")
            path = "/".join((*app_root, relative))
            if (root / path).exists():
                copied.append(path)
    return copied

def _patch_object_order(config: dict[str, Any]) -> dict[str, tuple[int, int]]:
    order: dict[str, tuple[int, int]] = {}
    for group_index, object_types in enumerate(_patch_map(config).values()):
        for type_index, object_type in enumerate(object_types):
            order[str(object_type).upper()] = (group_index, type_index)
    return order
