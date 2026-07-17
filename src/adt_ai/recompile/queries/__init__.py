"""The recompile module's SQL home.

A re-export shim over the topic modules — ``objects`` (overview, compile, and
error SQL), ``reports`` (materialized views, synonyms, disabled objects, jobs),
and ``trailing`` (the -trailing whitespace pass). Callers import everything from
``adt_ai.recompile.queries`` and cannot tell which topic a name comes from, which
is what lets the split change without touching a single import site.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.recompile.queries.objects import *  # noqa: F401,F403
from adt_ai.recompile.queries.reports import *  # noqa: F401,F403
from adt_ai.recompile.queries.trailing import *  # noqa: F401,F403
from adt_ai.shared.object_types import PLSQL_OBJECT_TYPES as PLSQL_OBJECT_TYPES
