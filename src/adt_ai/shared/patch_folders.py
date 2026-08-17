"""Reading a patch FOLDER back off disk.

Split out of `commit_discovery.py` when ADT #309 pushed it past the 20 KB context
guard (`tests/contracts/test_context_file_size.py`). The seam is the question each
half answers, not the byte count that forced the split: this reads an artifact
that already exists on disk, while `commit_discovery.py` scans git history into
commit records and narrows them to a selection. The two meet only at
`PatchFolder`, which is why it lives here with its readers.

`commit_discovery` re-exports everything below, so every existing importer keeps
working unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from adt_ai.shared.deploy_status import latest_deploy_status, target_status
from adt_ai.shared.sql_like import matches_sql_like

PATCH_FOLDER_RE = re.compile(r"^(?P<day>\d{6})-(?P<sequence>\d+)-(?P<code>.+)$")


@dataclass(frozen=True)
class PatchFolder:
    folder: str
    path: Path
    patch_code: str
    driving_sql: str | None
    commits: list[str]
    files: list[str]
    target_status: dict[str, str]
    # The single newest deploy log in the folder, as `<TARGET>:<OUTCOME>` --
    # `None` for a patch nobody has deployed (ADT #268). `target_status` above
    # answers a different question (has THIS target succeeded) and is what the
    # deploy skip-guard reads; this one is what the listing shows.
    latest_status: str | None = None


def patch_id(patch_code: str) -> str | None:
    """The ticket/card number a patch code carries, or ``None``.

    Jan, 2026-08-10: "ID should be number from the ticket/card. For ivory 65 it
    should be 65, for SASDSG-5566 it should be 5566."

    The number lives in the FIRST underscore-separated segment, the ticket
    reference, and everything after it is the human label. Scanning the whole
    code would read `66_LAYER0_FIX` as `660`, because the `0` of `LAYER0` is not
    part of any card id. Within that segment the LAST digit run wins, so a
    project-prefixed reference (`SASDSG-5566`, `IVORY67`) yields the number
    rather than a fragment of the prefix.
    """
    numbers = re.findall(r"\d+", patch_code.split("_", 1)[0])
    return numbers[-1] if numbers else None


def matches_patch_selector(folder: PatchFolder, selector: str) -> bool:
    """Does ``selector`` name this folder: an id, or a SQL LIKE pattern?

    Jan asked for `patch -archive 202608%` (2026-08-15), so the selector grew a
    second shape beside the card id `#268` gave it. An all-digit selector stays
    an id lookup, exactly as before, which is what keeps the documented
    `-archive 66 67` working; anything else is read as a pattern.

    The pattern goes through `shared/sql_like.matches_sql_like`, the one
    client-side mirror of Oracle's own LIKE, so `%` and `_` mean here what they
    mean in every other filter this tool takes. Hand-rolling a second wildcard
    dialect is what that helper exists to prevent.
    """
    selector = selector.strip()
    if not selector:
        return False
    if selector.isdigit():
        found = patch_id(folder.patch_code)
        return found is not None and int(found) == int(selector)
    return any(
        matches_sql_like(candidate, selector)
        for candidate in patch_folder_match_targets(folder)
    )


def patch_folder_match_targets(folder: PatchFolder) -> list[str]:
    """Every spelling of a folder that a LIKE pattern is compared against.

    Three, and the third is the one that is not obvious. A folder is
    ``yymmdd-seq-CODE``, so ``260813-13-IVORY123_NTF_SPINE`` carries its date
    with a TWO-digit year, while Jan's own example pattern is ``202608%`` --
    which matches that name nowhere. The day is therefore also offered in its
    ``YYYYMMDD`` spelling, so a pattern written the way a person writes a month
    selects the patches from it. The two-digit form still works: the literal
    folder name is the first target and is never rewritten.

    The century comes from ``strptime``'s ``%y`` rule rather than a hardcoded
    ``20`` prefix, so the name is read back the same way ``strftime("%y%m%d")``
    wrote it. A name that does not parse contributes no third target.
    """
    targets = [folder.folder, folder.patch_code]
    match = PATCH_FOLDER_RE.match(folder.folder)
    if not match:
        return targets
    day = match.group("day")
    try:
        parsed = datetime.strptime(day, "%y%m%d")
    except ValueError:
        return targets
    targets.append(f"{parsed:%Y%m%d}{folder.folder[len(day):]}")
    return targets


def discover_patch_folders(patch_root: Path, *, ref: str | None = None) -> list[PatchFolder]:
    if not patch_root.exists():
        return []
    folders = [
        parse_patch_folder(path)
        for path in sorted(patch_root.iterdir(), key=lambda item: item.name, reverse=True)
        if path.is_dir()
    ]
    if ref:
        needle = ref.upper()
        if needle.isdigit():
            # An all-digit ref is an ID lookup, compared against the parsed card
            # number EXACTLY, the substring path would match `67` inside
            # `260809-1-66_LAYER67_FIX`, and inside the `yymmdd-seq-` prefix of
            # every folder created that day (ADT #268).
            folders = [
                folder
                for folder in folders
                if (found := patch_id(folder.patch_code)) is not None
                and int(found) == int(needle)
            ]
        else:
            folders = [
                folder
                for folder in folders
                if needle in folder.folder.upper() or needle in folder.patch_code.upper()
            ]
    return folders


def parse_patch_folder(path: Path) -> PatchFolder:
    """Read a patch folder back from the only artifact that gets deployed.

    Old ADT recovered both lists from the generated install script,
    ``get_file_references`` (patch.py:1028-1037) off the ``@"..."`` lines,
    ``get_file_commits`` (patch.py:1041-1056) off the ``-- COMMITS:`` block. The
    ``files.txt`` / ``commits.txt`` sidecars ADT.ai wrote instead were never an
    old-ADT artifact (Jan, 2026-08-09: "Looks like shit I never asked for"), and
    unioning ``files.txt`` with the link lines double-counted every file: the
    sidecar spelled it repo-relative, the link line under ``snapshots/`` (ADT
    #259). Old folders that still carry the sidecars keep parsing, so a patch
    built before this change stays deployable.
    """
    match = PATCH_FOLDER_RE.match(path.name)
    patch_code = match.group("code") if match else path.name
    sql_files = sorted(path.glob("*.sql"))
    # Both halves are needed and neither is a superset: a DELETED file is named
    # in the header but has no `@` line to link, and a file injected without a
    # commit behind it (a grant script) is linked but carries no change status.
    files = [*_patch_file_references(sql_files), *_patch_script_files(sql_files)]
    files = files or _read_lines(path / "files.txt")
    commits = _patch_script_commits(sql_files) or _read_lines(path / "commits.txt")
    return PatchFolder(
        folder        = path.name,
        path          = path,
        patch_code    = patch_code,
        driving_sql   = sql_files[0].name if sql_files else None,
        commits       = commits,
        files         = sorted(set(files)),
        target_status = target_status(path),
        latest_status = latest_deploy_status(path),
    )


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# `@"./snapshots/<path>";`, the repo-relative path is what every consumer wants,
# so the snapshot prefix the link line carries is stripped here, once.
_FILE_LINK_RE = re.compile(r'@\s*"?\.?/([^";\s]+)"?')

# `--   <number>) <summary>` inside the `-- COMMITS:` header block (old ADT
# patch.py:1764, read back at patch.py:1053).
_COMMIT_ROW_RE = re.compile(r"^--\s+(\d+\)\s.*\S)\s*$")

# `-- NEW FILES:` / `-- DELETED FILES:` / `-- MODIFIED FILES:` (old ADT
# patch.py:1768-1779).
_FILE_SECTION_RE = re.compile(r"^-- (NEW|DELETED|MODIFIED) FILES:\s*$")


def _patch_file_references(sql_files: list[Path]) -> list[str]:
    references: list[str] = []
    for sql_file in sql_files:
        text = sql_file.read_text(encoding="utf-8", errors="replace")
        for match in _FILE_LINK_RE.finditer(text):
            reference = match.group(1)
            references.append(_repo_relative_reference(reference))
    return references


def _repo_relative_reference(reference: str) -> str:
    """A link line's path as every consumer wants it: relative to the repo root.

    Three spellings reach here. An exported object is snapshotted, so its link
    carries a `snapshots/` prefix. A template is linked where it already lives
    (ADT #288), so its link carries leading `../` segments, the patch folder
    always sits under the root, so stripping them yields the root-relative path by
    construction, whatever depth `patch_root` puts it at.

    A per-patch SCRIPT is the third, and it is deliberately left alone: ADT #309
    MOVED it into the patch, so `patch_scripts/<slot>/<name>.sql` is already the
    truthful path, relative to the patch folder, which is the only place that
    file now exists. Its repo-relative source is gone from the tree, and the
    `PROMPT -- SCRIPT:` line above the link is what records where it came from.
    """
    reference = reference.split("snapshots/", 1)[-1]
    parts = list(reference.split("/"))
    while parts and parts[0] in ("..", "."):
        parts.pop(0)
    return "/".join(parts)


def _patch_script_files(sql_files: list[Path]) -> list[str]:
    """Paths listed under the header's NEW / DELETED / MODIFIED sections."""
    paths: list[str] = []
    for sql_file in sql_files:
        section = False
        for line in sql_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if _FILE_SECTION_RE.match(line):
                section = True
                continue
            if not section:
                continue
            if not line.startswith("--   "):
                section = False
                continue
            paths.append(line[5:].strip())
    return [path for path in paths if path]


def _patch_script_commits(sql_files: list[Path]) -> list[str]:
    commits: list[str] = []
    for sql_file in sql_files:
        extracting = False
        for line in sql_file.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("-- COMMITS:"):
                extracting = True
                continue
            if not extracting:
                continue
            if line.strip() == "--":
                break
            row = _COMMIT_ROW_RE.match(line)
            if row and row.group(1) not in commits:
                commits.append(row.group(1))
    return commits
