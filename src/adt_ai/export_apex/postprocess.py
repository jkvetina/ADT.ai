from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from adt_ai.export_apex import queries
from adt_ai.export_apex.files import ApexFileResolver
from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.row_values import row_value


def _export_options(config: Mapping[str, object]) -> dict[str, object]:
    return {
        "originals"               : _flag(config.get("apex_keep_original_id")),
        "with_comments"           : _flag(config.get("apex_comments")),
        "with_date"               : _flag(config.get("apex_with_date")),
        "with_ir_public_reports"  : _flag(config.get("apex_with_ir_public_reports")),
        "with_ir_private_reports" : _flag(config.get("apex_with_ir_private_reports")),
        "with_ir_notifications"   : _flag(config.get("apex_with_ir_notifications")),
        "with_translations"       : _flag(config.get("apex_with_translations")),
        "with_no_subscriptions"   : _flag(config.get("apex_with_no_subscriptions")),
        "with_acl_assignments"    : _flag(config.get("apex_with_acl_assignments")),
        "with_audit_info"         : str(config.get("apex_with_audit_info") or ""),
    }

def _bind_params(sql: str, params: Mapping[str, Any]) -> dict[str, Any]:
    bind_names = set(re.findall(r":([A-Za-z][A-Za-z0-9_]*)", sql))
    return {
        key: value
        for key, value in params.items()
        if key in bind_names
    }

def _target_path(
    resolver: ApexFileResolver,
    application: ApexApplication,
    action: str,
    file_name: str,
) -> Path:
    relative = _strip_app_prefix(file_name, application)
    if action == "full":
        return resolver.full_export(application)
    if action == "apexlang":
        # Before the `workspace/` diversion below: an APEXlang tree carries its
        # own `workspace-components/` folder and must stay whole under
        # `apexlang/`, never be split across the shared workspace root.
        return resolver.apexlang_export(application, relative)
    if relative.startswith("workspace/"):
        return resolver.workspace_root() / Path(relative.removeprefix("workspace/"))
    if action == "split":
        return resolver.split_export(application, relative)
    if action == "readable":
        return resolver.readable_export(application, relative)
    if action == "embedded":
        return resolver.embedded_export(application, _embedded_relative(relative))
    raise ValueError(f"Unsupported APEX export action: {action}")

def _strip_app_prefix(file_name: str, application: ApexApplication) -> str:
    prefix = f"f{application.app_id}/"
    return file_name[len(prefix):] if file_name.startswith(prefix) else file_name

def _embedded_relative(relative: str) -> str:
    prefix = "embedded_code/"
    if relative.startswith(prefix):
        relative = relative[len(prefix):]
    if relative.startswith("pages/p"):
        relative = relative.replace("pages/p", "pages/page_", 1)
    return relative

def _payload_for(
    action: str,
    payload: str,
    relative: str,
    application: ApexApplication,
    enrichments: Mapping[int, str],
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
    release: str | None = None,
) -> str:
    if action == "embedded":
        lines = payload.splitlines(keepends=True)
        embedded_payload = "".join(lines[10:]) if len(lines) > 10 else payload
        output = _normalize_text_line_endings(embedded_payload)
    elif action == "full" and relative.endswith(".sql"):
        output = _enrich_sql(payload, enrichments)
    elif action == "split" and relative.endswith(".sql"):
        output = _clean_split_sql(payload, relative, application, enrichments, config, developers)
    else:
        output = payload
    return _override_apex_release(output, release) if relative.endswith(".sql") else output

def _override_apex_release(payload: str, release: str | None) -> str:
    if not release:
        return payload
    return re.sub(r"p_release=>'[^']+'", f"p_release=>'{release}'", payload)

def _normalize_text_line_endings(payload: str) -> str:
    return payload.replace("\r\n", "\n").replace("\r", "\n")

APEXLANG_STATIC_FILES_PREFIX = "shared-components/static-files/"


