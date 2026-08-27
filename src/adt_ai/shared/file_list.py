"""The one renderer for every console list whose rows are file paths (ADT #504).

Jan, 2026-08-24, reading a `PROCESSED FILES: ICT_OWNER` block: every row repeated
`ict_owner/database/tables/` and the folder was the part he was reading past. The
rows group under their folder now, each file indented two spaces further, with
the trailing slash kept so a folder line cannot be mistaken for a file::

      - ict_owner/database/synonyms/
        - UT/
          - be_between.sql

**One row is one directory** (ADT #507). `#504` gave the list exactly two levels,
a folder row and a leaf row, so every directory the folder rule did not claim
stayed glued onto one of them: `UT/be_between.sql` sat on a leaf and
`patch_scripts/APP309/tables_after/` on a folder line. The renderer walks from
the ANCHOR folder down to the file instead, one row per level, which is what puts
a script's slot and an `export_db -groups` sub-folder on rows of their own.

**One unit is two spaces and a child is always its parent plus one.** That single
rule is what fixes the nested commit rows under `WARNING - OUTDATED FILES:`,
which were four spaces past their file rather than two (*"they are indented with
6 spaces, that is wrong, I want just 4"*), and it is why `depth` is an argument
here rather than an indent each caller spells for itself.

**The module exists for the reuse, not for the shape.** Jan asked for the change
with the reason attached: *"you will reuse the code how to print nested files and
commits, so we dont face same fuckups as in the past where you did partially
hardcoded same fix on 6 other places"*. Fifteen sections printed their own
`print(f"  - {path}")` before this card, in five packages, and two of them had
already grown a nested form by hand. `tests/contracts/shared_readers.txt` carries
a `file_list` rule so a sixteenth fails the suite rather than quietly diverging.

**The anchor is injected, and there are exactly two rules.** `parent_folder`
below is the default and answers for any path. The one a project config can
improve is `patch/object_folders.object_folder_resolver`, which anchors an
exported object file at its `path_objects` type folder so a type folder stays
whole however many segments it carries. That resolver reads the `path_objects`
template, which lives in `patch/`, and `shared/` never imports a command package,
so the caller passes it rather than this module reaching for it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import PurePosixPath
from typing import Any

#: Nesting is on unless a project turns it off. Jan asked for the flag in the
#: same breath as the format, so a project that reads its patches in a narrow
#: terminal keeps the flat list.
NESTED_FILES_DEFAULT = True

#: One level. Every row on an ADT screen already opens on two spaces
#: (`shared/progress.ROW_INDENT`), and a child adds one more of these.
INDENT = "  "

#: The one character every listed row opens on (ADT #456). Old ADT split them
#: three ways and Jan asked for a single dash on every row.
ROW_MARKER = "-"

#: A whole depth-1 row prefix, spelled as one literal rather than assembled.
#: `row_prefix` builds the deeper ones by putting indents in front of it, so this
#: is load-bearing and not decoration; the reason it is a literal is that the
#: `file_list` rule in `tests/contracts/shared_readers.txt` recognises a list
#: renderer BY this shape, and a home its own guard cannot see is a guard that
#: passes vacuously (`#210`, and `#474`'s own "guard the guard" test).
FIRST_LEVEL = "  - "


def nested_files(config: dict[str, Any] | None) -> bool:
    """`nested_files`: whether a file list groups its rows under their folder.

    Global rather than `patch_`-prefixed because it governs the `export_db` and
    `search_repo` lists too, and lower case like every other key in
    `config/config.yaml`.
    """
    value = (config or {}).get("nested_files")
    return NESTED_FILES_DEFAULT if value is None else bool(value)


def parent_folder(path: str) -> str | None:
    """The default anchor: the directory ABOVE the file's own, or None.

    Correct wherever a path is not an exported object file, which is every list
    outside `patch -create`'s object sections: an install script, a per-patch
    script, a scaffolded file, an `export_db -groups` label.

    **It answers one level higher than the file's own directory** (ADT #507), so
    that directory becomes a row of its own rather than the tail of a folder
    line. On a per-patch script and an injected template that means
    `patch_scripts/APP309/` with `tables_after/` under it, and
    `config/patch_template/` with `db_end/` under it. `None` means there is
    nothing above to anchor on, and `file_rows` renders whatever directories the
    path itself carries, so a one-directory path comes out unchanged.
    """
    parts = PurePosixPath(path).parts
    return "/".join(parts[:-2]) + "/" if len(parts) > 2 else None


def row_prefix(depth: int) -> str:
    """`  - ` at depth 1, `    - ` at depth 2, and so on."""
    return f"{INDENT * max(depth - 1, 0)}{FIRST_LEVEL}"


def row(text: str, depth: int = 1) -> str:
    """One list row at ``depth``, the only place a marker meets an indent."""
    return f"{row_prefix(depth)}{text}"


def plain_row(text: str, depth: int = 1) -> str:
    """A row at ``depth`` carrying the indent and no marker (ADT #507).

    `row(text, depth)` with the `- ` removed and nothing shifted, which is what
    the nested commit rows under `WARNING - OUTDATED FILES:` needed: Jan,
    2026-08-24, reading a live `-create`, `      - 310) @Jan ...` becomes
    `      310) @Jan ...`. A numbered commit is not one of the files the list is
    about, and the dash in front of it said it was.
    """
    return f"{INDENT * max(depth, 0)}{text}"


def file_rows(
    paths: Sequence[str] | Iterable[str],
    *,
    nested: bool,
    folder_of: Callable[[str], str | None] = parent_folder,
    decorate: Callable[[str, str], str] | None = None,
    children: Callable[[str, int], Sequence[str]] | None = None,
    depth: int = 1,
) -> list[str]:
    """The rows a section prints for ``paths``, flat or grouped by folder.

    Order is the caller's, never sorted: on `patch -create` it is `patch_map`
    install order, and re-sorting it would answer a different question from the
    one the section header asks. A folder is emitted where it FIRST appears and
    collects every later path under it, so one folder is one place to look.

    ``folder_of`` returns the ANCHOR, the deepest folder that stays whole on one
    row; every directory between it and the file gets a row of its own. Answering
    None anchors the path at the root, which for a file carrying no directory at
    all is a plain row at ``depth``.

    ``decorate`` rewrites a row's text from ``(path, leaf)`` once the grouping is
    decided, so `search_repo` can prefix its git status letter without the folder
    rule ever seeing it.

    ``children`` hangs rows off one file, and is called with the depth they will
    be rendered at so the caller can size a truncation against the real prefix.
    That depth is the file's own plus one, whichever shape the list is in, which
    is the whole of what `WARNING - OUTDATED FILES:` needed: its commit rows sat
    four spaces past their file rather than two.
    """
    rows: list[str] = []
    if not nested:
        for path in paths:
            rows.append(row(_text(decorate, path, path), depth))
            rows.extend(_children(children, path, depth + 1))
        return rows
    return _tree_rows(list(paths), folder_of, decorate, children, depth)


def print_file_rows(
    paths: Sequence[str] | Iterable[str],
    *,
    nested: bool,
    folder_of: Callable[[str], str | None] = parent_folder,
    decorate: Callable[[str, str], str] | None = None,
    children: Callable[[str, int], Sequence[str]] | None = None,
    depth: int = 1,
) -> None:
    """`file_rows`, printed. Silent on an empty list, so a header can stand alone."""
    for line in file_rows(
        paths,
        nested    = nested,
        folder_of = folder_of,
        decorate  = decorate,
        children  = children,
        depth     = depth,
    ):
        print(line)


def _tree_rows(
    paths: list[str],
    folder_of: Callable[[str], str | None],
    decorate: Callable[[str, str], str] | None,
    children: Callable[[str, int], Sequence[str]] | None,
    depth: int,
) -> list[str]:
    """The grouped shape: one node per directory, keyed on its accumulated path.

    Keying on the accumulated path rather than on the anchor string is what makes
    the result independent of the order two paths arrive in. `a/b/f1.sql` anchors
    on `a/` and `a/b/c/f2.sql` on `a/b/`, so an anchor-keyed map would print the
    same folder twice at two depths; here a node's parent is the longest folder
    that is a strict prefix of it, whichever chain first produced either.
    """
    chains = [_folder_chain(path, folder_of(path)) for path in paths]
    known = {folder for chain, _leaf in chains for folder in chain}
    root: list[tuple] = []
    entries: dict[str, list[tuple]] = {}

    def node(folder: str) -> list[tuple]:
        if folder not in entries:
            parent = _closest_parent(folder, known)
            siblings = root if parent is None else node(parent)
            entries[folder] = []
            siblings.append(("dir", folder, parent))
        return entries[folder]

    for path, (chain, leaf) in zip(paths, chains, strict=True):
        holder = node(chain[-1]) if chain else root
        holder.append(("file", path, leaf))
    return _emit(root, depth, entries, decorate, children)


def _folder_chain(path: str, base: str | None) -> tuple[list[str], str]:
    """``path`` as its accumulated folder rows, plus the leaf text under them.

    A ``base`` that is not a prefix of ``path`` keeps `#504`'s behaviour: the
    whole path sits on the leaf under that one folder line, rather than this
    module inventing a split it has no basis for.
    """
    if base and not path.startswith(base):
        return [base], path
    rest = path.removeprefix(base) if base else path
    parts = rest.split("/")
    chain = [base] if base else []
    prefix = base or ""
    for part in parts[:-1]:
        prefix = f"{prefix}{part}/"
        chain.append(prefix)
    return chain, parts[-1]


def _closest_parent(folder: str, known: set[str]) -> str | None:
    candidates = [other for other in known if other != folder and folder.startswith(other)]
    return max(candidates, key=len) if candidates else None


def _emit(
    node_entries: list[tuple],
    depth: int,
    entries: dict[str, list[tuple]],
    decorate: Callable[[str, str], str] | None,
    children: Callable[[str, int], Sequence[str]] | None,
) -> list[str]:
    rows: list[str] = []
    for entry in node_entries:
        if entry[0] == "dir":
            _kind, folder, parent = entry
            rows.append(row(folder if parent is None else folder[len(parent):], depth))
            rows.extend(_emit(entries[folder], depth + 1, entries, decorate, children))
            continue
        _kind, path, leaf = entry
        rows.append(row(_text(decorate, path, leaf), depth))
        rows.extend(_children(children, path, depth + 1))
    return rows


def _text(decorate: Callable[[str, str], str] | None, path: str, leaf: str) -> str:
    return leaf if decorate is None else decorate(path, leaf)


def _children(
    children: Callable[[str, int], Sequence[str]] | None,
    path: str,
    depth: int,
) -> Sequence[str]:
    return () if children is None else children(path, depth)


__all__ = [name for name in globals() if not name.startswith("_")]
