"""The dependencies module's SQL home.

A re-export shim over the topic modules, ``objects`` (the offline SQLite
raw-mirror reads that back the query modes) and ``dictionary_reads`` (the live
Oracle dictionary SELECTs that populate that mirror). The two halves answer to
different databases but are one module's SQL, so they share one home.
``source_reads`` holds both halves of the text mirrors `search TERM` reads
(ADT #895), because one writer owns them.

No SQL lives here: this file only re-exports.
"""

from __future__ import annotations

from adt_ai.dependencies.queries.dictionary_reads import *  # noqa: F401,F403
from adt_ai.dependencies.queries.objects import *  # noqa: F401,F403
from adt_ai.dependencies.queries.source_reads import *  # noqa: F401,F403
