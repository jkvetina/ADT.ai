"""The reads `search TERM` makes of the dependency mirror's text tables (ADT #895).

Each template takes an ``IN`` list of `?` placeholders and a ``{match}`` slot.
The slot is `TERM_MATCH` for a term SQLite can fold, and empty otherwise: its
`lower()` folds ASCII only, so a non-ASCII term is decided row by row in Python
instead, over the rows of the scope. `instr` rather than `LIKE`, so a `%` or a
`_` in the term is the character it is and never a wildcard.
"""

from __future__ import annotations

TERM_MATCH = "\n  AND instr(lower(TEXT), ?) > 0"

#: One application's component text, shared components (no page) first.
APEX_SOURCE_TEMPLATE = """
SELECT APPLICATION_ID, PAGE_ID, COMPONENT_TYPE, COMPONENT_ID, COMPONENT_NAME, PROPERTY, TEXT
FROM APEX_COMPONENT_SOURCE
WHERE APPLICATION_ID IN ({apps}){match}
ORDER BY APPLICATION_ID, PAGE_ID, COMPONENT_TYPE, COMPONENT_NAME, COMPONENT_ID, PROPERTY
""".strip()

#: Text static files of the applications, and every workspace file, which
#: belongs to no application and so is never narrowed by one. Workspace files
#: sort last.
STATIC_FILES_TEMPLATE = """
SELECT SCOPE, WORKSPACE, APPLICATION_ID, FILE_NAME, BYTES, TEXT
FROM APEX_STATIC_FILES
WHERE TEXT IS NOT NULL
  AND (SCOPE = 'WORKSPACE' OR APPLICATION_ID IN ({apps})){match}
ORDER BY APPLICATION_ID = 0, APPLICATION_ID, SCOPE, FILE_NAME
""".strip()

__all__ = [
    "APEX_SOURCE_TEMPLATE",
    "STATIC_FILES_TEMPLATE",
    "TERM_MATCH",
]
