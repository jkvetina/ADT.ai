"""Every static file an APEXlang tree names and does not carry (ADT #930).

Each `static-files.apx` declares its payloads as `file <name> (` blocks, and the
compiler resolves every one against the `static-files/` folder beside it: the
application's own under `shared-components/`, each plugin's and each theme's under
its own folder. A name with no file behind it is one `REFERENCE_NOT_FOUND`, and
`apex import` compiles before it writes, so a single missing file refuses the
whole import.

Jan, 2026-09-23, after a deploy ran its `init` half and then failed on 67 of them:
*"We should check before the deploy that we have files exported and stop if not,
since there is no way it will succeed without them"*. This is that check, read
off the tree alone, so it costs no database round trip and runs before anything
does.
"""

from __future__ import annotations

import re
from pathlib import Path

from adt_ai.shared.file_list import CAPPED_ROWS, capped, more_row, plain_row

METADATA_NAME = "static-files.apx"
PAYLOAD_DIR = "static-files"

# `file app.css (` or `file "css/app.css" (`, the only two spellings the 26.1
# export writes; the name is the path below the sibling `static-files/` folder.
_FILE_RE = re.compile(r'^file\s+(?:"(?P<quoted>[^"]+)"|(?P<bare>\S+))\s*\(\s*$')


def missing_static_files(tree_root: Path) -> list[str]:
    """Each referenced payload not on disk, relative to ``tree_root``, sorted."""
    missing: list[str] = []
    for metadata in sorted(tree_root.rglob(METADATA_NAME)):
        folder = metadata.parent / PAYLOAD_DIR
        for name in _declared(metadata):
            if not (folder / name).is_file():
                missing.append((folder / name).relative_to(tree_root).as_posix())
    return sorted(missing)


def missing_refusal(
    app_id: int, label: str, missing: list[str], shown: int = CAPPED_ROWS
) -> str:
    """The refusal naming the missing files and the export that writes them.

    The first ``shown`` by path, then a count, because a tree missing every plugin
    file names dozens and the screen needs the pattern, not the inventory. The
    export named is the one that restores both halves: `-apexlang` writes the
    plugin and theme files, `-files` the application's own. The count opens the
    screen as a short uppercase headline (ADT #934).
    """
    lines = [
        f"APP {app_id} IS MISSING {len(missing)} STATIC FILE(S)",
        "",
        f"Its tree {label} does not carry them, and `apex import` refuses a tree",
        "missing even one:",
    ]
    kept, remaining = capped(missing, shown)
    lines.extend(plain_row(path) for path in kept)
    if remaining:
        lines.append(more_row(remaining))
    lines.append(f"Run: adt export_apex -app {app_id} -apexlang -files, then commit the files")
    return "\n".join(lines)


def _declared(metadata: Path) -> list[str]:
    try:
        text = metadata.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names: list[str] = []
    for line in text.splitlines():
        match = _FILE_RE.match(line.strip())
        if match:
            names.append(match.group("quoted") or match.group("bare"))
    return names


__all__ = ["METADATA_NAME", "PAYLOAD_DIR", "missing_refusal", "missing_static_files"]
