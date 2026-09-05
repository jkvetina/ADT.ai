"""The patch module's SQL home.

A re-export shim over the topic modules. No SQL lives here.
"""

from __future__ import annotations

from adt_ai.patch.queries.hardening import *  # noqa: F401,F403
from adt_ai.patch.queries.objects import *  # noqa: F401,F403
