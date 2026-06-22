from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adt_ai.export_apex.inventory import ApexApplication
from adt_ai.row_values import row_value


def workspace_developers_from_mapping(
    payload: Mapping[Any, Any],
) -> dict[str, dict[str, str]]:
    developers: dict[str, dict[str, str]] = {}
    for workspace, workspace_developers in payload.items():
        if not isinstance(workspace_developers, Mapping):
            continue
        workspace_key = str(workspace or "")
        if not workspace_key:
            continue
        for user_name, user_mail in workspace_developers.items():
            user_key = str(user_name or "")
            if not user_key:
                continue
            developers.setdefault(workspace_key, {})[user_key] = str(user_mail or "")
    return developers


def merge_workspace_developers(
    *sources: Mapping[str, Mapping[str, str]],
) -> dict[str, dict[str, str]]:
    merged: dict[str, dict[str, str]] = {}
    for source in sources:
        for workspace, developers in source.items():
            merged.setdefault(workspace, {}).update(dict(developers))
    return merged


def recent_author_label(
    application: ApexApplication,
    developers: Mapping[str, Mapping[str, str]],
    request: Any,
) -> str:
    if request.my_changes:
        return "ME"
    changed_by = request.changed_by or ""
    if not changed_by:
        return ""
    workspace_developers = developers.get(application.workspace, {})
    return changed_by if changed_by in workspace_developers else changed_by


def recent_authors(
    application: ApexApplication,
    developers: Mapping[str, Mapping[str, str]],
    request: Any,
) -> list[str]:
    authors: list[str] = []
    if request.changed_by:
        authors.append(request.changed_by)
    if request.my_changes:
        authors.extend(
            _my_author_aliases(
                developers.get(application.workspace, {}),
                request.my_name,
                request.my_email,
            )
        )
    return _unique_nonblank(authors)


def dedupe_recent_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[object, object, object]] = set()
    deduped: list[dict[str, Any]] = []
    for row in rows:
        key = (
            row_value(row, "TYPE_NAME"),
            row_value(row, "ID"),
            row_value(row, "NAME"),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)
    return deduped


def _my_author_aliases(
    workspace_developers: Mapping[str, str],
    my_name: str | None,
    my_email: str | None,
) -> list[str]:
    aliases: list[str] = []
    identity_keys = set(_identity_seed_values(my_name, my_email))
    if my_email:
        aliases.extend([my_email, my_email.upper()])
    for user_name, user_mail in workspace_developers.items():
        if (
            _identity_key(user_name) in identity_keys
            or _identity_key(user_mail) in identity_keys
        ):
            aliases.extend([user_name, user_mail])
    aliases.extend(_name_aliases(my_name))
    return _unique_nonblank(aliases)


def _identity_seed_values(my_name: str | None, my_email: str | None) -> set[str]:
    values = {_identity_key(alias) for alias in _name_aliases(my_name)}
    if my_email:
        values.add(_identity_key(my_email))
        values.add(_identity_key(my_email.split("@", 1)[0]))
    return {value for value in values if value}


def _name_aliases(my_name: str | None) -> list[str]:
    if not my_name:
        return []
    parts = [part for part in my_name.replace(".", " ").replace("_", " ").split() if part]
    if not parts:
        return []
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    aliases = [first]
    if last:
        aliases.extend(
            [
                f"{first}{last[0]}",
                f"{first}.{last}",
                f"{first}_{last}",
                f"{first}-{last}",
                f"{first}{last}",
            ]
        )
    return [alias.upper() for alias in aliases]


def _identity_key(value: object) -> str:
    return "".join(ch for ch in str(value or "").lower() if ch.isalnum())


def _unique_nonblank(values: list[str | None]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        unique.append(text)
    return unique
