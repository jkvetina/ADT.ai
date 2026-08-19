"""The grid the ut content sections share.

Three constants and no behaviour, in a module of their own because two different
renderers lay text on this grid, `render.py` (`TEST RESULTS:`, under `-verbose`)
and `problems.py` (`ERRORS & FAILURES:`), and one importing the other would make
the constants unreachable from the third without a cycle. A third renderer,
`dense.py`, was the original reason for the split; `#317` deleted it along with
`-dense`, and the module stays because the alternative is a second copy of `"  "`
per module, which is exactly the drift the comment below exists to prevent.
"""

from __future__ import annotations

# One grid for both content sections. `TEST RESULTS:` and `ERRORS & FAILURES:`
# have the same shape (a heading naming what follows, then its detail) so a
# package heading sits where a stanza heading sits and a test row sits where a
# wrapped message line sits. Four spaces against two: the detail reads as
# nested under its heading without starting a third of a tab-stop in.
HEADING_INDENT = "  "
DETAIL_INDENT = "    "

# The message body wraps inside the terminal; only an unbreakable token (an
# ORA stack line, a path) is allowed to overhang.
MESSAGE_WIDTH = 78

__all__ = [name for name in globals() if not name.startswith("__")]
