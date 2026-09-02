"""One SQLite store for everything ADT.ai caches about a project's APEX apps.

Four files held this before `#369`: `apex_apps.yaml` (one row per application),
`apex_developers.yaml` (workspace to developer to mail), `apex_timers.yaml` (a
rolling per-app-per-format duration that seeds the progress bar), and the
`export_apex` branch of `recent.yaml` (one watermark per environment, app and
format). They were written by three modules, read by five, and every reader paid
for a whole YAML mapping to answer a single lookup: `patch -create` loaded every
application ADT has ever exported to learn one workspace name, and
`validate -app 100` did the same to learn one owner.

They are one subject, so they are one store. The tables below split that subject
by what a row is about rather than by which module happened to write it, which
is why the watermark left `recent.yaml`: it describes an APEX export, and
`export_db`'s watermarks (that file's only other content) describe a database
one. `recent.yaml` keeps those, and is deleted once it holds nothing.

Two properties make it safe to open on any run:

* **Opening creates, never destroys.** A schema mismatch is no reason to wipe a
  cache the user cannot rebuild without a database, so `open` lifts an older
  file in place through the shared opener's migrations (ADT #642) and leaves
  every row that still describes something alone.
* **The legacy import runs once and is idempotent.** `migrate_apex_files` reads
  whatever of the four sources is on disk, writes it in, and deletes it. A
  half-finished conversion re-runs cleanly because every write is an upsert.

The public API hands back the same shapes the YAML readers used, keyed by app id
and workspace, so a call site swaps its loader and keeps its logic.
"""

from __future__ import annotations

import contextlib
import sqlite3
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from adt_ai.shared.internal_paths import internal_path
from adt_ai.shared.queries import apex_store as queries
from adt_ai.shared.sqlite_store import Migration, open_store
from adt_ai.shared.yaml_io import load_yaml_mapping, store_yaml_mapping

APEX_DB = "apex.db"

#: The YAML this store replaces, in the order `migrate_apex_files` drains them.
#: `recent.yaml` is absent on purpose: only one BRANCH of it moves, so it is
#: rewritten rather than deleted, and only goes when nothing is left in it.
LEGACY_APEX_FILES: tuple[str, ...] = (
    "apex_apps.yaml",
    "apex_developers.yaml",
    "apex_timers.yaml",
)

#: The `recent.yaml` key whose watermarks belong here.
RECENT_MODULE = "export_apex"

SCHEMA_VERSION = "2"

#: Version 1 to 2 (ADT #642): `watermarks.app_id` becomes the INTEGER every
#: other table keys by, and `_meta.value` becomes NOT NULL like every store's.
MIGRATIONS: tuple[Migration, ...] = (
    Migration("1", "2", lambda connection: connection.executescript(queries.APEX_STORE_LIFT_1)),
)

#: The application columns, in the order a row is written and read back. This
#: tuple is the single source: recording one more fact about an application
#: means adding it here and in `SCHEMA`, never at each call site.
APPLICATION_FIELDS: tuple[str, ...] = (
    "owner",
    "workspace",
    "workspace_id",
    "app_group",
    "app_alias",
    "app_name",
    "pages",
    "updated_at",
    "checksum",
)


def apex_store_path(root: Path | str) -> Path:
    """Where the store for ``root`` lives."""
    return internal_path(root, APEX_DB)


def _app_key(app_id: Any) -> int | None:
    """An application id as the store keys it, or None when it is not one.

    APEX app ids are integers, but they reach this module as YAML keys, CLI
    strings and database values, so every spelling is normalized once here. A
    value that is not an id at all is dropped rather than stored under a key
    nothing will ever look up again.
    """
    try:
        return int(str(app_id).strip())
    except (TypeError, ValueError):
        return None


