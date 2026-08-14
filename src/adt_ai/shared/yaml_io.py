from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared import text_files


def load_yaml_mapping(path: Path) -> dict[Any, Any]:
    """Read a YAML mapping file; missing, empty, non-mapping, or corrupt files yield {}.

    The callers sit behind gitignored caches (recent watermarks, apex timers,
    apps metadata). A corrupt cache costs one warning and a rebuild on the next
    refresh, never the whole command run.
    """
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as error:
        print(f"Warning: ignoring unreadable YAML file {path}: {error}", file=sys.stderr)
        return {}
    return data if isinstance(data, dict) else {}


def store_yaml_mapping(path: Path, payload: Mapping[Any, Any]) -> None:
    """Write a mapping as sorted block-style YAML, creating parent folders."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text_files.write_text(
        path,
        yaml.safe_dump(
            dict(payload),
            default_flow_style=False,
            sort_keys=True,
        ),
    )
