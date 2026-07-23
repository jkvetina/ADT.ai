"""Load the per-developer ``config/IDENTITY.yaml`` identity file.

``IDENTITY.yaml`` is gitignored and never committed — it holds the developer's
personal identity (``db_schema``, ``apex_account``, ``email``) so a schema
worked by several developers can still resolve who did what. There is
deliberately no committed sample; the shape is documented in ``USAGE.md``.

Two consumers read it: ``export_db -my`` filters exports to objects last
changed by ``db_schema``, and every new database connection sets
``DBMS_SESSION.SET_IDENTIFIER(db_schema)`` before ``STARTUP.sql`` runs.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

IDENTITY_FILENAME = "IDENTITY.yaml"


def load_identity(search_paths: Iterable[str | Path]) -> dict[str, Any]:
    """Return the first ``IDENTITY.yaml`` found on ``search_paths``, else ``{}``."""
    for directory in search_paths:
        path = Path(directory) / IDENTITY_FILENAME
        if path.is_file():
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if isinstance(data, dict):
                return data
    return {}


def session_identifier(identity: Mapping[str, Any]) -> str | None:
    """The identity's db schema, used as the session identifier, else ``None``."""
    value = (
        identity.get("db_schema")
        or identity.get("db")
        or identity.get("schema")
    )
    return str(value) if value else None
