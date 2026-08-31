"""Environment passed to child processes without ADT.ai's own secrets.

An environment variable is inherited by every child unless the parent removes
it. Git, pip, Java/SQLcl, and a configured vault command do not need ADT.ai's
connection-file encryption key, so handing it to them only widens the plaintext
surface. Keep this list deliberately narrow: credentials owned by another tool
may be required by that tool, while these two names belong exclusively to us.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

SENSITIVE_ENV_NAMES = frozenset({"ADT_KEY", "ADT_KEY_CMD"})


def safe_subprocess_environment(
    source: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy ``source`` (or the process environment), dropping ADT secrets."""
    environment = dict(os.environ if source is None else source)
    for name in SENSITIVE_ENV_NAMES:
        environment.pop(name, None)
    return environment
