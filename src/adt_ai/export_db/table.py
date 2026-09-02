"""Re-export of the shared ADT console table renderer.

The renderer itself moved to `shared/tables.py` under `#670`. It was never an
`export_db` concern: `ut`, `recompile`, `patch` and `dependencies` all draw a
table with it, so five commands imported a sixth command's package to render a
row, which is the layering TECHNICAL_REQUIREMENTS.md §Package Layout puts in
`shared/`.

Nothing but names crosses this file. It stays because `export_db/render.py` and
the export_db tests import the surface from here, and because a moved renderer
should not also be a rename for every existing call site.
"""

from __future__ import annotations

from adt_ai.shared.tables import (
    ADT_TABLE_GUTTER,
    ADT_TABLE_INDENT,
    _adt_cell,
    _AdtTableLayout,
    _cell_text,
    _commit_stdout,
    _compute_adt_layout,
    adt_table_line_width,
    close_adt_table,
    open_adt_table,
    print_adt_table,
)

__all__ = [
    "ADT_TABLE_GUTTER",
    "ADT_TABLE_INDENT",
    "_AdtTableLayout",
    "_adt_cell",
    "_cell_text",
    "_commit_stdout",
    "_compute_adt_layout",
    "adt_table_line_width",
    "close_adt_table",
    "open_adt_table",
    "print_adt_table",
]
