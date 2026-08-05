"""Text-file writing with a deterministic, configurable line ending.

Python's text mode translates ``"\\n"`` to ``os.linesep`` when ``newline`` is
omitted, so the same export writes LF on macOS/Linux and CRLF on Windows and
one repo exported from two machines diffs on every line (card #138). Every
ADT.ai text write therefore goes through this module or pins an explicit
``newline=`` at the call site — guarded by
``tests/contracts/test_text_write_newline.py``.

The active ending is process-wide state configured once per run from the
``file_crlf`` config key, matching old ADT semantics (``lib/util.py``'s
module-level ``newline``): LF everywhere by default, CRLF everywhere when
enabled. Raw data payloads such as LOB sidecars are the deliberate exception —
they mirror stored database values byte for byte, so their call sites pin
``newline=""`` and never translate.

Pinning ``newline=`` is not enough on its own, because Python only ever
translates ``"\\n"`` on write and never touches a ``"\\r"`` already in the
string. ``newline="\\n"`` therefore passes an existing ``"\\r\\n"`` straight
through, and ``newline="\\r\\n"`` rewrites the ``"\\n"`` inside it into
``"\\r\\r\\n"``. Oracle hands back whatever bytes were compiled — anything ever
loaded from a Windows client carries CRLF in ``ALL_SOURCE`` /
``DBMS_METADATA.GET_DDL`` — so both modes wrote CRLF and ``file_crlf`` read as
ignored (card #193). Everything here therefore *normalizes* first: incoming
CR/CRLF collapse to LF, then the configured ending is applied, so the configured
ending is the only ending on disk. ``open_text`` normalizes inside the returned
handle as well, since callers such as ``yaml.dump()`` write through it and would
otherwise bypass the fix.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import IO, Any

from adt_ai.shared.config import is_enabled

_newline = "\n"


def apply_config(config: Mapping[str, Any]) -> None:
    """Configure the process-wide line ending from the ``file_crlf`` key."""
    configure_crlf(is_enabled(config.get("file_crlf")))


def configure_crlf(enabled: bool) -> None:
    global _newline
    _newline = "\r\n" if enabled else "\n"


def configured_newline() -> str:
    return _newline


def normalize(content: str) -> str:
    """Collapse every CR / CRLF in ``content`` to a lone LF."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


class _NormalizingWriter:
    """Text handle that rewrites every incoming line ending to ``newline``.

    The underlying handle is opened with ``newline=""`` so Python translates
    nothing; this wrapper does the whole job itself and is therefore symmetric —
    CRLF, CR and LF input all produce exactly one configured ending.
    """

    def __init__(self, handle: IO[str], newline: str) -> None:
        self._handle = handle
        self._newline = newline
        self._pending_cr = False

    def write(self, text: str) -> int:
        accepted = len(text)
        if self._pending_cr:
            text = "\r" + text
            self._pending_cr = False
        if text.endswith("\r"):
            # A trailing CR may be the first half of a CRLF split across two
            # writes (yaml.dump() emits many small chunks), so hold it back until
            # the next chunk — or close() — settles what it is.
            self._pending_cr = True
            text = text[:-1]
        if text:
            self._handle.write(normalize(text).replace("\n", self._newline))
        return accepted

    def writelines(self, lines: Iterable[str]) -> None:
        for line in lines:
            self.write(line)

    def _flush_pending(self) -> None:
        if self._pending_cr:
            self._pending_cr = False
            self._handle.write(self._newline)

    def close(self) -> None:
        self._flush_pending()
        self._handle.close()

    def __enter__(self) -> _NormalizingWriter:
        self._handle.__enter__()
        return self

    def __exit__(self, *exc_info: object) -> Any:
        self._flush_pending()
        return self._handle.__exit__(*exc_info)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def open_text(path: Path, mode: str = "w") -> IO[str]:
    """Open ``path`` for writing text, normalized to the configured line ending."""
    handle = path.open(mode, encoding="utf-8", newline="")
    return _NormalizingWriter(handle, _newline)


def write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with the configured line ending (UTF-8)."""
    with open_text(path) as handle:
        handle.write(content)
