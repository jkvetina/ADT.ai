"""`repo_authors`: one developer, two commit addresses (ADT #831).

A developer who commits from a personal machine under a personal address and
from work under the company one shows up as two people in git history. The
legacy ADT carried `repo_authors`, a flat `personal: company` map, and this is
that key: every history reader maps a stored commit author through it before it
shows, groups or filters on it, so `calendar` prints one row and `search
-by`/`-my` match both addresses. An absent or empty key maps nothing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CONFIG_KEY = "repo_authors"


def author_aliases(config: Mapping[str, Any]) -> dict[str, str]:
    raw = config.get(CONFIG_KEY)
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"INVALID {CONFIG_KEY} IN CONFIG\n\n"
            "It must map a personal address to a company address, "
            f"got {type(raw).__name__}."
        )
    return {
        str(personal).strip().lower(): str(company).strip()
        for personal, company in raw.items()
        if str(personal or "").strip() and str(company or "").strip()
    }


def canonical_author(author: str, aliases: Mapping[str, str]) -> str:
    return aliases.get(author.strip().lower(), author)
