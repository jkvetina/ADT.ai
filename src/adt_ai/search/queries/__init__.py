"""The search module's SQL home.

A re-export shim over the ``term`` topic module, the reads `search TERM` makes
of the dependency mirror's text tables (ADT #895). No SQL lives here.
"""

from __future__ import annotations

from adt_ai.search.queries.term import *  # noqa: F401,F403
