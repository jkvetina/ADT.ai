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
