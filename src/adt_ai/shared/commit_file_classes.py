"""What a changed path IS, read off the project's export layout.

Split out of `commit_discovery.py` when ADT #429 pushed that file past the 20 KB
context guard. The seam is the question each half answers: the scanner decides
WHICH commits a run sees, this decides what a file inside one of them is, which
is the only part that has to know how an export lays its folders out.

The classes are coarse on purpose. They separate a database object from an APEX
component, an APEX full application export and a patch artifact, because those
four are treated differently downstream; they do not resolve an object type,
which is `patch/layout.py`'s job and needs the whole `object_types` map.
"""

from __future__ import annotations

import re
from pathlib import Path


def classify_file(
    path: str,
    *,
    include_full_exports: bool,
    apex_heads: tuple[tuple[str, ...], ...] = (),
) -> str | None:
    parts = Path(path).parts
    if not parts:
        return "file"
    # `database`/`apex` lead in the legacy layout and follow the schema in the
    # default layout (<schema>/database/..., <schema>/apex/...). Recognise both.
    if len(parts) >= 4 and parts[0].lower() == "database":
        return f"database:{parts[1]}:{parts[2]}"
    if len(parts) >= 4 and parts[1].lower() == "database":
        return f"database:{parts[0]}:{parts[2]}"
    apex_at = _apex_segment_index(parts, apex_heads)
    if apex_at is not None and len(parts) >= apex_at + 3:
        app = parts[apex_at + 1]
        if len(parts) == apex_at + 3 and re.fullmatch(r"f\d+\.sql", parts[apex_at + 2].lower()):
            return f"apex:{app}:full" if include_full_exports else None
        return f"apex:{app}:component"
    if len(parts) >= 2 and parts[0].lower() == "patch":
        return f"patch:{parts[1]}"
    return "file"


def _apex_segment_index(
    parts: tuple[str, ...],
    apex_heads: tuple[tuple[str, ...], ...],
) -> int | None:
    """Where the APEX export root ENDS in ``parts``, or None when it is elsewhere.

    The index is the last segment of the matched head, which is what the classic
    reading calls the `apex` segment, so the application folder is the one after
    it either way. Configured heads are tried first and the classic reading is
    the fallback, exactly as the layout helpers order them: the fallback is what
    every caller passing no heads gets, and the heads matter for a project whose
    `path_apex` puts `apex/` deeper than one level under the root. The cost of
    missing it is a full app export entering a patch that never asked for one
    with `-fullapp` (ADT #429).
    """
    for head in apex_heads:
        if len(parts) <= len(head):
            continue
        if all(
            ("<" in segment and ">" in segment) or segment.lower() == parts[index].lower()
            for index, segment in enumerate(head)
        ):
            return len(head) - 1
    if parts[0].lower() == "apex":
        return 0
    if len(parts) >= 2 and parts[1].lower() == "apex":
        return 1
    return None
