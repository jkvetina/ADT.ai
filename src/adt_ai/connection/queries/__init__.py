"""The connection module's SQL home.

A re-export shim over the topic modules, today only ``probe`` (what
``connection -test`` reads once connected). Callers import everything from
``adt_ai.connection.queries``.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.connection.queries.probe import *  # noqa: F401,F403
