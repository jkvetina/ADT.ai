"""Text-file writing with a deterministic, configurable line ending.

Python's text mode translates ``"\\n"`` to ``os.linesep`` when ``newline`` is
omitted, so the same export writes LF on macOS/Linux and CRLF on Windows and
one repo exported from two machines diffs on every line (card #138). Every
ADT.ai text write therefore goes through this module or pins an explicit
``newline=`` at the call site, guarded by
``tests/contracts/test_text_write_newline.py``.

The active ending is process-wide state configured once per run from the
``file_crlf`` config key, matching old ADT semantics (``lib/util.py``'s
module-level ``newline``): LF everywhere by default, CRLF everywhere when
enabled. Raw data payloads such as LOB sidecars are the deliberate exception,
they mirror stored database values byte for byte, so their call sites pin
``newline=""`` and never translate.

Pinning ``newline=`` is not enough on its own, because Python only ever
translates ``"\\n"`` on write and never touches a ``"\\r"`` already in the
string. ``newline="\\n"`` therefore passes an existing ``"\\r\\n"`` straight
through, and ``newline="\\r\\n"`` rewrites the ``"\\n"`` inside it into
``"\\r\\r\\n"``. Oracle hands back whatever bytes were compiled, anything ever
loaded from a Windows client carries CRLF in ``ALL_SOURCE`` /
``DBMS_METADATA.GET_DDL``, so both modes wrote CRLF and ``file_crlf`` read as
ignored (card #193). Everything here therefore *normalizes* first: incoming
CR/CRLF collapse to LF, then the configured ending is applied, so the configured
ending is the only ending on disk. ``open_text`` normalizes inside the returned
handle as well, since callers such as ``yaml.dump()`` write through it and would
otherwise bypass the fix.

**A byte-identical rewrite is not a write** (card #593). Both writers below
compare the bytes they are about to lay down against the bytes already there
and do nothing when they agree, so an export whose output has not changed
leaves every one of its files at the mtime it already had. That matters outside
this process: an export tree under a syncing folder re-uploads whatever it
touched, and a replace-by-rename books as a delete plus an add, so one run
against an unchanged schema cost 17,649 re-uploads (measured 2026-08-28). The
comparison is on CONTENT, never on a timestamp or a database ``last_ddl_time``,
because a stamp says when the object was touched and this asks whether the file
would differ.

**The skip is silent, and that is the requirement** (card #594). #593 also
counted the writes and printed `FILES: n written, m unchanged` above every run's
timer; Jan, 2026-08-29: *"I did not asked for this, dont clutter the console
output without my approval!"*. The counters were state kept for that one reader,
so they went with it and nothing here reports anything now. The per-file answer
is what a caller actually needs and both writers still return it: they say
whether they wrote, which is how ``export_db`` tells `update` from `unchanged`
on its own object rows.
"""

from __future__ import annotations

import contextlib
import os
import secrets
import stat
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import TracebackType
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
    nothing; this wrapper does the whole job itself and is therefore symmetric,
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
            # the next chunk (or close()) settles what it is.
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

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._flush_pending()
        self._handle.__exit__(exc_type, exc_value, traceback)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._handle, name)


def open_text(path: Path, mode: str = "w") -> _NormalizingWriter:
    """Open ``path`` for writing text, normalized to the configured line ending.

    The streaming handle, for a caller that hands its output to somebody else's
    writer (``yaml.dump``) or appends to a log. It writes unconditionally: the
    unchanged-skip below needs the whole payload in hand to compare, and a
    handle by definition does not have it. A caller that produces an artifact
    an export rewrites every run builds its text and calls :func:`write_text`
    or :func:`write_bytes` instead.
    """
    handle = path.open(mode, encoding="utf-8", newline="")
    return _NormalizingWriter(handle, _newline)


def rendered_bytes(content: str) -> bytes:
    """Exactly the bytes :func:`write_text` puts on disk for ``content``.

    One renderer, so a comparison, a hash and a write cannot disagree about
    what "the file's content" is (`export_db`'s `-baseline` hashing reads it
    for the same reason, card #452).
    """
    return normalize(content).replace("\n", _newline).encode("utf-8")


def bytes_match(path: Path, payload: bytes) -> bool:
    """Does ``path`` already hold exactly these bytes?

    The size is asked first because it settles most of the negatives without
    reading anything, and a file that does not exist, or that is not a file at
    all, simply does not match: this is consulted on the way into a write, so
    it answers rather than raises.
    """
    try:
        if path.stat().st_size != len(payload):
            return False
        return path.read_bytes() == payload
    except OSError:
        return False


def text_matches(path: Path, content: str) -> bool:
    """Would writing ``content`` to ``path`` change the file?  (Inverted.)"""
    return bytes_match(path, rendered_bytes(content))


def write_bytes(path: Path, payload: bytes) -> bool:
    """Write ``payload`` to ``path`` unless the file already holds those bytes.

    Returns whether anything was written, which is what makes the skip
    observable to a caller that reports its own per-object action. That return
    is the whole reporting surface: nothing is accumulated here and no run says
    anything about the split on the console (card #594).

    A changed artifact is assembled and flushed beside its destination, then
    atomically replaces it. A killed export can therefore leave either the old
    complete file or the new complete file, never a tracked file truncated at
    the byte the process reached. Same-directory placement is essential: only
    then does ``os.replace`` keep its atomic guarantee across mount points.
    """
    return _write_bytes(path, payload, private=False)


def write_private_text(path: Path, content: str) -> bool:
    """Atomically write owner-only UTF-8 text with configured line endings.

    Credential-bearing files use this instead of the general artifact writer.
    The destination is forced to ``0600`` even when its bytes are unchanged,
    and the temporary starts private, so there is no interval in which a new
    secret file follows a permissive process umask.
    """
    return _write_bytes(path, rendered_bytes(content), private=True)


def _write_bytes(path: Path, payload: bytes, *, private: bool) -> bool:
    if bytes_match(path, payload):
        if private:
            os.chmod(path, 0o600)
        return False
    temporary, descriptor = _open_atomic_temporary(path, mode=0o600 if private else 0o666)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if not private:
            _preserve_mode(path, temporary)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)
    return True


def write_text(path: Path, content: str) -> bool:
    """Write ``content`` to ``path`` with the configured line ending (UTF-8)."""
    return write_bytes(path, rendered_bytes(content))


def _open_atomic_temporary(path: Path, *, mode: int = 0o666) -> tuple[Path, int]:
    """Create a unique temporary with the requested pre-umask permissions."""
    for _attempt in range(100):
        temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                mode,
            )
        except FileExistsError:  # practically impossible; still fail safely
            continue
        return temporary, descriptor
    raise FileExistsError(f"could not allocate a temporary file beside {path}")


def _preserve_mode(path: Path, temporary: Path) -> None:
    """Keep an existing artifact's permission bits across replacement."""
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError:
        return
    os.chmod(temporary, mode)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename where the platform supports syncing directories."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    with contextlib.suppress(OSError):
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