def _skip_collection_file(action: str, relative: str) -> bool:
    if action == "apexlang":
        # The PL/SQL block already drops static-file payloads; the writer refuses
        # them too so `-files` stays the single static-file channel even if a
        # future APEX build returns one as a CLOB. The sibling
        # `shared-components/static-files.apx` metadata is not under this prefix
        # and stays in.
        return relative.startswith(APEXLANG_STATIC_FILES_PREFIX)
    if action != "split":
        return False
    return (
        relative == "install.sql"
        or relative == "application/create_application.sql"
        or re.fullmatch(r"f\d+\.sql", relative) is not None
    )

def _clean_split_sql(
    payload: str,
    relative: str,
    application: ApexApplication,
    enrichments: Mapping[int, str],
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
) -> str:
    offset = _default_id_offset(payload)
    payload = re.sub(r",p_default_id_offset=>(\d+)", ",p_default_id_offset=>0", payload)
    payload = _clean_page_author(payload, relative, application, config, developers)
    return _enrich_sql(payload, enrichments, offset=offset)

def _clean_page_author(
    payload: str,
    relative: str,
    application: ApexApplication,
    config: Mapping[str, object],
    developers: Mapping[str, Mapping[str, str]],
) -> str:
    if not relative.startswith("application/pages/page_"):
        return payload
    author = str(config.get("apex_authors") or "")
    if not author or not bool(config.get("apex_keep_developers")):
        return payload
    developer = _extract_first(r",p_last_updated_by=>'([^']+)'", payload)
    if developer in developers.get(application.workspace, {}):
        return payload
    payload = re.sub(r",p_last_updated_by=>'([^']+)'", f",p_last_updated_by=>'{author}'", payload)
    timestamp = str(config.get("apex_timestamps") or "")
    if timestamp:
        payload = re.sub(
            r",p_last_upd_yyyymmddhh24miss=>'(\d+)'",
            f",p_last_upd_yyyymmddhh24miss=>'{timestamp}'",
            payload,
        )
    return payload

def _enrich_sql(payload: str, enrichments: Mapping[int, str], offset: int = 0) -> str:
    for component_id, component_name in enrichments.items():
        payload = payload.replace(
            f".id({component_id})\n",
            f".id({component_id})  -- {component_name}\n",
        )
        if offset:
            shifted_id = component_id - offset
            payload = payload.replace(
                f".id({shifted_id})\n",
                f".id({shifted_id})  -- {component_name}\n",
            )
    return payload

def _default_id_offset(payload: str) -> int:
    match = re.search(r",p_default_id_offset=>(\d+)", payload)
    return int(match.group(1)) if match else 0

def _extract_first(pattern: str, text: str) -> str:
    match = re.search(pattern, text)
    return match.group(1) if match else ""

def _checksum_value(rows: list[dict[str, Any]]) -> str:
    """The fingerprint APEX returns, reduced to the value itself.

    `CHECKSUM-SH256` comes back as a one-member collection whose payload is
    file contents, so it carries whatever line endings produced it. It is
    stored as a YAML scalar now rather than written to a file (ADT #343), and
    a scalar carries none of that. APEX names the member itself, and there is
    only ever one, so the name is not read.
    """
    for row in rows:
        value = str(row_value(row, "CLOB_CONTENT") or "").strip()
        if value:
            return value
    return ""

def _enrichments(gateway: QueryGateway, application: ApexApplication) -> dict[int, str]:
    rows = gateway.fetch_all(queries.APEX_ID_NAMES_QUERY, {"app_id": application.app_id})
    return {
        int(row_value(row, "COMPONENT_ID")): (
            f"{row_value(row, 'COMPONENT_TYPE')}: {row_value(row, 'COMPONENT_NAME')}"
        )
        for row in rows
        if row_value(row, "COMPONENT_ID") is not None
    }

def _flag(value: object) -> str:
    return "Y" if bool(value) else "N"

def _blob_bytes(value: Any) -> bytes:
    if value is None:
        return b""
    if isinstance(value, bytes):
        return value
    if hasattr(value, "read"):
        return value.read()
    return bytes(value)
