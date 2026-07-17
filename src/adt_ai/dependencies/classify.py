"""Split a ``"TYPE.NAME"`` dependency node into its parts.

Internal vs external is decided at query time from owners present in the tracked
dependency mirror (see :mod:`adt_ai.dependencies.store`), so no name-prefix
heuristic lives here anymore — only the node parser that the query modes still
need to turn ``TYPE.NAME`` input into its components.
"""

from __future__ import annotations


def split_node(node: str) -> tuple[str, str]:
    """Split a ``"TYPE.NAME"`` node into ``(type, name)``.

    Only the first dot separates the type from the name, because the type itself
    may contain a space (``"PACKAGE BODY"``). A node with no dot is treated as a
    bare name.
    """
    type_part, _, name_part = node.partition(".")
    if not name_part:
        return "", type_part
    return type_part, name_part


def split_object_row(node: str) -> dict[str, str]:
    """Render a ``"TYPE.NAME"`` node as ``OBJECT_TYPE`` / ``OBJECT_NAME`` columns.

    Used by the default ``dependencies`` table output so the dotted node is split
    into two columns, with multiword types like ``"PACKAGE BODY"`` kept whole.
    """
    object_type, object_name = split_node(node)
    return {"OBJECT_TYPE": object_type, "OBJECT_NAME": object_name}
