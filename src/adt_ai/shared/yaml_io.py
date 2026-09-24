from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared import text_files
from adt_ai.shared.file_list import plain_row, print_file_rows
from adt_ai.shared.progress import print_adt_header

#: `#861`, spelled by Jan picking it: a file the reader skipped is named under
#: its own header, the file on the row and the parser's reason under it, rather
#: than as one bare `Warning: ignoring unreadable YAML file ...` line.
UNREADABLE_FILES_HEADER = "WARNING - UNREADABLE FILES:"


def load_yaml_mapping(path: Path, *, quiet: bool = False) -> dict[Any, Any]:
    """Read a YAML mapping file; missing, empty, non-mapping, or corrupt files yield {}.

    The callers sit behind gitignored caches (recent watermarks, apex timers,
    apps metadata). A corrupt cache costs one warning and a rebuild on the next
    refresh, never the whole command run. A file that is not UTF-8 is corrupt in
    exactly that sense, and so is a value YAML builds and then rejects itself;
    both raised past this as a `ValueError` until ADT #923.

    ``quiet`` is for the one reader that runs before the command banner, the
    legacy APEX cache conversion behind the early CLI hook, where nothing may
    print (`internal_paths`): the warning stays with the reader that runs after
    it. A file that cannot be opened at all still raises ``OSError`` either way,
    because it is not known to be empty, and that caller leaves such a root as
    found rather than converting the file as empty and deleting it.
    """
    if not path.is_file():
        return {}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (ValueError, yaml.YAMLError) as error:
        if not quiet:
            print_unreadable_yaml(path, error)
        return {}
    return data if isinstance(data, dict) else {}


def print_unreadable_yaml(path: Path, error: Exception) -> None:
    """The `UNREADABLE FILES:` section for one file a YAML reader skipped.

    **On stdout, like every other section, although the bare line was on stderr.**
    Written first to stderr, the live run on `SANDBOX` lost the blank under it:
    the runtime holds each stream's trailing newlines back and settles them per
    stream, so the next stdout header landed flush against the last row. A
    command whose stdout is a document, `search -format yaml`, already
    routes its chrome to stderr around the whole segment, and this rides it.
    """
    reason = _yaml_reason(error)
    print_adt_header(UNREADABLE_FILES_HEADER)
    print_file_rows(
        [str(path)], nested=False, children=lambda _path, depth: [plain_row(reason, depth)]
    )


def _yaml_reason(error: Exception) -> str:
    """The parser's problem and where it is, on one line.

    PyYAML's own text runs to four lines, the last two a copy of the offending
    source with a caret under it, which is a screen of its own under a row.
    """
    problem = getattr(error, "problem", None)
    mark = getattr(error, "problem_mark", None)
    if problem and mark is not None:
        return f"{problem}, line {mark.line + 1}, column {mark.column + 1}"
    return str(error).strip().splitlines()[0] if str(error).strip() else type(error).__name__


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