class ApexStore:
    """Read and write access to one project's APEX cache."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    # -- lifecycle ---------------------------------------------------------

    @classmethod
    def open(cls, db_path: Path | str) -> ApexStore:
        # Setup can raise, and until ADT #510 nothing closed the connection when
        # it did: `sqlite3.connect` succeeds against any readable path and only
        # the first statement discovers the bytes are not a database. The caller
        # that finds this is `migrate_apex_files`, which catches `sqlite3.Error`
        # by design so a corrupt cache never stops a command, so the failure was
        # handled, the run continued, and the connection stayed open until the
        # collector reached it and reported an unclosed database against whatever
        # test was running by then. The shared opener carries that guard for
        # every store now.
        connection = open_store(
            db_path,
            schema     = queries.APEX_STORE_SCHEMA,
            version    = SCHEMA_VERSION,
            migrations = MIGRATIONS,
        )
        return cls(connection)

    @classmethod
    def load(cls, root: Path | str) -> ApexStore:
        return cls.open(apex_store_path(root))

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> ApexStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- applications ------------------------------------------------------

    def applications(self) -> dict[int, dict[str, Any]]:
        """Every recorded application, keyed by app id.

        The shape `apex_apps.yaml` handed back, `app_id` included in the value,
        so a reader that walked that mapping keeps walking this one.
        """
        rows = self.connection.execute(queries.APEX_APPLICATIONS_QUERY).fetchall()
        return {row["app_id"]: _application_row(row) for row in rows}

    def application(self, app_id: Any) -> dict[str, Any] | None:
        """One application, or None when this project never exported it."""
        key = _app_key(app_id)
        if key is None:
            return None
        row = self.connection.execute(queries.APEX_APPLICATION_QUERY, (key,)).fetchone()
        return _application_row(row) if row is not None else None

    def store_applications(self, payloads: Iterable[Mapping[str, Any]]) -> int:
        """Upsert one row per application; returns how many were written.

        An upsert rather than a replace because the checksum arrives on its own
        pass, after the export that computes it, so a plain insert would drop
        the fingerprint of every application a later run re-lists.
        """
        rows = []
        for payload in payloads:
            key = _app_key(payload.get("app_id"))
            if key is None:
                continue
            rows.append((key, *(payload.get(field) for field in APPLICATION_FIELDS)))
        if not rows:
            return 0
        with self.connection:
            self.connection.executemany(
                queries.apex_application_upsert(APPLICATION_FIELDS), rows
            )
        return len(rows)

    def store_checksum(self, app_id: Any, checksum: str) -> None:
        """Record one application's fingerprint beside the rest of its facts."""
        key = _app_key(app_id)
        if key is None or not checksum:
            return
        with self.connection:
            self.connection.execute(queries.APEX_CHECKSUM_UPSERT, (key, checksum))

    # -- developers --------------------------------------------------------

    def developers(self) -> dict[str, dict[str, str]]:
        """``{workspace: {user_name: user_mail}}``, the old file's shape."""
        result: dict[str, dict[str, str]] = {}
        for row in self.connection.execute(queries.APEX_DEVELOPERS_QUERY):
            result.setdefault(row["workspace"], {})[row["user_name"]] = row["user_mail"] or ""
        return result

    def store_developers(self, developers: Mapping[str, Mapping[str, str]]) -> None:
        """Merge a ``{workspace: {user: mail}}`` mapping into the store."""
        rows = [
            (str(workspace), str(user_name), str(user_mail or ""))
            for workspace, members in developers.items()
            for user_name, user_mail in (members or {}).items()
            if str(workspace) and str(user_name)
        ]
        if not rows:
            return
        with self.connection:
            self.connection.executemany(queries.APEX_DEVELOPER_UPSERT, rows)

    # -- timers ------------------------------------------------------------

    def timers(self) -> dict[int, dict[str, float]]:
        """``{app_id: {action: seconds}}``, the old file's shape."""
        result: dict[int, dict[str, float]] = {}
        for row in self.connection.execute(queries.APEX_TIMERS_QUERY):
            result.setdefault(row["app_id"], {})[row["action"]] = row["seconds"]
        return result

    def store_timer(self, app_id: Any, action: str, seconds: float) -> None:
        key = _app_key(app_id)
        if key is None or not action:
            return
        with self.connection:
            self.connection.execute(
                queries.APEX_TIMER_UPSERT, (key, str(action), float(seconds))
            )

    def store_timers(self, timers: Mapping[Any, Mapping[str, Any]]) -> None:
        """Write a whole ``{app_id: {action: seconds}}`` mapping."""
        for app_id, actions in timers.items():
            if not isinstance(actions, Mapping):
                continue
            for action, seconds in actions.items():
                with contextlib.suppress(TypeError, ValueError):
                    self.store_timer(app_id, action, float(seconds))

    # -- watermarks --------------------------------------------------------

    def watermark(self, environment: str, app_id: Any, action: str) -> str | None:
        """The last covering export of one app's one format, or None."""
        key = _app_key(app_id)
        if key is None:
            return None
        row = self.connection.execute(
            queries.APEX_WATERMARK_QUERY,
            (str(environment), key, str(action)),
        ).fetchone()
        return str(row["exported_at"]) if row is not None else None

    def set_watermark(self, environment: str, app_id: Any, action: str, stamp: str) -> None:
        key = _app_key(app_id)
        if key is None:
            return
        with self.connection:
            self.connection.execute(
                queries.APEX_WATERMARK_UPSERT,
                (str(environment), key, str(action), str(stamp)),
            )

    def watermarks(self) -> dict[str, dict[int, dict[str, str]]]:
        """Every watermark, in the nested shape `recent.yaml` used, keyed by app id."""
        result: dict[str, dict[int, dict[str, str]]] = {}
        for row in self.connection.execute(queries.APEX_WATERMARKS_QUERY):
            environment = result.setdefault(row["environment"], {})
            environment.setdefault(row["app_id"], {})[row["format"]] = row["exported_at"]
        return result


