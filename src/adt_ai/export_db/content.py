from __future__ import annotations

import sys
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from adt_ai.export_db.config import _configured_object_types, _split_patterns
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.shared.sql_like import matches_sql_like

if TYPE_CHECKING:
    from adt_ai.export_db.runner import ExportDbRequest


def close_with_empty_lines(content: str, empty_lines: int) -> str:
    """End `content` with exactly `empty_lines` blank lines before end of file.

    The one place the tail of an exported file is decided, so every object type
    converges on the same shape whatever its normalizer left behind: a
    slash-terminated object arrived here carrying one blank line, a TABLE none,
    and an object that had `COMMENT ON` lines appended one again (`#687`).

    Idempotent by construction, which is what lets a re-export of unchanged
    content still read as `unchanged` on disk.
    """
    body = content.rstrip("\n")
    if not body:
        # A file with no body is not a file of newlines.
        return ""
    return body + "\n" * (empty_lines + 1)

def _has_comments(object_type: str, config: dict[str, Any]) -> bool:
    return object_type.upper() in _configured_comment_types(
        config,
        key      = "object_comments",
        defaults = {"TABLE", "VIEW"},
    )

def _has_column_comments(object_type: str, config: dict[str, Any]) -> bool:
    return object_type.upper() in _configured_comment_types(
        config,
        key      = "object_col_comments",
        defaults = {"TABLE"},
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
        defaults = {"TABLE", "VIEW"},
    ) | _configured_comment_types(
        config,
        key      = "object_col_comments",
        defaults = {"TABLE"},
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
    object_display_name: str | None = None,
) -> str:
    # These lines are appended into the object's OWN file, so they follow that
    # file's spelling exactly as the definition line does (`#679`). Recasing one
    # and not the other is what leaves a half-recased file behind.
    ignored_columns = ignored_columns or set()
    object_lines = [
        line
        for comment in comments
        if not comment.get("COLUMN_NAME")
        if (
            line := _render_comment(
                database_object, comment, include_columns, None, object_display_name
            )
        )
    ]
    column_comments = [
        comment
        for comment in comments
        if comment.get("COLUMN_NAME")
        if str(comment.get("COLUMN_NAME")).upper() not in ignored_columns
    ]
    column_width = _column_comment_width(
        database_object, column_comments, object_display_name
    )
    column_lines = [
        line
        for comment in column_comments
        if (
            line := _render_comment(
                database_object, comment, include_columns, column_width, object_display_name
            )
        )
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
    object_display_name: str | None = None,
) -> str | None:
    # Only the object-name half follows the file. A column name is not the
    # file's name and keeps the lowercase the export has always written.
    object_name = object_display_name or database_object.name.lower()
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
    object_display_name: str | None = None,
) -> int | None:
    object_name = object_display_name or database_object.name.lower()
    column_names = [
        f"{object_name}.{str(comment.get('COLUMN_NAME')).lower()}"
        for comment in comments
        if comment.get("COLUMN_NAME")
    ]
    if not column_names:
        return None
    return max((len(column_name) // 4) * 4 + 5 for column_name in column_names)

def _append_job_arguments(
    content: str, rows: list[dict[str, Any]], object_name: str = ""
) -> str:
    argument_lines = _render_job_arguments(rows)
    if not argument_lines:
        return content
    # Anchored on the END of the CREATE_JOB call rather than on whatever follows it.
    # It used to anchor on `DBMS_SCHEDULER.SET_ATTRIBUTE`, which only ever worked
    # because the template emitted a hardcoded JOB_PRIORITY row for every job; ADT
    # #414 removed that invention (PROCOBJ emits no priority attribute at a default
    # priority), and with it the anchor. The close of CREATE_JOB is always there,
    # and it is also the right place: arguments have to be set before the ENABLE.
    marker = "    );\n    --\n"
    replacement = f"    );\n    --\n{argument_lines}\n    --\n"
    if marker not in content:
        # The job HAS arguments but the DDL didn't render the expected
        # CREATE_JOB block to anchor them, dropping them silently would
        # export a job that deploys without its arguments.
        label = f" for job {object_name}" if object_name else ""
        print(
            f"Warning: job arguments{label} not exported: "
            "no DBMS_SCHEDULER.CREATE_JOB block found in the DDL",
            file=sys.stderr,
        )
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

def _qualified(owner: str, object_name: str, keep_owner: bool) -> str:
    """Render an object name with or without its owner, per `keep_owner`.

    One spelling for every GRANT and DIRECTORY line, so the three renderers
    below cannot disagree about what the key means.
    """
    name = object_name.lower()
    if not keep_owner or not owner:
        return name
    return f"{owner.lower()}.{name}"

def _render_grants_made(
    rows: list[dict[str, Any]],
    prefix: str | None,
    ignore: list[str] | None,
    schema: str = "",
    keep_owner: bool = False,
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
            f"GRANT {', '.join(sorted(privileges))} "
            f"ON {_qualified(schema, object_name, keep_owner)} "
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
    keep_owner: bool = False,
) -> dict[str, str]:
    grouped: dict[str, dict[str, dict[str, list[str]]]] = {}
    for row in rows:
        owner = str(row.get("OWNER") or "")
        object_type = str(row.get("OBJECT_TYPE") or row.get("TYPE") or "")
        object_name = str(row.get("OBJECT_NAME") or row.get("TABLE_NAME") or "")
        privilege = str(row.get("PRIVILEGE") or "")
        grantable = " WITH GRANT OPTION" if row.get("GRANTABLE") == "YES" else ""
        sql = (
            f"GRANT {privilege} ON {_qualified(owner, object_name, keep_owner)} "
            f"TO {schema.lower()}{grantable};"
        )
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

def _render_directories(
    rows: list[dict[str, Any]],
    schema: str,
    keep_owner: bool = False,
) -> str:
    names = [
        _qualified(
            str(row.get("OWNER") or schema),
            str(row.get("DIRECTORY_NAME") or ""),
            keep_owner,
        )
        for row in rows
    ]
    lines = [
        f"CREATE OR REPLACE DIRECTORY {name:<31} "
        f"AS '{_escape_sql_text(str(row.get('DIRECTORY_PATH') or ''))}';"
        for name, row in zip(names, rows, strict=True)
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
    if not any(matches_sql_like(name, pattern) for pattern in prefixes):
        return False
    return not any(matches_sql_like(name, pattern) for pattern in (ignore or []))
