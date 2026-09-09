"""Loading a user's own normalizer from a file on disk.

Split out of `normalizers.py` when `#740` pushed that module past the 20 KB
context cap `tests/export_db/test_normalizer_structure.py` pins, the same move
`#679` made for `normalizer_context.py`. It sits beside that file and imports
nothing from `normalizers`, so the names stay importable from there unchanged.

A plugin declares either a `NORMALIZERS` mapping of object type to callable, or a
single `OBJECT_TYPE` plus a `normalize` function. Anything else is refused by
name, because a plugin that loaded and silently normalized nothing would be
indistinguishable from one ADT never found.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from adt_ai.export_db.normalizer_context import Normalizer


class NormalizerError(Exception):
    """Raised when a user normalizer plugin cannot be loaded."""


def load_plugin(plugin_path: Path) -> dict[str, Normalizer]:
    module = import_plugin(plugin_path)

    mapping = getattr(module, "NORMALIZERS", None)
    if isinstance(mapping, dict):
        return {
            str(object_type).upper(): normalizer
            for object_type, normalizer in mapping.items()
            if callable(normalizer)
        }

    object_type = getattr(module, "OBJECT_TYPE", None)
    normalizer = getattr(module, "normalize", None)
    if isinstance(object_type, str) and callable(normalizer):
        return {object_type.upper(): normalizer}

    raise NormalizerError(
        f"Plugin does not expose NORMALIZERS or OBJECT_TYPE + normalize: {plugin_path}"
    )


def import_plugin(plugin_path: Path) -> ModuleType:
    path = Path(plugin_path)
    spec = importlib.util.spec_from_file_location(f"adt_ai_normalizer_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise NormalizerError(f"Cannot load normalizer plugin: {plugin_path}")

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
