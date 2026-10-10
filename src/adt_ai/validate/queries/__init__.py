"""The validate module's SQL home.

A re-export shim over the topic modules: ``codescan_store`` holds the SQLite
statements behind `config/internal/codescan.db`, the baseline `validate
-codescan` compares each tree against, and ``static_id_tab`` the dictionary read
`validate -scan` names Tab-bearing Static IDs with. Callers import everything
from ``adt_ai.validate.queries``.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.validate.queries.codescan_store import *  # noqa: F401,F403
from adt_ai.validate.queries.static_id_tab import *  # noqa: F401,F403