def _application_row(row: sqlite3.Row) -> dict[str, Any]:
    payload: dict[str, Any] = {"app_id": row["app_id"]}
    for field in APPLICATION_FIELDS:
        value = row[field]
        if value is not None:
            payload[field] = value
    return payload


def migrate_apex_files(root: Path | str) -> list[str]:
    """Fold the legacy APEX caches into ``apex.db`` and delete them.

    Returns the file names actually converted, empty on a root that has none.
    Never raises and never prints: it runs from the same early CLI hook as
    :func:`~adt_ai.shared.internal_paths.migrate_internal_files`, before the
    command banner, where a failure would be worse than a stale cache.
    """
    converted: list[str] = []
    sources = {name: internal_path(root, name) for name in LEGACY_APEX_FILES}
    recent_path = internal_path(root, "recent.yaml")
    recent = load_yaml_mapping(recent_path) if recent_path.is_file() else {}
    has_recent_apex = isinstance(recent.get(RECENT_MODULE), dict)
    if not any(path.is_file() for path in sources.values()) and not has_recent_apex:
        return []
    try:
        with ApexStore.load(root) as store:
            if sources["apex_apps.yaml"].is_file():
                _import_applications(store, load_yaml_mapping(sources["apex_apps.yaml"]))
                converted.append("apex_apps.yaml")
            if sources["apex_developers.yaml"].is_file():
                store.store_developers(
                    _string_mapping(load_yaml_mapping(sources["apex_developers.yaml"]))
                )
                converted.append("apex_developers.yaml")
            if sources["apex_timers.yaml"].is_file():
                store.store_timers(load_yaml_mapping(sources["apex_timers.yaml"]))
                converted.append("apex_timers.yaml")
            if has_recent_apex:
                _import_watermarks(store, recent[RECENT_MODULE])
                converted.append("recent.yaml")
    except (OSError, sqlite3.Error):
        # Same posture as every other early sweep: a root ADT.ai cannot write is
        # left exactly as found and the command proceeds on what it still has.
        return []
    for name in LEGACY_APEX_FILES:
        if name in converted:
            with contextlib.suppress(OSError):
                sources[name].unlink()
    if has_recent_apex:
        _drain_recent_apex(recent_path, recent)
    return converted


def _import_applications(store: ApexStore, payload: Mapping[Any, Any]) -> None:
    rows = []
    for app_id, entry in payload.items():
        if not isinstance(entry, Mapping):
            continue
        row = dict(entry)
        row.setdefault("app_id", app_id)
        rows.append(row)
    store.store_applications(rows)


def _import_watermarks(store: ApexStore, payload: Mapping[Any, Any]) -> None:
    for environment, apps in payload.items():
        if not isinstance(apps, Mapping):
            continue
        for app_id, formats in apps.items():
            if not isinstance(formats, Mapping):
                continue
            for action, stamp in formats.items():
                if isinstance(stamp, str) and stamp:
                    store.set_watermark(str(environment), app_id, str(action), stamp)


def _string_mapping(payload: Mapping[Any, Any]) -> dict[str, dict[str, str]]:
    return {
        str(workspace): {str(user): str(mail or "") for user, mail in members.items()}
        for workspace, members in payload.items()
        if isinstance(members, Mapping)
    }


def _drain_recent_apex(path: Path, payload: dict[Any, Any]) -> None:
    """Take `export_apex` out of `recent.yaml`, and the file with it when empty.

    The file's other key is `export_db`, whose watermarks describe a database
    export and stay exactly where they are. A root that only ever exported APEX
    is left holding an empty mapping, which is a file that answers nothing, so
    it goes rather than sitting there as one more thing to explain.
    """
    payload.pop(RECENT_MODULE, None)
    with contextlib.suppress(OSError):
        if payload:
            store_yaml_mapping(path, payload)
        else:
            path.unlink(missing_ok=True)


__all__ = [
    "APEX_DB",
    "APPLICATION_FIELDS",
    "LEGACY_APEX_FILES",
    "MIGRATIONS",
    "RECENT_MODULE",
    "SCHEMA_VERSION",
    "ApexStore",
    "apex_store_path",
    "migrate_apex_files",
]
