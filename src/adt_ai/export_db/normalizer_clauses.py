"""Clause-level rewrites shared by the object normalizers.

They live beside `normalizers.py` rather than inside it because that file is held
under the 20 KB context cap `tests/export_db/test_normalizer_structure.py`
measures, and they depend on nothing from it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from adt_ai.export_db.normalizer_identifiers import identifier_key


def strip_default_clauses(text: str, tokens: Iterable[str]) -> str:
    r"""Drop each default clause from `text`, and only when its value matches whole.

    `tokens` are regex fragments without their leading whitespace, e.g.
    ``INCREMENT\s+BY\s+1``. A plain ``str.replace(" INCREMENT BY 1", "")`` also
    matches the PREFIX of ``INCREMENT BY 10``, which drops the clause and fuses
    the remaining digits onto the neighbouring token: a sequence carrying
    ``INCREMENT BY 10 ... CACHE 200`` exported as ``MAXVALUE 99…9900`` with both
    clauses gone (ADT #663). Old ADT matched ``' INCREMENT BY 1 '`` with a
    trailing space for exactly this reason; the trailing ``\b`` here is that
    guard without requiring the clause to be followed by anything.
    """
    for token in tokens:
        text = re.sub(rf"\s+{token}\b", "", text, flags=re.IGNORECASE)
    return text


def owner_qualifier_stripper(object_owner: str | None) -> Callable[[re.Match[str]], str]:
    """Drop a schema qualifier only when it names the object's OWN owner.

    The unqualifying rewrite anchors on the object's own name, so a reference to
    a same-named object in another schema (`hr.orders` inside `sales.orders`)
    matched too and lost its qualifier, silently repointing the statement at the
    exporting schema (ADT #652). `_normalize_sql_identifier` already compares
    against `context.object_owner`; this is the same test on the raw-payload
    path. With no known owner the qualifier still goes, which is what every
    export without a resolved owner has always done.

    The match must expose the qualifier as the `owner` group.
    """

    def replace(match: re.Match[str]) -> str:
        return match.group(0) if is_foreign_owner(match.group("owner"), object_owner) else ""

    return replace


def is_foreign_owner(owner: str, object_owner: str | None) -> bool:
    """Whether `owner` names a schema other than the object's own (the `#652` rule).

    With no resolved owner nothing counts as foreign, so the qualifier goes, which
    is what every export without one has always done.
    """
    if not object_owner:
        return False
    return identifier_key(owner) != identifier_key(object_owner)
