"""The ut3 module's SQL home.

A re-export shim over the topic modules — ``suites`` holds the discovery reads
(``_UT`` packages from the dictionary, suite/test items from utPLSQL's own
annotation cache), the annotation-cache rebuild, and the ``ut.run`` call that
executes a suite. Callers import everything from ``adt_ai.ut3.queries``.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.ut3.queries.suites import *  # noqa: F401,F403
