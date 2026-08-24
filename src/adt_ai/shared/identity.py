"""Load the per-developer ``config/IDENTITY.yaml`` identity file.

``IDENTITY.yaml`` is gitignored and never committed, it holds the developer's
personal identity (``db_schema``, ``apex_account``, ``email``) so a schema
worked by several developers can still resolve who did what. There is
deliberately no committed sample; the shape is documented in ``docs/config.md``.

**This module is the single answer to "who is the user", and since ADT #469 that
includes the git half.** Two facts, two halves:

* the DATABASE identity, ``db_schema``, read by ``export_db -my`` and by every
  new database connection's ``DBMS_SESSION.SET_IDENTIFIER`` before
  ``STARTUP.sql`` runs;
* the COMMIT identity, ``apex_account`` and ``email``, read by every ``-my`` and
  ``-by`` that filters git history, and by ``export_apex -my`` when it matches
  APEX workspace developers.

``email`` and ``apex_account`` sat in the documented shape and in this docstring
for months with **no reader anywhere in the tree**, while five call sites in four
modules asked ``git config`` directly through three different spellings. Jan,
2026-08-22: *"WHEN we already have IDENTITY file, dont you think the git account
should be rather added there, then have different paths how to resolve who the
user is???"*

**Git is the fallback, not a peer.** The file is optional and gitignored, so a
checkout that has none behaves exactly as it did: ``git config user.email`` and
``user.name`` answer when the file states nothing. The two halves fall back
independently, or a file naming only an account would silently lose its email.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from adt_ai.shared.git_files import git_config_value

IDENTITY_FILENAME = "IDENTITY.yaml"


def load_identity(search_paths: Iterable[str | Path]) -> dict[str, Any]:
    """Return the first readable ``IDENTITY.yaml`` on ``search_paths``, else ``{}``.

    The file is hand-edited and best-effort by design: a syntax error costs a
    warning and the identity features, never the command run.
    """
    for directory in search_paths:
        path = Path(directory) / IDENTITY_FILENAME
        if path.is_file():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError as error:
                print(f"Warning: ignoring unreadable {path}: {error}", file=sys.stderr)
                continue
            if isinstance(data, dict):
                return data
    return {}


def session_identifier(identity: Mapping[str, Any]) -> str | None:
    """The identity's db schema, used as the session identifier, else ``None``."""
    value = (
        identity.get("db_schema")
        or identity.get("db")
        or identity.get("schema")
    )
    return str(value) if value else None


def default_search_paths(root: Path | str) -> list[Path]:
    """Where to look for ``IDENTITY.yaml`` when the caller knows only a root.

    The same two places `cli/context._config_search_paths` falls back to with no
    `-config-dir`, in the same order, so a project keeping `IDENTITY.yaml` beside
    `config.yaml` is found either way. A caller that HAS a `-config-dir` passes
    its own paths instead: that flag is an override and the derived default must
    not quietly win over it.

    This lives here rather than in `cli/context` because the runners that need an
    identity (`search_repo`, `rebuild`, `calendar`) carry a root and no argparse
    namespace, and reaching into `cli` from `shared` would invert the layering.
    """
    root = Path(root)
    return [root / "config", root]


def commit_email(identity: Mapping[str, Any], root: Path | None = None) -> str:
    """The developer's commit address: the file's ``email``, else git's.

    Compared as a case-insensitive substring of a stored commit author, which is
    why the value is stripped: `IDENTITY.yaml` is hand-edited, and a trailing
    space in a substring match selects nothing and says nothing about why.

    A blank or missing value falls back rather than returning ``""``. An empty
    author filter matches no commit at all, so returning one would build a patch
    that silently comes out empty, which is the failure `#467` was filed on.
    """
    value = str(identity.get("email") or "").strip()
    return value or git_config_value("user.email", root).strip()


def commit_account(identity: Mapping[str, Any], root: Path | None = None) -> str:
    """The developer as another system's records name them: ``apex_account``.

    APEX workspace developers are often `FIRST.LAST` logins rather than
    addresses, which is the whole reason this key exists beside ``email``. Falls
    back to `git config user.name`, the name a commit author line carries.
    """
    value = str(identity.get("apex_account") or "").strip()
    return value or git_config_value("user.name", root).strip()


def commit_identity(
    identity: Mapping[str, Any],
    root: Path | None = None,
) -> tuple[str, str]:
    """``(account, email)`` for one already-loaded identity mapping."""
    return commit_account(identity, root), commit_email(identity, root)


def resolve_commit_identity(
    search_paths: Iterable[str | Path] | None = None,
    root: Path | None = None,
) -> tuple[str, str]:
    """Load the identity file and resolve ``(account, email)`` in one call.

    The one entry point every git-backed ``-my`` / ``-by`` goes through, so a
    grep for "who is the user" lands in this module rather than in five call
    sites spelling `git config` three ways (ADT #469).

    ``search_paths`` is the caller's own resolution when it has one (a CLI edge
    that saw `-config-dir`); otherwise the paths are derived from ``root``.
    """
    if search_paths is None:
        search_paths = default_search_paths(root) if root is not None else []
    return commit_identity(load_identity(search_paths), root)


def resolve_commit_email(
    search_paths: Iterable[str | Path] | None = None,
    root: Path | None = None,
) -> str:
    """The email half of :func:`resolve_commit_identity`, for the git filters."""
    return resolve_commit_identity(search_paths, root)[1]
