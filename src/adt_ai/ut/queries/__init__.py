"""The ut module's SQL home.

A re-export shim over the topic modules, ``suites`` holds the discovery reads
(``_UT`` packages from the dictionary, suite/test items from utPLSQL's own
annotation cache), the annotation-cache rebuild, and the ``ut.run`` call that
executes a suite; ``coverage`` holds the utPLSQL coverage session and the
per-package block-coverage read; ``store`` holds the SQLite run history behind
`config/internal/ut.db`, the one set of statements here that is not Oracle.
Callers import everything from ``adt_ai.ut.queries``.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.ut.queries.coverage import *  # noqa: F401,F403
from adt_ai.ut.queries.store import *  # noqa: F401,F403
from adt_ai.ut.queries.suites import *  # noqa: F401,F403
