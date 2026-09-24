"""Writing and hashing an object's file (split out of `files.py`, `#923`).

`files.py` answers where an object's file is; this module owns the bytes that land
there. The seam is `ObjectFileResolver.checked_path`: every method below asks the
resolver for a path it has already refused outside the project, so a mapping
error is always the resolver's to raise and this module needs nothing from
`files.py` at runtime. `files.py` re-exports all three names, so every import of
them from there keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from adt_ai.export_db.config import DEFAULT_EMPTY_LINES
from adt_ai.export_db.content import close_with_empty_lines
from adt_ai.export_db.inventory import DatabaseObject
from adt_ai.shared import text_files
from adt_ai.shared.git_files import file_payload_hash

if TYPE_CHECKING:
    from adt_ai.export_db.files import ObjectFileResolver


@dataclass(frozen=True)
class ObjectWriteRequest:
    object  : DatabaseObject
    content : str
    path    : Path | None = None


@dataclass(frozen=True)
class ObjectWritePlan:
    object  : DatabaseObject
    path    : Path
    action  : Literal["create", "update", "unchanged", "hashed"]
    #: Set only by :meth:`ObjectFileWriter.hash_one` (`#452`): the hash of the
    #: bytes this object WOULD have been written as, so a baseline can be
    #: measured off a live database without touching the working tree.
    content_hash: str | None = None
    #: The text that hash was taken over, also set only by `hash_one`, so a
    #: measured baseline can store a table exactly as an export writes it
    #: (ADT #857) without rendering it a second time.
    rendered: str | None = None


class ObjectFileWriter:
    """Writes an object's file, and owns the one decision about how it ends.

    `empty_lines` is the `file_empty_lines` config key (`#687`). It is applied
    HERE rather than in the content pipeline because three methods have to agree
    on the same bytes (the write, the `-baseline` hash, and the
    `differs_from_disk` comparison the `GRANT` overview row reads), and because
    every file this class writes then closes the same way, object file, table
    `.fix` sidecar and grants file alike.
    """

    def __init__(
        self,
        resolver: ObjectFileResolver,
        empty_lines: int = DEFAULT_EMPTY_LINES,
    ) -> None:
        self.resolver = resolver
        self.empty_lines = empty_lines

    def _closed(self, content: str) -> str:
        return close_with_empty_lines(content, self.empty_lines)

    def write(self, requests: list[ObjectWriteRequest]) -> list[ObjectWritePlan]:
        return [self.write_one(request) for request in requests]

    def differs_from_disk(self, request: ObjectWriteRequest) -> bool:
        """Is the file this request targets absent, or holding other content?

        The same comparison :meth:`write_one` makes, asked without writing
        anything. `export_db` asks it before printing the `GRANT` overview row,
        which exists to say those artifacts moved and must not claim a run that
        rewrites the same bytes (`#437`).
        """
        path = self.resolver.checked_path(request.object, request.path)
        return not text_files.text_matches(path, self._closed(request.content))

    def write_one(self, request: ObjectWriteRequest) -> ObjectWritePlan:
        """Write the object's file, unless the file already holds these bytes.

        The skip is the shared writer's (`#593`); this reads its answer back to
        name the action. `export_db` used to build its writer with
        ``compare_existing=False``, trading a rewrite of every touched file for
        one skipped read per object, which under a syncing folder re-uploaded
        the whole export after a run that changed nothing.
        """
        path = self.resolver.checked_path(request.object, request.path)
        existed = path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        written = text_files.write_text(path, self._closed(request.content))
        action: Literal["create", "update", "unchanged"] = (
            ("update" if existed else "create") if written else "unchanged"
        )
        return ObjectWritePlan(object=request.object, path=path, action=action)

    def hash_one(self, request: ObjectWriteRequest) -> ObjectWritePlan:
        """What this object would hash to, without writing anything (`#452`).

        The path is resolved exactly as a write would resolve it, so a measured
        baseline is keyed the same way the working tree is and the two are
        directly comparable.

        The bytes are the bytes `write_text` would have produced, which is why
        the configured line ending is applied here rather than hashing the raw
        DDL string: `file_payload_hash` canonicalizes line endings (`#454`), so
        this would agree either way, and pinning it to the writer's own output
        keeps that agreement a property of the code rather than a coincidence.

        Jan, 2026-08-21: *"when patch calculate the hash of the file, it must be
        the same as the hash calculated in export_db -baseline mode."*
        """
        path = self.resolver.checked_path(request.object, request.path)
        rendered = self._closed(request.content)
        return ObjectWritePlan(
            object       = request.object,
            path         = path,
            action       = "hashed",
            content_hash = file_payload_hash(text_files.rendered_bytes(rendered)),
            rendered     = rendered,
        )
