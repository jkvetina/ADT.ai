"""The optional CSS and JS minifiers, and how a run finds out it has none.

`rcssmin` and `rjsmin` are what old ADT minified with, and neither is an ADT.ai
dependency: only the `live_upload` watch reaches them, and a package every user
installs for one optional step of one command is weight most of them never use.
So they are looked up at runtime, the command uploads with or without them, and
the CLI says so once when they are missing.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from types import ModuleType

Importer = Callable[[str], ModuleType]

CSS_PACKAGE = "rcssmin"
JS_PACKAGE = "rjsmin"

# What to type when the row above says they are missing.
INSTALL_COMMAND = f"pip install {CSS_PACKAGE} {JS_PACKAGE}"


@dataclass(frozen=True)
class Minifiers:
    css: Callable[[str], str]
    js: Callable[[str], str]

    def for_suffix(self, suffix: str) -> Callable[[str], str] | None:
        """The minifier for this suffix, or None when nothing minifies it.

        Old ADT sent JavaScript through the CSS minifier here and left its own
        CSS branch unreachable, so `.css` was never minified and `.js` was
        minified by the wrong tool. The pairing is the fix; it is the only
        deliberate departure from that file (docs/live_upload.md).
        """
        return {".css": self.css, ".js": self.js}.get(suffix)


def load_minifiers(importer: Importer = importlib.import_module) -> Minifiers | None:
    """Both minifiers, or None when either package is not installed.

    `keep_bang_comments` is old ADT's own setting: a `/*! ... */` header is a
    licence banner, and a minifier that strips it makes the shipped file say
    nothing about what it is.
    """
    try:
        css = importer(CSS_PACKAGE)
        js = importer(JS_PACKAGE)
    except ImportError:
        return None
    return Minifiers(
        css = partial(css.cssmin, keep_bang_comments=True),
        js  = partial(js.jsmin, keep_bang_comments=True),
    )
