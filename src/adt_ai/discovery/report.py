from __future__ import annotations

from datetime import datetime
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.internal_paths import ensure_gitignored

GITIGNORE_ENTRY = "config/discovery/"


def report_path(root: Path, when: datetime) -> Path:
    """Return the discovery report path for ``when`` (local 24h time).

    ``<root>/config/discovery/<YYYY-MM-DD--HH-MI>.md``, the minute granularity is
    what makes same-minute re-runs append to one file (see ``append_sections``).
    """
    return root / "config" / "discovery" / f"{when:%Y-%m-%d--%H-%M}.md"


def next_query_index(path: Path) -> int:
    """Return the 1-based number for the next query section in ``path``.

    Counts existing ``## `` section headers so same-minute appends continue the
    run-wide numbering instead of restarting at 1. A comment-derived header still
    counts, every rendered query owns one number.
    """
    if not path.exists():
        return 1
    text = path.read_text(encoding="utf-8")
    existing = sum(1 for line in text.splitlines() if line.startswith("## "))
    return existing + 1


def append_sections(path: Path, sections: list[str]) -> None:
    """Write rendered ``sections`` to ``path``, creating or appending.

    Creates the parent folder when missing. A same-minute re-run appends after a
    blank-line separator instead of overwriting. Each section already ends with a
    newline; joining with ``\\n`` yields the blank line between sections.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(sections)
    if path.exists() and path.read_text(encoding="utf-8").strip():
        with text_files.open_text(path, "a") as handle:
            handle.write("\n" + body)
    else:
        text_files.write_text(path, body)


def ensure_discovery_ignored(root: Path) -> None:
    """Idempotently ensure ``config/discovery/`` is git-ignored in ``root``.

    Through the one shared read-modify-write (ADT #1094): this copy read the
    file unguarded, so a cp1250 comment in ``.gitignore`` crashed `discovery`
    after its report had already been written.
    """
    ensure_gitignored(root, GITIGNORE_ENTRY)
