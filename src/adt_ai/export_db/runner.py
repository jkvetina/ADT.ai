from __future__ import annotations

import datetime
import fnmatch
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.db import QueryGateway
from adt_ai.export_db.files import (
    ObjectFileResolver,
    ObjectFileWriter,
    ObjectWritePlan,
    ObjectWriteRequest,
)
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery, has_exact_name_filter
from adt_ai.export_db.normalizers import (
    NormalizerRegistry,
    build_table_fix_sql,
    normalize_ddl,
)

DROPBOX_PATH_RE = re.compile(r"/Users/[^/]+/Library/CloudStorage/Dropbox/")


class ExportDbReporter:
    @property
    def reports_objects(self) -> bool:
        return True

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | None = None,
    ) -> None:
        pass

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        pass

    def start_export(self, schema: str, total: int) -> None:
        pass

    def export_object(self, database_object: DatabaseObject) -> None:
        pass

    def finish_type(self, schema: str, object_type: str) -> None:
        pass


def print_adt_header(message: str, append: str = "", file=None) -> None:
    print(file=file)
    print(f"{message}{(' ' + append).rstrip()}", file=file)
    print("-" * len(message), file=file)


def print_adt_table(
    rows: list[dict[str, object]],
    min_widths: Mapping[str, int] | None = None,
) -> None:
    if not rows:
        return
    min_widths = min_widths or {}
    columns = list(rows[0].keys())
    widths = [
        max(
            len(column),
            min_widths.get(column, 0),
            *(len(DROPBOX_PATH_RE.sub("Dropbox/", str(row.get(column, "")))) for row in rows),
        )
        for column in columns
    ]
    numeric = [
        all(
            str(row.get(column, "")).isnumeric() or row.get(column, "") in {None, ""}
            for row in rows
        )
        for column in columns
    ]

    def format_row(values: list[object]) -> str:
        line = "  "
        for index, value in enumerate(values):
            text = DROPBOX_PATH_RE.sub("Dropbox/", str(value or ""))
            align = ">" if numeric[index] else "<"
            line += f"{text:{align}{widths[index]}}   "
        return line

    print()
    print(format_row([column.upper().replace("_", " ") for column in columns]))
    print(format_row(["-" * width for width in widths]))
    for row in rows:
        print(format_row([row.get(column, "") for column in columns]))
    print()
    _commit_stdout()


def _commit_stdout() -> None:
    commit_pending = getattr(sys.stdout, "commit_pending", None)
    if callable(commit_pending):
        commit_pending()
        return
    sys.stdout.flush()


def print_adt_pipes(rows: dict[str, list[str]]) -> None:
    for key in sorted(rows):
        for index, value in enumerate(rows[key]):
            label = key.upper() if index == 0 else ""
            print(f"  {label:>18} | {value}")
    print()


class ConsoleExportDbReporter(ExportDbReporter):
    def __init__(self, silent: bool = False) -> None:
        self._last_type_by_schema: dict[str, str] = {}
        self._silent = silent

    @property
    def reports_objects(self) -> bool:
        return not self._silent

    def overview(
        self,
        schema: str,
        objects: list[DatabaseObject],
        names: list[str] | None = None,
        recent_days: int | None = None,
    ) -> None:
        print_adt_header(_overview_header(names=names, recent_days=recent_days))
        counts = Counter(database_object.object_type for database_object in objects)
        rows = [
            {"object_type": object_type, "count": counts[object_type]}
            for object_type in sorted(counts)
        ]
        print_adt_table(rows)

    def deleted_objects(self, schema: str, objects: list[DatabaseObject]) -> None:
        if not objects:
            return
        grouped: dict[str, list[str]] = {}
        for database_object in sorted(objects, key=lambda item: (item.object_type, item.name)):
            grouped.setdefault(database_object.object_type, []).append(database_object.name)
        print_adt_header("DELETED OBJECTS:")
        print_adt_pipes(grouped)

    def start_export(self, schema: str, total: int) -> None:
        self._last_type_by_schema[schema] = ""
        print_adt_header("EXPORTING OBJECTS:", f"({total})")
        if self._silent:
            return
        print()

    def export_object(self, database_object: DatabaseObject) -> None:
        last_type = self._last_type_by_schema.get(database_object.schema, "")
        object_type = (
            database_object.object_type
            if database_object.object_type != last_type
            else ""
        )
        self._last_type_by_schema[database_object.schema] = database_object.object_type
        if self._silent:
            return
        print(f"{object_type:>20} | {database_object.name:<54}")

    def finish_type(self, schema: str, object_type: str) -> None:
        if self._silent:
            return
        print(f"{'':>20} |")


