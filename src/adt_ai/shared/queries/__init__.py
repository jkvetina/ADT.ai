"""The shared module's SQL home.

A re-export shim over the ``clock`` and ``versions`` topic modules. No SQL
lives here.
"""

from __future__ import annotations

from adt_ai.shared.queries.clock import *  # noqa: F401,F403
from adt_ai.shared.queries.versions import *  # noqa: F401,F403
