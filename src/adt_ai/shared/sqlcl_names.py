"""Named SQLcl connections (ADT #148, reshaped by #1079).

SQLcl keeps its own connection storage (``connect -save`` / ``connect -name``,
password in the OS secure store). ADT registers one named connection per
(connection file, environment, schema) tuple so generated SQLcl scripts connect
by name instead of embedding cleartext credentials on every call.

The name is ``ADT_`` + the connection-file basename (its parent folder for the
generic ``connections.yaml``), then ``_<ENV>`` only when the file defines more
than one environment, and ``_<SCHEMA>`` only when that environment defines more
than one schema. It is worked out on every run and never written anywhere: the
connection file is the user's, and ADT records nothing in it (ADT #1079). A
``sqlcl:`` the user wrote himself still wins over the derivation; one ADT wrote
in an earlier version is recognised by :func:`is_generated_sqlcl_name` and
ignored, and `connection_tidy` removes it from the file.

Nothing is fingerprinted either. The connect plan tries the stored name first;
a store that lost the entry, a password the database no longer accepts, or a
saved entry that reaches a different database than the file names all fail that
first connect, and only then is the name re-registered (`sqlcl_connect`).
"""

from __future__ import annotations

import re
from itertools import product
from pathlib import Path

SQLCL_NAME_PREFIX = "ADT_"

# Line width for every writer that touches a connection file (#670, moved out of
# `connection/stored_secrets.py`: `shared/` never imports a command package, and
# this module -- itself `shared/` -- needed the constant too). Wide enough that a
# stored secret stays on its own key's line: see the note in `connection.runner._yaml`.
SECRET_SAFE_YAML_WIDTH = 4096

# The generic filename identifies no project; the parent folder does.
_GENERIC_FILE_STEMS = {"connections"}

# SQLcl's CONNMGR subcommand is ``DELETE -conn <name>``, there is no ``DEL``
# abbreviation, it fails with "Expected a subcommand" (verified against SQLcl
# 26.1's own bundled help text). A constant because two paths once built this
# line and drifted onto two different wrong strings; the re-registration
# preamble in ``sqlcl_connect`` is the one that builds it today.
CONNMGR_DELETE_COMMAND = "CONNMGR DELETE -conn {name}"

# The six hex characters ADT #660 appended to every name. #1079 dropped them;
# a recorded name that still carries them is one ADT wrote, never the user.
_RETIRED_PATH_HASH = re.compile(r"_[0-9a-f]{6}$")


def derive_sqlcl_name(
    source_file: Path,
    environment: str,
    schema: str,
    *,
    multi_environment: bool,
    multi_schema: bool,
) -> str:
    parts = [_file_base(source_file)]
    if multi_environment:
        parts.append(environment)
    if multi_schema:
        parts.append(schema)
    return _join(parts)


def is_generated_sqlcl_name(
    value: object,
    source_file: Path | str,
    environment: str,
    schema: str,
) -> bool:
    """True when ``value`` is a name ADT derived, in any version, for this tuple.

    Every shape a past derivation could have produced is accepted: with or
    without the environment, with or without the schema (both depended on how
    many the file held at the time), and with or without #660's path hash. A
    name matching none of them is the user's own and is kept.
    """
    text = str(value or "").strip()
    if not text:
        return False
    text = _RETIRED_PATH_HASH.sub("", text)
    base = _file_base(Path(source_file))
    for with_env, with_schema in product((False, True), repeat=2):
        parts = [base]
        if with_env:
            parts.append(environment)
        if with_schema:
            parts.append(schema)
        if text.casefold() == _join(parts).casefold():
            return True
    return False


def _file_base(source_file: Path) -> str:
    base = source_file.stem
    if base.lower() in _GENERIC_FILE_STEMS:
        base = source_file.parent.name
    return base


def _join(parts: list[str]) -> str:
    return SQLCL_NAME_PREFIX + "_".join(_sanitize(part) for part in parts)


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")
