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