@dataclass(frozen=True)
class ExportDbRequest:
    root         : Path
    schemas      : list[str]
    config       : dict[str, Any]
    schema_export: dict[str, dict[str, Any]] | None = None
    object_types : list[str] | None = None
    names        : list[str] | None = None
    prefix       : str | None = None
    ignore       : list[str] | None = None
    recent_days  : int | None = None
    clean        : bool = False
    dry_run      : bool = False
    reporter     : ExportDbReporter | None = None


GatewayFactory = Callable[[str], QueryGateway]


class ExportDbRunner:
    def __init__(
        self,
        gateway_factory: GatewayFactory,
        normalizer_registry: NormalizerRegistry | None = None,
    ) -> None:
        self.gateway_factory = gateway_factory
        self.normalizer_registry = normalizer_registry or NormalizerRegistry.builtin()

    def run(self, request: ExportDbRequest) -> list[ObjectWritePlan]:
        gateway_factory = _cached_gateway_factory(self.gateway_factory)
        resolver = ObjectFileResolver.from_config(
            root   = request.root,
            config = _with_default_layout(request.config),
        )
        writer = ObjectFileWriter(resolver, compare_existing=False)
        object_contents = self._contents(
            request,
            resolver        = resolver,
            gateway_factory = gateway_factory,
        )
        grant_contents = self._grant_contents(
            request,
            gateway_factory = gateway_factory,
        )
        if request.dry_run:
            requests: list[ObjectWriteRequest] = []
            for database_object, content, fix_content in object_contents:
                requests.append(ObjectWriteRequest(database_object, content))
                if fix_content is not None:
                    requests.append(
                        ObjectWriteRequest(
                            database_object,
                            fix_content,
                            path = resolver.fix_path_for(database_object),
                        )
                    )
            requests.extend(
                ObjectWriteRequest(database_object, content)
                for database_object, content in grant_contents
            )
            return writer.plan(requests, dry_run=True)
        plans: list[ObjectWritePlan] = []
        for database_object, content, fix_content in object_contents:
            plans.append(writer.write_one(ObjectWriteRequest(database_object, content)))
            fix_path = resolver.fix_path_for(database_object)
            if fix_content is not None:
                plans.append(
                    writer.write_one(
                        ObjectWriteRequest(database_object, fix_content, path=fix_path)
                    )
                )
            elif fix_path.exists():
                fix_path.unlink()
        for database_object, content in grant_contents:
            plans.append(writer.write_one(ObjectWriteRequest(database_object, content)))
        return plans

    def _contents(
        self,
        request: ExportDbRequest,
        resolver: ObjectFileResolver,
        gateway_factory: GatewayFactory,
    ) -> Iterable[tuple[DatabaseObject, str, str | None]]:
        reporter = request.reporter or ExportDbReporter()
        for schema in request.schemas:
            discovery = ObjectDiscovery(gateway_factory(schema))
            schema_export = (request.schema_export or {}).get(schema, {})
            database_objects = discovery.discover(
                schema       = schema,
                object_types = request.object_types or _configured_object_types(request.config),
                names        = request.names,
                prefix       = request.prefix or schema_export.get("prefix"),
                ignore       = request.ignore or _split_patterns(schema_export.get("ignore")),
                recent_days  = request.recent_days,
                prefer_exact_names = True,
            )
            if not has_exact_name_filter(request.names):
                reporter.overview(
                    schema,
                    database_objects,
                    names       = request.names,
                    recent_days = request.recent_days,
                )
            if not _has_runtime_filter(request):
                missing_objects = resolver.missing_objects(database_objects, schema=schema)
                reporter.deleted_objects(
                    schema,
                    missing_objects,
                )
                if _is_enabled(request.config.get("auto_delete")) and not request.dry_run:
                    resolver.delete_missing_objects(missing_objects)
            if request.clean and not request.dry_run:
                resolver.delete_configured_object_files(schema)
            if database_objects:
                discovery.setup_dbms_metadata()
            comment_types = _comment_query_object_types(request, request.config)
            if comment_types:
                discovery.prepare_comments(
                    schema       = schema,
                    object_types = comment_types,
                    names        = request.names,
                    prefix       = request.prefix or schema_export.get("prefix"),
                    ignore       = request.ignore or _split_patterns(schema_export.get("ignore")),
                )
            reporter.start_export(schema, len(database_objects))
            reports_objects = reporter.reports_objects
            for index, database_object in enumerate(database_objects):
                if reports_objects:
                    reporter.export_object(database_object)
                raw_ddl = discovery.ddl(database_object)
                content = normalize_ddl(
                    raw_ddl,
                    object_type = database_object.object_type,
                    object_name = database_object.name,
                    registry    = self.normalizer_registry,
                )
                fix_content = (
                    build_table_fix_sql(raw_ddl, database_object.name)
                    if database_object.object_type == "TABLE"
                    else None
                )
                if database_object.object_type == "JOB":
                    content = _append_job_arguments(
                        content,
                        discovery.job_arguments(database_object),
                    )
                content = _append_comments(
                    content,
                    database_object,
                    discovery.comments(database_object)
                    if _has_comments(database_object.object_type, request.config)
                    else [],
                    include_columns = _has_column_comments(
                        database_object.object_type,
                        request.config,
                    ),
                    ignored_columns = _ignored_comment_columns(request.config),
                )
                yield database_object, content, fix_content
                next_object = (
                    database_objects[index + 1]
                    if index + 1 < len(database_objects)
                    else None
                )
                if (
                    reports_objects
                    and (
                        next_object is None
                        or next_object.object_type != database_object.object_type
                    )
                ):
                    reporter.finish_type(schema, database_object.object_type)

    def _grant_contents(
        self,
        request: ExportDbRequest,
        gateway_factory: GatewayFactory,
    ) -> Iterable[tuple[DatabaseObject, str]]:
        if "GRANT" not in request.config.get("object_types", {}):
            return
        if not _requested_object_type_matches("GRANT", request.object_types):
            return
        for schema in request.schemas:
            discovery = ObjectDiscovery(gateway_factory(schema))
            schema_export = (request.schema_export or {}).get(schema, {})
            prefix = request.prefix or schema_export.get("prefix")
            ignore = request.ignore or _split_patterns(schema_export.get("ignore"))

            yield DatabaseObject(schema, "GRANT", schema), _render_grants_made(
                discovery.grants_made(schema, prefix=prefix, ignore=ignore),
                prefix = prefix,
                ignore = ignore,
            )
            for owner, content in _render_grants_received(
                discovery.grants_received(schema),
                schema = schema,
            ).items():
                yield DatabaseObject(schema, "GRANT", f"received/{owner.upper()}"), content
            yield (
                DatabaseObject(schema, "GRANT", f"{schema.upper()}_schema"),
                _render_user_privileges(
                    discovery.user_privileges(schema),
                    schema = schema,
                ),
            )
            yield (
                DatabaseObject(schema, "GRANT", f"{schema.upper()}_directories"),
                _render_directories(
                    discovery.directories(schema),
                    schema = schema,
                ),
            )

