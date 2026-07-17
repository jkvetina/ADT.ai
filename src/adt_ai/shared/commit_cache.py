from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class HistoryRecord:
    number: int
    id: str
    summary: str
    author: str
    date: str
    files: dict[str, str]
    deleted: list[str]
    patch: str | None = None

    @property
    def commit_hash(self) -> str:
        return self.id


def current_branch(root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or "HEAD"


def cache_path(
    root: Path,
    cache_file_template: str = "./config/commits/#BRANCH#.yaml",
    branch: str = "main",
) -> Path:
    path = Path(cache_file_template.replace("#BRANCH#", branch)).expanduser()
    return path if path.is_absolute() else root / path


def load_history_cache(
    root: Path,
    branch: str,
    cache_file_template: str = "./config/commits/#BRANCH#.yaml",
) -> dict[int, HistoryRecord]:
    path = cache_path(root, cache_file_template, branch)
    if not path.is_file():
        return {}
    # A hand-edited or truncated cache must degrade to "no cache" (the next
    # rebuild rewrites it from git), not crash the caller — but never silently:
    # a partial cache resumed as-is could pin history to the wrong tip.
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            raise ValueError("cache root is not a mapping")
        records: dict[int, HistoryRecord] = {}
        for number, fields in data.items():
            records[int(number)] = HistoryRecord(
                number  = int(number),
                id      = fields["id"],
                summary = fields.get("summary", ""),
                author  = fields.get("author", ""),
                date    = fields.get("date", ""),
                files   = fields.get("files") or {},
                deleted = fields.get("deleted") or [],
                patch   = fields.get("patch"),
            )
        return records
    except Exception as error:
        print(
            f"Warning: ignoring unreadable commit cache {path}: {error}",
            file=sys.stderr,
        )
        return {}
