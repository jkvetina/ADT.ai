"""DB: the `search TERM` layer read from the files `export_db` wrote (ADT #904).

The layer read a line-for-line copy of `USER_SOURCE` that `rebuild` kept in the
dependency mirror until Jan, 2026-09-19: *"You will always have the export_db,
thats a core thing. You dont have to duplicate this shit! REMOVE IT"*. Every
project exports its schemas anyway, and the files hold more than the copy did:
a table, a view or a sequence has no `USER_SOURCE` lines at all.

The files are found by `export_db`'s own resolver rather than a second reading
of the layout, so a type folder two types share answers by the longest
extension and a `-groups` sub-folder is read where the export put it. A schema
with no exported file is named under `WARNING - NOT SEARCHED`, never answered with no
hits, and so is a file that could not be read (`#958`); nothing here connects to
Oracle.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

from adt_ai.export_db.files import ObjectFileResolver
from adt_ai.search.term_model import Hit, TermRequest, TermResult, line_hits, line_matches
from adt_ai.shared.object_files import object_name_from_file


def search_db_layer(request: TermRequest, result: TermResult) -> None:
    """DB: every exported object file of the schemas the request names."""
    if "DB" not in request.layers:
        return
    if not request.owners:
        result.not_searched.append(("DB", "no schema named or configured, name one with -schema"))
        return
    resolver = ObjectFileResolver.from_config(request.root, dict(request.config))
    files: dict[Path, tuple[str, str]] = {}
    for owner in dict.fromkeys(owner.upper() for owner in request.owners):
        exported = resolver.object_files([owner])
        if not exported:
            # The fact alone: under ADT #906 `search` never hands back a
            # command to run, and nothing here exports what is missing.
            result.not_searched.append(("DB", f"SCHEMA {owner} has no exported files"))
            continue
        # A `path_objects` with no `<schema>` puts every schema in one tree, so
        # a file is read once, for the first schema that reached it.
        for object_type, path in exported:
            files.setdefault(path, (owner, object_type))
    owners = {owner for owner, _type in files.values()}
    if not owners:
        return
    found: list[Hit] = []
    read = 0
    for path, (owner, object_type) in files.items():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as error:
            # One file a permission, a lock or a concurrent export took away
            # used to end the whole search, losing the hits already found and
            # every layer after this one (`#958`). It is named the way
            # `search -data` names an object the database refused.
            result.not_searched.append(("DB", f"{_shown(path, request.root)}: {_why(error)}"))
            continue
        read += 1
        found.extend(_file_hits(resolver, path, owner, object_type, text, request.term))
    if not read:
        return
    result.searched.add("DB")
    hits = sorted(found, key=_hit_order)
    if len(owners) > 1:
        hits = [replace(hit, component=f"{hit.db_object[0]}.{hit.component}")
                for hit in hits if hit.db_object is not None]
    result.hits.extend(hits)


def _file_hits(
    resolver: ObjectFileResolver,
    path: Path,
    owner: str,
    object_type: str,
    text: str,
    term: str,
) -> Iterator[Hit]:
    if not line_matches(text, term):
        return
    name = object_name_from_file(path, resolver.object_types[object_type].extension)
    for line, flag, excerpt in line_hits(text, term):
        yield Hit(
            source    = "DB",
            component = name,
            prop      = object_type,
            line      = line,
            flag      = flag,
            excerpt   = excerpt,
            db_object = (owner, object_type, name),
        )


def _shown(path: Path, root: Path) -> str:
    """The file as the project names it, so the row fits beside the others.

    The resolver builds every path as `root / <relative>` (`under_root` hands
    back the path it was given), so it is always relative to the root.
    """
    return path.relative_to(root).as_posix()


def _why(error: OSError) -> str:
    """`Permission denied`, not `[Errno 13] ...: '<the absolute path again>'`."""
    return error.strerror or type(error).__name__


def _hit_order(hit: Hit) -> tuple[str, str, str, int]:
    """Owner, object, type, line: a spec reads before its body, as it compiles."""
    owner, object_type, name = hit.db_object or ("", "", "")
    return owner, name, object_type, hit.line or 0


__all__ = ["search_db_layer"]
