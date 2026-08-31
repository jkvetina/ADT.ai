"""Filesystem-boundary validation for configured and database-derived paths."""

from __future__ import annotations

import re
from pathlib import Path

from adt_ai.shared.sql_identifiers import is_identifier

_SIMPLE_COMPONENT = re.compile(r"[A-Za-z0-9_$#.-]+")
_WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")


class UnsafePathError(ValueError):
    """A value would create an unsupported or out-of-root filesystem path."""


def simple_oracle_identifier(value: object, *, role: str) -> str:
    """Return an unquoted Oracle identifier that maps to one stable filename."""
    text = str(value)
    if not is_identifier(text):
        raise UnsafePathError(
            f"Unsupported {role} {text!r}; ADT exports only unquoted Oracle "
            "identifiers as filenames"
        )
    return text


def simple_component(value: object, *, role: str) -> str:
    """Return one human-readable filename component or reject it."""
    text = str(value)
    if text in {"", ".", ".."} or not _SIMPLE_COMPONENT.fullmatch(text):
        raise UnsafePathError(
            f"Unsupported {role} {text!r}; use only letters, digits, _, -, ., $, or #"
        )
    return text


def simple_relative_path(value: object, *, role: str) -> Path:
    """Return a simple relative path whose components cannot traverse upward."""
    text = str(value).replace("\\", "/").strip()
    if not text or text.startswith("/") or _WINDOWS_DRIVE.match(text):
        raise UnsafePathError(f"{role} must be a non-empty relative path: {value!r}")
    parts = [part for part in text.split("/") if part and part != "."]
    if not parts:
        raise UnsafePathError(f"{role} must name a folder: {value!r}")
    return Path(*(simple_component(part, role=role) for part in parts))


def under_root(root: Path, path: Path, *, role: str) -> Path:
    """Return ``path`` only when its resolved target stays below ``root``."""
    root_resolved = Path(root).resolve()
    path_resolved = Path(path).resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError as error:
        raise UnsafePathError(f"{role} must stay under {root_resolved}: {path}") from error
    return Path(path)
