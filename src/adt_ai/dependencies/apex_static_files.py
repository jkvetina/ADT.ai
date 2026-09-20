"""An application's static files, with the text of every file that is text (ADT #895).

Three scopes feed `APEX_STATIC_FILES`: the application's own files, the files of
the plugins it carries (where a `PLUGIN_…` dynamic action's real JavaScript
lives), and its workspace's files. They are read from the dictionary views,
which need no workspace set in the session, rather than `wwv_flow_files`, which
answers nothing until one is (measured on SANDBOX).

**What counts as text** is decided in three steps, because neither the MIME type
nor the name can be trusted alone.

1. A MIME type or extension that says text (`text/*`, JavaScript, JSON, XML,
   `.css`, `.js` and the rest) makes it a candidate, and the bytes then have to
   decode in the file's own charset, UTF-8 when it names none, with no NUL byte.
2. A MIME type or extension that says binary (images, audio, video, fonts,
   archives, PDF) keeps it binary without a look at the bytes.
3. Anything that says neither is sniffed. SANDBOX holds a workspace file named
   `adt_fixture_ws_css`, no extension, served as `application/octet-stream`,
   and it is CSS: code a page loads, so code `search TERM` has to find. It is
   text when its bytes decode strictly in its charset, carry no NUL byte, and
   hold no control character other than tab, newline, carriage return and
   form feed. A real binary fails that within its first few bytes.

Anything else keeps its row, name, MIME type and size, with no text, so
`search` can say it exists and never matches inside it. There is no size cap:
a big or minified file is the reader's call, not the mirror's.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from adt_ai.dependencies import queries
from adt_ai.shared.db import QueryGateway

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

#: A control character a text file does not carry: every C0 and C1 control
#: except tab, newline, form feed and carriage return, plus DEL.
_CONTROL_CHARACTER = re.compile(r"[\x00-\x08\x0b\x0e-\x1f\x7f-\x9f]")


def read_static_files(
    gateway: QueryGateway, app_id: int
) -> tuple[str, list[dict[str, Any]]]:
    """``(workspace, rows)`` for one application's three file scopes.

    The workspace is returned beside the rows because the writer replaces the
    workspace's files by it, and an application with no workspace file still
    names one.
    """
    params = {"app_id": app_id}
    found = gateway.fetch_all(queries.APEX_APPLICATION_WORKSPACE_QUERY, params)
    workspace = str(found[0].get("WORKSPACE") or "") if found else ""
    rows = [
        _file_row("APP", workspace, app_id, str(row["FILE_NAME"]), row)
        for row in gateway.fetch_all(queries.APEX_APP_STATIC_FILES_QUERY, params)
    ]
    rows += [
        _file_row("PLUGIN", workspace, app_id, f"{row['PLUGIN_NAME']}/{row['FILE_NAME']}", row)
        for row in gateway.fetch_all(queries.APEX_PLUGIN_FILES_QUERY, params)
    ]
    if workspace:
        rows += [
            _file_row("WORKSPACE", workspace, 0, str(row["FILE_NAME"]), row)
            for row in gateway.fetch_all(
                queries.APEX_WORKSPACE_STATIC_FILES_QUERY, {"workspace": workspace}
            )
        ]
    return workspace, rows


def _file_row(
    scope: str, workspace: str, app_id: int, file_name: str, row: dict[str, Any]
) -> dict[str, Any]:
    payload = as_bytes(row.get("FILE_CONTENT"))
    mime_type = str(row.get("MIME_TYPE") or "")
    return {
        "SCOPE": scope,
        "WORKSPACE": workspace,
        "APPLICATION_ID": app_id,
        "FILE_NAME": file_name,
        "MIME_TYPE": mime_type or None,
        "BYTES": len(payload),
        "TEXT": decode_text(file_name, mime_type, row.get("FILE_CHARSET"), payload),
    }


def decode_text(file_name: str, mime_type: str, charset: Any, payload: bytes) -> str | None:
    """The file's text, or None when it is not text (the module docstring's steps)."""
    mime = mime_type.lower()
    suffix = PurePosixPath(file_name.lower()).suffix
    says_text = (
        mime.startswith("text/")
        or any(marker in mime for marker in TEXT_MIME_MARKERS)
        or suffix in TEXT_EXTENSIONS
    )
    says_binary = (
        mime.startswith(BINARY_MIME_PREFIXES)
        or mime in BINARY_MIME_TYPES
        or suffix in BINARY_EXTENSIONS
    )
    if (not says_text and says_binary) or b"\x00" in payload:
        return None
    try:
        text = payload.decode(str(charset or "utf-8"))
    except (LookupError, UnicodeDecodeError):
        return None
    if not says_text and _CONTROL_CHARACTER.search(text):
        return None
    return text


def as_bytes(value: Any) -> bytes:
    """A BLOB as bytes, whichever of the gateway's shapes it arrived in.

    python-oracledb hands back a LOB locator to `read()`, SQLcl hands back the
    bytes it decoded from hex, or the raw string when that decode failed.
    """
    if value is None:
        return b""
    if hasattr(value, "read"):
        return as_bytes(value.read())
    if isinstance(value, str):
        return value.encode("utf-8")
    return bytes(value)


__all__ = ["TEXT_EXTENSIONS", "as_bytes", "decode_text", "read_static_files"]
