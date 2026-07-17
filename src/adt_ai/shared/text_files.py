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
"""

from __future__ import annotations

from collections.abc import Mapping
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


def open_text(path: Path, mode: str = "w") -> IO[str]:
    """Open ``path`` for writing text with the configured line ending."""
    return path.open(mode, encoding="utf-8", newline=_newline)


def write_text(path: Path, content: str) -> None:
    """Write ``content`` to ``path`` with the configured line ending (UTF-8)."""
    with open_text(path) as handle:
        handle.write(content)
