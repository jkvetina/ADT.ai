"""The configured naming convention: what is a test package, and what does it test.

`ut3` used to hardcode `_UT` in five places — a SQL ``LIKE '%\\_UT'`` in four
queries and a string slice in the runner — so a project whose test packages are
named anything else, or live in a schema of their own, simply could not use the
command. Four config values replace all five:

* ``ut_pattern`` — which packages are test packages (``'_UT$'``);
* ``ut_match``   — capture group 1 pairs a test package back to the package it
  tests (``'^(.+)_UT$'``), which is what the coverage report's verdict columns
  are built on;
* ``ut_owner``   — the schema holding them; **the only one that defaults empty**,
  meaning the schema being tested;
* ``ut_module``  — capture group 1 is the module a **package** belongs to
  (``'^[^_]+_([^_]+)'``), which drives the two module roll-ups. It is matched
  against a suite's name and against a package under test's, so it must not be
  anchored to whatever marks a test package — nor to anything that has to follow
  the module token, since a module's own package (``ICT_VPD``) ends there.

**These are Oracle regular expressions, and every one of them is evaluated by
Oracle.** ``REGEXP_LIKE`` selects the test packages inside the dictionary query,
exactly where the old ``LIKE`` sat, and ``REGEXP_SUBSTR`` extracts the two
capture groups in the same pass. Nothing is fetched to be discarded and no
second regex engine is involved — a schema holds thousands of packages and a
handful of suites, so filtering after the round trip would spend the round trip
on rows nobody wants, on a command whose runtime already matters.

So this module compiles nothing. It is the config record and the two rules that
are not Oracle's to know: which schema owns the suites, and whether the module
roll-ups print at all.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# The convention `ut3` shipped with, now expressed as configuration rather than
# as a SQL literal — and shipped as real values, not blanks. Only `ut_owner`
# defaults empty, because "the schema being tested" is the only one of the four
# that has a sensible absent state.
DEFAULT_UT_PATTERN = "_UT$"
DEFAULT_UT_MATCH = "^(.+)_UT$"

# **Not anchored to the test-package suffix**, and that is the point. It is
# applied to two kinds of name — a suite, and a package under test — because the
# module is a property of the package, not of its test. Anchored to `_UT$` (as it
# shipped in card `#244`) it could only ever read a suite, so a package with no
# discovered suite had no module at all even when its own name spelled one; under
# `-name` that was most of the listing. Dropping the anchor also picks up a
# three-token suite like `ICT_INT_UT`, which the `.+_UT$` form needed a fourth
# token to match.
#
# **It does not require a trailing `_` either** (card `#248`). Prefix plus module
# and nothing after it is a real name: `ICT_VPD` is a module whose whole
# implementation is one package, and demanding a third token put it — and only it
# — in the unattributed group, immediately above the unnamed total row, where the
# two cannot be told apart. Measured by Jan on `ICT_OWNER@ORCLPDB1`: one package
# of 58. The anchor never bounded anything the capture group did not already
# bound, since `[^_]+` stops at the next underscore whether or not one is
# required to follow it.
DEFAULT_UT_MODULE = "^[^_]+_([^_]+)"

# Every expression is matched case-insensitively. Oracle stores identifiers
# upper case and a config file is hand-written, so a lower-case value that
# selected nothing would present as an empty green run — the one failure mode
# this whole module exists to make loud.
MATCH_PARAMETERS = "i"


@dataclass(frozen=True)
class UtNaming:
    """The four config values, passed to Oracle as binds."""

    pattern : str = DEFAULT_UT_PATTERN
    match   : str = DEFAULT_UT_MATCH
    owner   : str = ""
    module  : str = DEFAULT_UT_MODULE

    @classmethod
    def from_config(cls, config: Mapping[str, Any] | None) -> UtNaming:
        config = config or {}
        return cls(
            pattern = _text(config.get("ut_pattern"), DEFAULT_UT_PATTERN),
            match   = _text(config.get("ut_match"), DEFAULT_UT_MATCH),
            # Upper-cased because it is compared against `ALL_OBJECTS.OWNER`,
            # which is how Oracle stores it.
            owner   = _text(config.get("ut_owner"), "").upper(),
            # The one value a project may legitimately blank: a project with no
            # module convention should not be shown a column of empty groups.
            # Absent still means the default — only an explicit `ut_module: ''`
            # turns the roll-ups off.
            module  = _blankable(config.get("ut_module"), DEFAULT_UT_MODULE),
        )

    @property
    def modules_enabled(self) -> bool:
        """Is ``ut_module`` configured at all?

        Blanking it is how a project turns the roll-ups off; it ships set, so
        that is a deliberate act rather than the default state.
        """
        return bool(self.module)

    @property
    def module_bind(self) -> str | None:
        """``ut_module`` as Oracle sees it — ``None`` when the roll-ups are off.

        ``REGEXP_SUBSTR(name, NULL, ...)`` is NULL, so an unconfigured module
        column comes back empty without the query needing a second shape.
        """
        return self.module or None

    def owner_for(self, schema: str) -> str:
        """Which schema holds the test packages for ``schema``.

        The one place the empty-means-same-schema rule lives, so no caller has
        to remember it.
        """
        return self.owner or (schema or "").upper()

    def discovery_binds(self, schema: str) -> dict[str, object]:
        """Binds for the query that selects the suites and derives both names.

        Spelled out per query rather than passed as one superset: Oracle rejects
        a bind the statement does not declare, so a shared dictionary would
        couple every query to every other one's placeholders.
        """
        return {
            "ut_owner"   : self.owner_for(schema),
            "ut_pattern" : self.pattern,
            "ut_match"   : self.match,
            "ut_module"  : self.module_bind,
        }

    def selection_binds(self, schema: str) -> dict[str, object]:
        """Binds for a suite-schema query that only has to select, not derive."""
        return {
            "ut_owner"   : self.owner_for(schema),
            "ut_pattern" : self.pattern,
        }


def _text(value: Any, default: str) -> str:
    """A config value as text, treating blank and absent the same."""
    if value is None:
        return default
    return str(value).strip() or default


def _blankable(value: Any, default: str) -> str:
    """Like ``_text``, but an explicit blank stays blank.

    The difference matters for exactly one key. A missing ``ut_module`` is a
    project that never thought about modules and should get the shipped
    convention; ``ut_module: ''`` is a project that thought about it and said
    no. Collapsing the two would make the roll-ups impossible to turn off.
    """
    if value is None:
        return default
    return str(value).strip()


__all__ = [name for name in globals() if not name.startswith("__")]
