"""The search module's SQL home.

A re-export shim over the topic modules: ``term``, the reads `search TERM` makes
of the dependency mirror's text tables (ADT #895), and ``data``, the dictionary
reads of `search TERM -data` (ADT #920). No SQL lives here.
"""

from __future__ import annotations

from adt_ai.search.queries.data import *  # noqa: F401,F403
from adt_ai.search.queries.term import *  # noqa: F401,F403
