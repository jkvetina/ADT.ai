"""Every text property of an application's components, one row each (ADT #895).

`search TERM` has to find a JavaScript function in a dynamic action, a plugin
name in an `ACTION_CODE`, a `javascript:` target on a button. The dependency
mirror cannot: `APEX_USED_DB_OBJECT_COMP_PROPS` only holds the SQL and PL/SQL
fragments the dependency scan compiled, and APEX 26.1 has no single view
holding every component's source either (measured, `895_apex_views.md`). So
the text is read view by view from the `APEX_APPLICATION_*` dictionary.

**The reader is driven by a spec, not by one query per release.** `SPEC` names
each view once, with its component label, its id, name and page columns, and
every text column worth searching. The columns the connected release really
has are read once per run from `ALL_TAB_COLUMNS` and intersected with the spec:

* a column an older APEX lacks drops out of that view's SELECT, so nothing
  fails on `ORA-00904` and nothing needs a version floor written by hand;
* a view left with no text column, or with no id column to key its rows by, is
  skipped rather than read;
* a column a newer APEX adds is ignored until someone adds it to the spec.

That is why the spec lists `ATTRIBUTE_01` to `ATTRIBUTE_25` everywhere rather
than the count each view happens to have on 26.1.

One SELECT per view, filtered on the application, and the unpivot happens here
in Python: SQL `UNPIVOT` refuses CLOB columns and the `VALUES` constructor needs
23ai, where client databases can be 19c. NULL, empty and `{}` values are
skipped; an `ATTRIBUTES` JSON object becomes one `ATTRIBUTES.<key>` row per key,
so a plugin attribute is found under its own name.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, NamedTuple

from adt_ai.dependencies import queries
from adt_ai.shared.db import QueryGateway


class ViewSpec(NamedTuple):
    #: The human label `search` prints, `DYNAMIC ACTION`, `PAGE TEMPLATE`.
    component_type: str
    #: The view's own id column; the application id for the application row.
    id_column: str
    #: What a reader calls the component, or None when the view has no name.
    name_column: str | None
    #: `PAGE_ID` for a page component, None for a shared component.
    page_column: str | None
    text_columns: tuple[str, ...]


def _numbered(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"{prefix}{number:02d}" for number in range(1, count + 1))


#: `ATTRIBUTE_01..25` and the JSON `ATTRIBUTES`: the plugin and component-type
#: settings, which is where a native `Execute JavaScript` keeps its code.
_ATTRIBUTES = (*_numbered("ATTRIBUTE_", 25), "ATTRIBUTES")
_CONDITIONS = ("CONDITION_EXPRESSION1", "CONDITION_EXPRESSION2")
_FILE_URLS = ("JAVASCRIPT_FILE_URLS", "CSS_FILE_URLS")

#: The action columns both DA action views share. `ACTION_CODE` is kept as a
#: property of its own so `PLUGIN_<name>` finds every action a plugin runs.
_DA_ACTION_TEXT = (
    "ACTION_CODE",
    *_ATTRIBUTES,
    "INIT_JAVASCRIPT_CODE",
    "AFFECTED_ELEMENTS",
    "CLIENT_CONDITION_EXPRESSION",
    "SERVER_CONDITION_EXPRESSION1",
    "SERVER_CONDITION_EXPRESSION2",
)

SPEC: dict[str, ViewSpec] = {
    "APEX_APPLICATIONS": ViewSpec(
        "APPLICATION", "APPLICATION_ID", "APPLICATION_NAME", None,
        (
            *_FILE_URLS, "DB_SESSION_INIT_CODE", "DB_SESSION_CLEANUP_CODE",
            "PWA_SERVICE_WORKER_HOOKS", "HTTP_RESPONSE_HEADERS", "HOME_LINK",
            "LOGIN_URL", "LOGOUT_URL",
        ),
    ),
    "APEX_APPLICATION_PAGES": ViewSpec(
        "PAGE", "PAGE_ID", "PAGE_NAME", "PAGE_ID",
        (
            "JAVASCRIPT_CODE", "JAVASCRIPT_CODE_ONLOAD", "INLINE_CSS", "PAGE_HTML_HEADER",
            "PAGE_HTML_ONLOAD", *_FILE_URLS, "HEADER_TEXT", "BODY_HEADER", "FOOTER_TEXT",
            "READ_ONLY_CONDITION_EXP1", "READ_ONLY_CONDITION_EXP2",
        ),
    ),
    "APEX_APPLICATION_PAGE_DA": ViewSpec(
        "DYNAMIC ACTION", "DYNAMIC_ACTION_ID", "DYNAMIC_ACTION_NAME", "PAGE_ID",
        (
            "WHEN_ELEMENT", "WHEN_CONDITION_ELEMENT", "WHEN_EXPRESSION",
            "WHEN_EVENT_CUSTOM_NAME", *_CONDITIONS,
        ),
    ),
    # The action carries no name of its own worth reading (`ACTION_NAME` is the
    # action type), so it is listed under the dynamic action that runs it.
    "APEX_APPLICATION_PAGE_DA_ACTS": ViewSpec(
        "DA ACTION", "ACTION_ID", "DYNAMIC_ACTION_NAME", "PAGE_ID", _DA_ACTION_TEXT
    ),
    # Actions behind a button, card or menu entry; no parent dynamic action.
    "APEX_APPL_PAGE_COMP_DA_ACTS": ViewSpec(
        "DA ACTION", "ACTION_ID", "ACTION_NAME", "PAGE_ID", _DA_ACTION_TEXT
    ),
    "APEX_APPLICATION_PAGE_BUTTONS": ViewSpec(
        "BUTTON", "BUTTON_ID", "BUTTON_NAME", "PAGE_ID",
        ("REDIRECT_URL", "BUTTON_ATTRIBUTES", *_CONDITIONS),
    ),
    "APEX_APPLICATION_PAGE_ITEMS": ViewSpec(
        "ITEM", "ITEM_ID", "ITEM_NAME", "PAGE_ID",
        (
            "ITEM_SOURCE", "ITEM_DEFAULT", "SOURCE_POST_COMPUTATION", "LOV_DEFINITION",
            "QUICK_PICK_SOURCE", "HTML_FORM_ELEMENT_ATTRIBUTES",
            "FORM_ELEMENT_OPTION_ATTRIBUTES", "PRE_ELEMENT_TEXT", "POST_ELEMENT_TEXT",
            "INIT_JAVASCRIPT_CODE", *_ATTRIBUTES, *_CONDITIONS,
            "READ_ONLY_CONDITION_EXP1", "READ_ONLY_CONDITION_EXP2",
        ),
    ),
    "APEX_APPLICATION_PAGE_REGIONS": ViewSpec(
        "REGION", "REGION_ID", "REGION_NAME", "PAGE_ID",
        (
            "REGION_SOURCE", *_ATTRIBUTES, "INIT_JAVASCRIPT_CODE", "REGION_HEADER_TEXT",
            "REGION_FOOTER_TEXT", "URL", "WHERE_CLAUSE", "ORDER_BY_CLAUSE", *_CONDITIONS,
        ),
    ),
    "APEX_APPLICATION_PAGE_PROC": ViewSpec(
        "PROCESS", "PROCESS_ID", "PROCESS_NAME", "PAGE_ID",
        ("PROCESS_SOURCE", *_ATTRIBUTES, *_CONDITIONS, "RUNTIME_WHERE_CLAUSE"),
    ),
    "APEX_APPLICATION_PROCESSES": ViewSpec(
        "APP PROCESS", "APPLICATION_PROCESS_ID", "PROCESS_NAME", None,
        ("PROCESS", *_ATTRIBUTES, *_CONDITIONS),
    ),
    "APEX_APPLICATION_PAGE_VAL": ViewSpec(
        "VALIDATION", "VALIDATION_ID", "VALIDATION_NAME", "PAGE_ID",
        ("VALIDATION_EXPRESSION1", "VALIDATION_EXPRESSION2", *_CONDITIONS),
    ),
    "APEX_APPLICATION_PAGE_COMP": ViewSpec(
        "COMPUTATION", "COMPUTATION_ID", "ITEM_NAME", "PAGE_ID",
        ("COMPUTATION", *_CONDITIONS),
    ),
    "APEX_APPLICATION_COMPUTATIONS": ViewSpec(
        "APP COMPUTATION", "APPLICATION_COMPUTATION_ID", "COMPUTATION_ITEM", None,
        ("COMPUTATION", *_CONDITIONS),
    ),
    "APEX_APPLICATION_LOVS": ViewSpec(
        "LOV", "LOV_ID", "LIST_OF_VALUES_NAME", None,
        ("LIST_OF_VALUES_QUERY", "WHERE_CLAUSE"),
    ),
    "APEX_APPL_PLUGINS": ViewSpec(
        "PLUGIN", "PLUGIN_ID", "NAME", None,
        (
            "PLSQL_CODE", "PARTIAL_TEMPLATE", "REPORT_BODY_TEMPLATE", "REPORT_GROUP_TEMPLATE",
            "REPORT_ROW_TEMPLATE", "REPORT_CONTAINER_TEMPLATE", *_FILE_URLS, *_ATTRIBUTES,
        ),
    ),
    "APEX_APPLICATION_AUTHORIZATION": ViewSpec(
        "AUTHORIZATION", "AUTHORIZATION_SCHEME_ID", "AUTHORIZATION_SCHEME_NAME", None,
        _ATTRIBUTES,
    ),
    "APEX_APPLICATION_AUTH": ViewSpec(
        "AUTHENTICATION", "AUTHENTICATION_SCHEME_ID", "AUTHENTICATION_SCHEME_NAME", None,
        ("PLSQL_CODE", *_ATTRIBUTES),
    ),
    "APEX_APPLICATION_TEMP_PAGE": ViewSpec(
        "PAGE TEMPLATE", "TEMPLATE_ID", "TEMPLATE_NAME", None,
        (
            "HEADER_TEMPLATE", "PAGE_BODY", "FOOTER_TEMPLATE", "JAVASCRIPT_CODE",
            "JAVASCRIPT_CODE_ONLOAD", "INLINE_CSS", "DIALOG_JS_INIT_CODE",
            "DIALOG_JS_CLOSE_CODE", "DIALOG_JS_CANCEL_CODE", *_FILE_URLS,
        ),
    ),
    "APEX_APPLICATION_TEMP_REGION": ViewSpec(
        "REGION TEMPLATE", "REGION_TEMPLATE_ID", "TEMPLATE_NAME", None,
        ("TEMPLATE", "TEMPLATE2", "TEMPLATE3", "JAVASCRIPT_CODE_ONLOAD", *_FILE_URLS),
    ),
    "APEX_APPLICATION_TEMP_LIST": ViewSpec(
        "LIST TEMPLATE", "LIST_TEMPLATE_ID", "TEMPLATE_NAME", None,
        (
            "LIST_TEMPLATE_CURRENT", "LIST_TEMPLATE_NONCURRENT", "SUB_LIST_ITEM_CURRENT",
            "SUB_LIST_ITEM_NONCURRENT", "ITEM_TEMPLATE_CURR_W_CHILD",
            "ITEM_TEMPLATE_NONCURR_W_CHILD", "SUB_TEMPLATE_CURR_W_CHILD",
            "SUB_TEMPLATE_NONCURR_W_CHILD", "JAVASCRIPT_CODE_ONLOAD", "INLINE_CSS",
            *_FILE_URLS,
        ),
    ),
    "APEX_APPLICATION_TEMP_REPORT": ViewSpec(
        "REPORT TEMPLATE", "TEMPLATE_ID", "TEMPLATE_NAME", None,
        (
            "COL_TEMPLATE1", "COL_TEMPLATE2", "COL_TEMPLATE3", "COL_TEMPLATE4",
            "COL_TEMPLATE_BEFORE_ROWS", "COL_TEMPLATE_AFTER_ROWS", "JAVASCRIPT_CODE_ONLOAD",
            *_FILE_URLS,
        ),
    ),
    "APEX_APPLICATION_TEMP_BUTTON": ViewSpec(
        "BUTTON TEMPLATE", "BUTTON_TEMPLATE_ID", "TEMPLATE_NAME", None,
        ("TEMPLATE", "HOT_TEMPLATE"),
    ),
    # `javascript:` targets live here and on the nav bar, and the page-link
    # store (`flow.db`) keeps only the page a link lands on.
    "APEX_APPLICATION_LIST_ENTRIES": ViewSpec(
        "LIST ENTRY", "LIST_ENTRY_ID", "ENTRY_TEXT", None,
        ("ENTRY_TARGET", "LINK_ATTRIBUTES", *_numbered("ENTRY_ATTRIBUTE_", 10), *_CONDITIONS),
    ),
    "APEX_APPLICATION_NAV_BAR": ViewSpec(
        "NAV BAR", "NAV_BAR_ID", "ICON_SUBTEXT", None,
        ("ICON_TARGET", "ONCLICK_JAVASCRIPT", *_CONDITIONS),
    ),
    "APEX_APPLICATION_PAGE_BRANCHES": ViewSpec(
        "BRANCH", "BRANCH_ID", "BRANCH_NAME", "PAGE_ID", ("BRANCH_ACTION", *_CONDITIONS)
    ),
}


def discover_columns(gateway: QueryGateway) -> dict[str, set[str]]:
    """The columns each spec view has on the connected APEX release."""
    rows = gateway.fetch_all(queries.apex_source_columns_query(tuple(SPEC)))
    columns: dict[str, set[str]] = {}
    for row in rows:
        columns.setdefault(str(row["TABLE_NAME"]), set()).add(str(row["COLUMN_NAME"]))
    return columns


def read_component_source(
    gateway: QueryGateway, app_id: int, columns: Mapping[str, set[str]]
) -> list[dict[str, Any]]:
    """Every non-empty text property of ``app_id``, as `APEX_COMPONENT_SOURCE` rows."""
    rows: list[dict[str, Any]] = []
    for view, spec in SPEC.items():
        present = columns.get(view, set())
        texts = tuple(column for column in spec.text_columns if column in present)
        if spec.id_column not in present or not texts:
            continue
        keys = [spec.id_column]
        for column in (spec.name_column, spec.page_column):
            if column and column in present and column not in keys:
                keys.append(column)
        query = queries.apex_source_view_query(view, (*keys, *texts))
        for record in gateway.fetch_all(query, {"app_id": app_id}):
            rows.extend(_unpivot(app_id, spec, record, texts))
    return rows


def _unpivot(
    app_id: int, spec: ViewSpec, record: Mapping[str, Any], texts: tuple[str, ...]
) -> list[dict[str, Any]]:
    base = {
        "APPLICATION_ID": app_id,
        "PAGE_ID": record.get(spec.page_column) if spec.page_column else None,
        "COMPONENT_TYPE": spec.component_type,
        # Text, never an int: an APEX id is a NUMBER, and a plugin id on the
        # 26.1 container reads 16382027049111291021, past SQLite's 64-bit
        # INTEGER, which `sqlite3` refuses to bind (ADT #901).
        "COMPONENT_ID": str(record[spec.id_column]),
        "COMPONENT_NAME": record.get(spec.name_column) if spec.name_column else None,
    }
    rows = []
    for column in texts:
        for prop, text in _properties(column, as_text(record.get(column))):
            rows.append({**base, "PROPERTY": prop, "TEXT": text})
    return rows


def _properties(column: str, text: str | None) -> list[tuple[str, str]]:
    """``(property, text)`` pairs for one column, empty values dropped."""
    if text is None or not _has_text(text):
        return []
    if column != "ATTRIBUTES":
        return [(column, text)]
    try:
        parsed = json.loads(text)
    except ValueError:
        return [(column, text)]
    if not isinstance(parsed, dict):
        return [(column, text)]
    pairs = []
    for key, value in parsed.items():
        rendered = value if isinstance(value, str) else json.dumps(value)
        if value is not None and _has_text(rendered):
            pairs.append((f"ATTRIBUTES.{key}", rendered))
    return pairs


def _has_text(text: str) -> bool:
    """Whether a value holds anything to search: not blank and not `{}`.

    An empty `ATTRIBUTES` arrives as the string `{}`, never NULL (measured on
    every fixture region), so it is dropped by its text rather than its type.
    """
    return text.strip() not in ("", "{}")


def as_text(value: Any) -> str | None:
    """A dictionary value as text, whichever shape the gateway handed back.

    The Oracle gateway maps CLOB to `str` through its output type handler, but
    a LOB locator (a handler not installed, a driver that ignores it) answers
    `read()`, and SQLcl can hand back bytes, so each is read the one way it can
    be rather than trusted to be a string.
    """
    if value is None or isinstance(value, str):
        return value
    if hasattr(value, "read"):
        return as_text(value.read())
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).decode("utf-8", errors="replace")
    return str(value)


__all__ = ["SPEC", "ViewSpec", "as_text", "discover_columns", "read_component_source"]
