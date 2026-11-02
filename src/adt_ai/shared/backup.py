"""`-backup` (ADT #1099): one flag on every export command that leaves nothing out.

A normal export is shaped for patching, and the shipped config says so: a
`where` filter on `app_id`, the audit columns in `ignored_columns`, no saved
interactive reports, no sequence positions. A client backup taken with those
defaults silently lost 18 rows, the audit columns of 514 tables, 18 saved user
reports and 604 sequence positions. Each command reads `-backup` for its own
part; nothing in `config.yaml` changes, so a patch export is untouched.

- `export_data`: no `where` filter and no `ignored_columns` (`export_data/runner.py`).
- `export_db`: each sequence keeps its `START WITH` (`object_normalizers/sequence.py`).
- `export_apex`: the flags below, which this module owns.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

#: The `config.yaml` flags that keep saved interactive reports out of an APEX
#: export by default.
APEX_BACKUP_FLAGS = (
    "apex_with_ir_public_reports",
    "apex_with_ir_private_reports",
    "apex_with_ir_notifications",
)


def apex_backup_config(config: Mapping[str, Any]) -> dict[str, Any]:
    """`config` with every saved-report flag on, for this run only."""
    return {**config, **dict.fromkeys(APEX_BACKUP_FLAGS, True)}


__all__ = ["APEX_BACKUP_FLAGS", "apex_backup_config"]
