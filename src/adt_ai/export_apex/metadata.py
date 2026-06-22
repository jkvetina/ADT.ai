from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.row_values import row_value


def _store_application_metadata(path: Path, applications: list[ApexApplication]) -> None:
    if not applications:
        return
    payload = _load_yaml_mapping(path)
    for application in applications:
        payload[application.app_id] = {
            "owner": application.owner,
            "workspace": application.workspace,
            "workspace_id": application.workspace_id,
            "app_group": application.app_group,
            "app_id": application.app_id,
            "app_alias": application.app_alias,
            "app_name": application.app_name,
            "pages": application.pages,
            "updated_at": application.updated_at,
        }
    _store_yaml_mapping(path, payload)

def _store_workspace_developers(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    payload = _load_yaml_mapping(path)
    for row in rows:
        workspace = str(row_value(row, "WORKSPACE") or "")
        user_name = str(row_value(row, "USER_NAME") or "")
        user_mail = str(row_value(row, "USER_MAIL") or "")
        if not workspace or not user_name:
            continue
        workspace_developers = payload.get(workspace)
        if not isinstance(workspace_developers, dict):
            workspace_developers = {}
            payload[workspace] = workspace_developers
        workspace_developers[user_name] = user_mail
    _store_yaml_mapping(path, payload)

def _workspace_developers_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    developers: dict[str, dict[str, str]] = {}
    for row in rows:
        workspace = str(row_value(row, "WORKSPACE") or "")
        user_name = str(row_value(row, "USER_NAME") or "")
        user_mail = str(row_value(row, "USER_MAIL") or "")
        if not workspace or not user_name:
            continue
        developers.setdefault(workspace, {})[user_name] = user_mail
    return developers

def _load_yaml_mapping(path: Path) -> dict[Any, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data if isinstance(data, dict) else {}

def _store_yaml_mapping(path: Path, payload: Mapping[Any, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(
            dict(payload),
            default_flow_style=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )

def _merge_app_groups(path: Path, new_text: str) -> str:
    """Merge the workspace-shared ``app_groups.yaml`` instead of overwriting it.

    APEX's readable export emits only the application group(s) the exported app
    belongs to, so exporting a second app in the same workspace would otherwise
    clobber the first app's groups. Merge the freshly exported blocks with the
    existing file, keyed (deduplicated) by the group ``id``: existing entries
    keep their position, the latest export wins on conflicts, new groups are
    appended.
    """
    existing_text = path.read_text(encoding="utf-8") if path.is_file() else ""
    merged: dict[str, str] = {}
    for key, block in _parse_app_group_blocks(existing_text):
        merged[key] = block
    for key, block in _parse_app_group_blocks(new_text):
        merged[key] = block
    if not merged:
        return new_text
    return _render_app_group_blocks(merged.values())

def _parse_app_group_blocks(text: str) -> list[tuple[str, str]]:
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if line.strip() == "---":
            continue
        if line.startswith("- "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    parsed: list[tuple[str, str]] = []
    for block_lines in blocks:
        while block_lines and not block_lines[-1].strip():
            block_lines.pop()
        if not block_lines:
            continue
        block_id = None
        for line in block_lines:
            match = re.match(r"\s*id:\s*(\d+)", line)
            if match:
                block_id = match.group(1)
                break
        block_text = "\n".join(block_lines)
        parsed.append((block_id if block_id is not None else block_text, block_text))
    return parsed

def _render_app_group_blocks(blocks: Any) -> str:
    lines = ["---"]
    for block_text in blocks:
        lines.append(block_text)
        lines.append("")
    return "\n".join(lines) + "\n"