def _with_default_layout(config: dict[str, Any]) -> dict[str, Any]:
    if "path_objects" in config:
        return config
    return {**config, "path_objects": "database/<schema>/<object_type>"}


def _cached_gateway_factory(gateway_factory: GatewayFactory) -> GatewayFactory:
    gateways: dict[str, QueryGateway] = {}

    def for_schema(schema: str) -> QueryGateway:
        if schema not in gateways:
            gateways[schema] = gateway_factory(schema)
        return gateways[schema]

    return for_schema


def _configured_object_types(config: dict[str, Any]) -> list[str]:
    raw_types = config.get("object_types", {})
    if not isinstance(raw_types, dict):
        return []
    return [
        object_type
        for object_type in raw_types
        if object_type not in {"DATA", "GRANT"}
    ]


def _requested_object_type_matches(
    object_type: str,
    requested_types: list[str] | None,
) -> bool:
    if requested_types is None:
        return True
    normalized_type = object_type.upper()
    return any(
        fnmatch.fnmatchcase(
            normalized_type,
            requested_type.upper().replace("%", "*").replace("_", "?"),
        )
        for requested_type in requested_types
    )


def _overview_header(
    names: list[str] | None,
    recent_days: int | None,
) -> str:
    if recent_days is None:
        show_header = "OVERVIEW"
    else:
        changed_since = datetime.date.today() - datetime.timedelta(days=recent_days - 1)
        show_header = f"CHANGED SINCE {changed_since}"
    show_filter = " ".join(names or ["%"])
    show_filter = f" {show_filter} ".replace(" % ", " ").strip()
    if show_filter:
        show_header = f"{show_header}, FILTER"
    return f"OBJECTS {show_header}: {show_filter}".rstrip()


