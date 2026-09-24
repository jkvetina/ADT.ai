"""Guessing a file's MIME type, deterministically across platforms (#670).

`mimetypes.guess_type` is not the same answer on every OS for a couple of
extensions this repo ships constantly: `.js` reads back `text/javascript` on
some platforms' system mimetypes database and `application/javascript` on
others, and `.css` has the same shape of disagreement. A bare
`mimetypes.guess_type` call therefore makes a test -- or a deployed APEX
static file's `p_mime_type` -- depend on which machine ran it. `guess_mime_type`
normalises the handful of extensions known to vary and falls through to the
stdlib guess, then to a caller-supplied default, for everything else.

`live_upload/files.py` (an uploaded file's `p_mime_type`) and
`patch/snapshots.py` (an APEX static file shipped in a patch) both call this
rather than `mimetypes` directly, so the two never drift onto two different
guesses for the same extension again.

**Text or binary** is answered here too, once, for every reader that has to
know. `dependencies/apex_static_files.py` decides it for a file read out of
APEX (ADT #895) and `patch/snapshots.py` for a file copied into a patch
(ADT #939), from the same name and MIME families below.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

# Extensions whose stdlib guess is not one fixed string across macOS, Linux
# and Windows. Keyed on the lowercase suffix (including the dot); the value is
# the ONE type this repo reports for it, regardless of platform.
_MIME_TYPE_OVERRIDES = {
    ".css": "text/css",
    ".js": "text/javascript",
}

#: The extensions that make a file text whatever its MIME type says.
TEXT_EXTENSIONS = frozenset(
    {".js", ".mjs", ".css", ".json", ".html", ".htm", ".xml", ".txt", ".md", ".sql", ".svg", ".map"}
)

#: MIME subtypes that are text outside `text/*`: `application/javascript`,
#: `application/json`, `image/svg+xml` and the rest of their family.
TEXT_MIME_MARKERS = ("javascript", "json", "xml")

#: MIME families and types that are binary however their bytes happen to read.
BINARY_MIME_PREFIXES = ("image/", "audio/", "video/", "font/")
BINARY_MIME_TYPES = frozenset({"application/zip", "application/pdf"})

#: Extensions that make a file binary, `application/octet-stream` or not.
BINARY_EXTENSIONS = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tif", ".tiff",
        ".woff", ".woff2", ".ttf", ".otf", ".eot", ".zip", ".gz", ".jar", ".pdf",
        ".mp3", ".mp4", ".wav", ".ogg", ".webm",
    }
)


def guess_mime_type(name: str, *, default: str) -> str:
    """The MIME type for a file named `name`.

    Checks the override table first, then `mimetypes.guess_type`, then falls
    back to `default` when neither recognises the extension.
    """
    suffix = Path(name).suffix.lower()
    override = _MIME_TYPE_OVERRIDES.get(suffix)
    if override is not None:
        return override
    return mimetypes.guess_type(name)[0] or default


def says_text(name: str, mime_type: str) -> bool:
    """The name or the MIME type calls the file text (`.css`, `text/*`, JSON, SVG)."""
    mime = mime_type.lower()
    return (
        mime.startswith("text/")
        or any(marker in mime for marker in TEXT_MIME_MARKERS)
        or Path(name).suffix.lower() in TEXT_EXTENSIONS
    )


def says_binary(name: str, mime_type: str) -> bool:
    """The name or the MIME type calls the file binary (image, font, archive, PDF)."""
    mime = mime_type.lower()
    return (
        mime.startswith(BINARY_MIME_PREFIXES)
        or mime in BINARY_MIME_TYPES
        or Path(name).suffix.lower() in BINARY_EXTENSIONS
    )


def is_binary(name: str, payload: bytes) -> bool:
    """``payload`` is not text, so no encoding applies to it (ADT #939).

    A NUL byte settles it, since that is git's own test and no text encoding a
    project would declare writes one. Otherwise the name decides, the way it
    does for a static file read out of APEX: binary when it says binary and
    nothing says text, so an SVG, `image/svg+xml`, stays text. What is left
    unnamed and NUL-free is text, including one that is not valid UTF-8: that
    is the file `WARNING - NOT UTF-8:` exists to name (ADT #932).
    """
    if b"\x00" in payload:
        return True
    mime = guess_mime_type(name, default="")
    return says_binary(name, mime) and not says_text(name, mime)
