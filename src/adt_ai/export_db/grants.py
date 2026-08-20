"""The GRANT artifacts an export writes.

Split out of `runner.py` at the 20 KB per-file context budget, along a seam the
module already had: everything else in that file exports an object the schema
owns, while these four artifacts are privilege reports rendered from dictionary
views and keyed on the schema itself.

`#360` added an `EXPORTING GRANTS FOR <SCHEMA>:` section here and `#372` removed
it as furniture nobody asked for. The split outlives it because the budget is
its real reason: folded back, `runner.py` measures 20.4 KB. These four reads run
under the export's own header, like the object pulls above them.
"""

from __future__ import annotations

from collections.abc import Iterable

from adt_ai.export_db.config import _requested_object_type_matches
from adt_ai.export_db.content import (
    _render_directories,
    _render_grants_made,
    _render_grants_received,
    _render_user_privileges,
)
from adt_ai.export_db.files import ObjectFileWriter, ObjectWriteRequest
from adt_ai.export_db.inventory import DatabaseObject, ObjectDiscovery

# The object type all four artifacts already carry. Named once so the yield
# below, the overview row and the compact label cannot drift onto three
# spellings. Jan wrote `GRANTS` when he asked for it on screen (`#382`); the
# type stays singular because it is also the folder name under `path_objects`,
# so renaming it would move every project's exported files.
GRANT_OBJECT_TYPE = "GRANT"


def exports_grants(request) -> bool:
    """Will this request write GRANT artifacts at all?

    The two guards `grant_contents` opens with, lifted so the overview row and
    the compact label can ask before the reads run. A shared predicate rather
    than a copied pair of conditions: a console naming a type the export then
    skips is exactly the drift this prevents (`#382`).

    It imports the type predicate itself, where `grant_contents` takes one as an
    argument. That asymmetry is deliberate and is the seam's, not an oversight:
    `grant_contents` is called with `runner`'s own filtering vocabulary and
    importing it back there would turn the split into a cycle, while this reads
    `config`, which imports nothing from here.
    """
    if GRANT_OBJECT_TYPE not in request.config.get("object_types", {}):
        return False
    return bool(_requested_object_type_matches(GRANT_OBJECT_TYPE, request.object_types))


def grant_artifacts(
    request,
    schema,
    discovery: ObjectDiscovery,
    split_patterns,
    writer: ObjectFileWriter,
) -> tuple[list[tuple[DatabaseObject, str]], bool]:
    """This schema's GRANT artifacts, and whether any of them actually moved.

    The four reads and the comparison are one step because the console asks one
    question. `#382` put a `GRANT` row in the overview to say the type was
    coming, and put it there with the rest of the table, so it was a claim about
    what the config selects rather than about anything the export would do: a
    schema where nothing had changed still printed a table holding that one row.
    Jan, 2026-08-20: *"When you are not detecting any grant changes, dont print
    it. You will print table header and only AFTER you fetch grants and evaluate
    changes, you will print the line"*.

    So the runner calls this under the overview table it deliberately left open,
    which is also what announces the wait, the job `#382` had given the row
    itself and the compact bar's `GRANTS` label. A row that opens before the
    read that decides it is the shape SOP §Console output contract bans outright.

    **Comparing is not deciding what to write.** `run()` writes every artifact
    whatever this answers, so a file edited by hand is still restored on the next
    export; the answer reaches the screen and nowhere else.

    Content is the only signal available. None of these four is a `USER_OBJECTS`
    row, so there is no `LAST_DDL_TIME` for a `-recent` window to narrow them by,
    which is exactly why the answer is worth computing: a windowed run cannot
    learn from the dictionary whether a grant moved, and this can.
    """
    artifacts = list(grant_contents(request, schema, discovery, split_patterns))
    changed = any(
        writer.differs_from_disk(ObjectWriteRequest(grant_object, content))
        for grant_object, content in artifacts
    )
    return artifacts, changed


def grant_contents(
    request,
    schema,
    discovery: ObjectDiscovery,
    split_patterns,
) -> Iterable[tuple[DatabaseObject, str]]:
    """Yield one schema's GRANT artifacts, read on the caller's own discovery.

    Per schema rather than per request, because the runner calls this from
    inside its own schema loop: the reads have to land while that schema's
    export section is still open, and the discovery is the one it is already
    pulling objects through.

    `split_patterns` arrives as an argument rather than being imported: it is
    `runner`'s own filtering vocabulary, and importing it back would make the
    split a cycle instead of a seam. The type predicate went the other way with
    `exports_grants`, which reads `config` itself so the overview and the compact
    label can ask the same question before these reads run (`#382`).
    """
    if not exports_grants(request):
        return
    schema_export = (request.schema_export or {}).get(schema, {})
    prefix = request.prefix or schema_export.get("prefix")
    ignore = request.ignore or split_patterns(schema_export.get("ignore"))
    yield DatabaseObject(schema, GRANT_OBJECT_TYPE, schema), _render_grants_made(
        discovery.grants_made(schema, prefix=prefix, ignore=ignore),
        prefix = prefix,
        ignore = ignore,
    )
    for owner, content in _render_grants_received(
        discovery.grants_received(schema),
        schema = schema,
    ).items():
        yield DatabaseObject(schema, GRANT_OBJECT_TYPE, f"received/{owner.upper()}"), content
    yield (
        DatabaseObject(schema, GRANT_OBJECT_TYPE, f"{schema.upper()}_schema"),
        _render_user_privileges(discovery.user_privileges(schema), schema=schema),
    )
    yield (
        DatabaseObject(schema, GRANT_OBJECT_TYPE, f"{schema.upper()}_directories"),
        _render_directories(discovery.directories(schema), schema=schema),
    )