def _has_runtime_filter(request: ExportDbRequest) -> bool:
    return any(
        (
            request.object_types is not None,
            request.names is not None,
            request.recent_days is not None,
        )
    )


def _split_patterns(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, list | tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value)]


def _is_enabled(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().upper() in {"1", "TRUE", "Y", "YES", "ON"}


def _has_comments(object_type: str, config: dict[str, Any]) -> bool:
    return object_type.upper() in _configured_comment_types(
        config,
        key      = "object_comments",
        defaults = {"TABLE", "VIEW", "MATERIALIZED VIEW"},
    )


def _has_column_comments(object_type: str, config: dict[str, Any]) -> bool:
    return object_type.upper() in _configured_comment_types(
        config,
        key      = "object_col_comments",
        defaults = {"TABLE", "MATERIALIZED VIEW"},
    )


def _configured_comment_types(
    config: dict[str, Any],
    key: str,
    defaults: set[str],
) -> set[str]:
    raw_types = config.get(key)
    if raw_types is None:
        return defaults
    if isinstance(raw_types, list | tuple | set):
        return {str(object_type).upper() for object_type in raw_types}
    return {str(raw_types).upper()}


def _comment_query_object_types(
    request: ExportDbRequest,
    config: dict[str, Any],
) -> list[str]:
    configured = _configured_comment_types(
        config,
        key      = "object_comments",
        defaults = {"TABLE", "VIEW", "MATERIALIZED VIEW"},
    ) | _configured_comment_types(
        config,
        key      = "object_col_comments",
        defaults = {"TABLE", "MATERIALIZED VIEW"},
    )
    requested = (
        {object_type.upper() for object_type in request.object_types}
        if request.object_types is not None
        else set(_configured_object_types(config))
    )
    return sorted(configured & requested)


def _append_comments(
    content: str,
    database_object: DatabaseObject,
    comments: list[dict[str, Any]],
    include_columns: bool,
    ignored_columns: set[str] | None = None,
) -> str:
    ignored_columns = ignored_columns or set()
    object_lines = [
        line
        for comment in comments
        if not comment.get("COLUMN_NAME")
        if (line := _render_comment(database_object, comment, include_columns, None))
    ]
    column_comments = [
        comment
        for comment in comments
        if comment.get("COLUMN_NAME")
        if str(comment.get("COLUMN_NAME")).upper() not in ignored_columns
    ]
    column_width = _column_comment_width(database_object, column_comments)
    column_lines = [
        line
        for comment in column_comments
        if (line := _render_comment(database_object, comment, include_columns, column_width))
    ]
    if not object_lines and not column_lines:
        return content
    lines = content.rstrip().splitlines()
    if object_lines:
        lines.extend(["--", *object_lines])
    if column_lines:
        lines.extend(["--", *column_lines])
    return "\n".join([*lines, "", ""])


def _render_comment(
    database_object: DatabaseObject,
    comment: dict[str, Any],
    include_columns: bool,
    column_width: int | None,
) -> str | None:
    object_name = database_object.name.lower()
    column_name = comment.get("COLUMN_NAME")
    raw_text = comment.get("COMMENTS")
    text = "" if raw_text is None else str(raw_text)
    if column_name:
        if not include_columns:
            return None
        column_full = f"{object_name}.{str(column_name).lower()}"
        if column_width is not None:
            column_full = f"{column_full:<{column_width}}"
        return (
            f"COMMENT ON COLUMN {column_full} "
            f"IS '{_escape_sql_text(text)}';"
        )
    if not text and database_object.object_type.upper() != "TABLE":
        return None
    return f"COMMENT ON TABLE {object_name} IS '{_escape_sql_text(text)}';"


def _escape_sql_text(value: str) -> str:
    return value.replace("'", "''")


def _column_comment_width(
    database_object: DatabaseObject,
    comments: list[dict[str, Any]],
) -> int | None:
    column_names = [
        f"{database_object.name.lower()}.{str(comment.get('COLUMN_NAME')).lower()}"
        for comment in comments
        if comment.get("COLUMN_NAME")
    ]
    if not column_names:
        return None
    return max((len(column_name) // 4) * 4 + 5 for column_name in column_names)


def _append_job_arguments(content: str, rows: list[dict[str, Any]]) -> str:
    argument_lines = _render_job_arguments(rows)
    if not argument_lines:
        return content
    marker = "    --\n    DBMS_SCHEDULER.SET_ATTRIBUTE"
    replacement = f"    --\n{argument_lines}\n    --\n    DBMS_SCHEDULER.SET_ATTRIBUTE"
    if marker not in content:
        return content
    return content.replace(marker, replacement, 1)


def _render_job_arguments(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for row in rows:
        argument_name = row.get("ARGUMENT_NAME") or row.get("argument_name")
        argument_position = row.get("ARGUMENT_POSITION") or row.get("argument_position")
        value = _escape_sql_text(str(row.get("VALUE") or row.get("value") or ""))
        if argument_name:
            selector = f"argument_name => '{_escape_sql_text(str(argument_name))}'"
        else:
            selector = f"argument_position => {argument_position}"
        lines.append(
            "    DBMS_SCHEDULER.SET_JOB_ARGUMENT_VALUE("
            f"in_job_name, {selector}, argument_value => '{value}');"
        )
    return "\n".join(lines)


def _render_grants_made(
    rows: list[dict[str, Any]],
    prefix: str | None,
    ignore: list[str] | None,
) -> str:
    if any(row.get("SQL") or row.get("sql") for row in rows):
        return _render_grants_made_sql_rows(rows)
    filtered = [
        row for row in rows
        if _grant_object_matches(str(row.get("OBJECT_NAME") or ""), prefix, ignore)
    ]
    lines: list[str] = []
    last_type = ""
    grouped = _group_grants_made(filtered)
    for object_type, object_name, grantable in sorted(grouped):
        if object_type != last_type:
            lines.extend(["", "--", f"-- {object_type}", "--"])
        privileges, grantees = grouped[(object_type, object_name, grantable)]
        grant_option = " WITH GRANT OPTION" if grantable == "YES" else ""
        lines.append(
            f"GRANT {', '.join(sorted(privileges))} ON {object_name.lower()} "
            f"TO {', '.join(_sort_oracle_names(grantees))}{grant_option};"
        )
        last_type = object_type
    return "\n".join(lines).lstrip() + ("\n\n" if lines else "")


def _render_grants_made_sql_rows(rows: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    last_type = ""
    for row in rows:
        object_type = str(row.get("OBJECT_TYPE") or row.get("TYPE") or "")
        sql = str(row.get("SQL") or row.get("sql") or "")
        if not sql:
            continue
        if object_type != last_type:
            lines.extend(["", "--", f"-- {object_type}", "--"])
        lines.append(sql)
        last_type = object_type
    return "\n".join(lines).lstrip() + ("\n\n" if lines else "")


def _ignored_comment_columns(config: dict[str, Any]) -> set[str]:
    default_ignored = {
        "CREATED_AT",
        "CREATED_BY",
        "CREATED_ON",
        "UPDATED_AT",
        "UPDATED_BY",
        "UPDATED_ON",
    }
    configured = config.get("ignored_columns")
    if isinstance(configured, list | tuple | set):
        return default_ignored | {str(column).upper() for column in configured}
    if configured is None:
        return default_ignored
    return default_ignored | {str(configured).upper()}


def _sort_oracle_names(values: Iterable[str]) -> list[str]:
    return sorted(
        (value.lower() for value in values),
        key=lambda value: value.upper().replace("_", "{"),
    )


def _group_grants_made(
    rows: list[dict[str, Any]],
) -> dict[tuple[str, str, str], tuple[set[str], set[str]]]:
    grouped: dict[tuple[str, str, str], tuple[set[str], set[str]]] = {}
    for row in rows:
        key = (
            str(row.get("OBJECT_TYPE") or row.get("TYPE") or ""),
            str(row.get("OBJECT_NAME") or row.get("TABLE_NAME") or ""),
            str(row.get("GRANTABLE") or "NO"),
        )
        grouped.setdefault(key, (set(), set()))
        grouped[key][0].add(str(row.get("PRIVILEGE") or ""))
        grouped[key][1].add(str(row.get("GRANTEE") or ""))
    return grouped


def _render_grants_received(
    rows: list[dict[str, Any]],
    schema: str,
) -> dict[str, str]:
    grouped: dict[str, dict[str, dict[str, list[str]]]] = {}
    for row in rows:
        owner = str(row.get("OWNER") or "")
        object_type = str(row.get("OBJECT_TYPE") or row.get("TYPE") or "")
        object_name = str(row.get("OBJECT_NAME") or row.get("TABLE_NAME") or "")
        privilege = str(row.get("PRIVILEGE") or "")
        grantable = " WITH GRANT OPTION" if row.get("GRANTABLE") == "YES" else ""
        sql = f"GRANT {privilege} ON {object_name.lower()} TO {schema.lower()}{grantable};"
        grouped.setdefault(owner, {}).setdefault(object_type, {}).setdefault(
            object_name,
            [],
        ).append(sql)

    rendered: dict[str, str] = {}
    for owner, object_types in grouped.items():
        lines = [f"ALTER SESSION SET CURRENT_SCHEMA = {owner.lower()};", ""]
        for object_type in sorted(object_types):
            lines.extend(["--", f"-- {object_type}", "--"])
            for object_name in sorted(object_types[object_type]):
                lines.extend(sorted(object_types[object_type][object_name]))
            lines.append("")
        lines.append(f"ALTER SESSION SET CURRENT_SCHEMA = {schema.upper()};")
        rendered[owner] = "\n".join(lines).lstrip() + "\n\n"
    return rendered


def _render_user_privileges(rows: list[dict[str, Any]], schema: str) -> str:
    roles = sorted(
        str(row.get("NAME") or "")
        for row in rows
        if row.get("PRIVILEGE_KIND") == "ROLE"
    )
    privileges = sorted(
        str(row.get("NAME") or "") for row in rows if row.get("PRIVILEGE_KIND") == "SYSTEM"
    )
    lines = [f"GRANT {role:<21} TO {schema.lower()};" for role in roles]
    if privileges:
        lines.append("--")
        lines.extend(f"GRANT {privilege:<33} TO {schema.lower()};" for privilege in privileges)
    return "\n".join(lines).lstrip("-\n") + ("\n\n" if lines else "")


def _render_directories(rows: list[dict[str, Any]], schema: str) -> str:
    lines = [
        "CREATE OR REPLACE DIRECTORY "
        f"{str(row.get('OWNER') or schema).lower()}."
        f"{str(row.get('DIRECTORY_NAME') or '').lower():<31} "
        f"AS '{_escape_sql_text(str(row.get('DIRECTORY_PATH') or ''))}';"
        for row in rows
    ]
    return "\n".join(sorted(lines)).lstrip() + ("\n" if lines else "")


def _grant_object_matches(
    object_name: str,
    prefix: str | None,
    ignore: list[str] | None,
) -> bool:
    name = object_name.upper()
    if name.startswith("BIN$"):
        return False
    prefixes = _split_patterns(prefix) or ["%"]
    if not any(
        fnmatch.fnmatchcase(name, pattern.upper().replace("%", "*"))
        for pattern in prefixes
    ):
        return False
    return not any(
        fnmatch.fnmatchcase(name, pattern.upper().replace("%", "*"))
        for pattern in (ignore or [])
    )
