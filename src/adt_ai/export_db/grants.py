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
