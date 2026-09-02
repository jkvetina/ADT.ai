"""A secret fetched by running a command (ADT #397).

``pwd_cmd:`` on a schema's ``db:`` block, ``wallet_pwd_cmd:`` on the wallet
block, and ``ADT_KEY_CMD`` one level up for the encryption key. ADT.ai runs the
command and takes its stdout as the value, so the connection file holds no
password and no ciphertext at all.

**What this buys is custody, not in-process secrecy.** The value still reaches
python-oracledb as plaintext, and an agent allowed to run ``adtai`` can run the
same command itself, so nothing here hides anything from such an agent. What
changes is who owns the secret: the customer's own vault holds it, rotates it,
revokes it, and records who fetched it. Revoking there revokes it everywhere,
which is not true of a value copied into a file.

Three properties are load bearing and each is pinned by a test:

* **No shell.** The command runs as an argv list. A string form is split with
  :func:`shlex.split`, so a space in a vault path stays one argument and a ``&``
  or a ``;`` inside one is passed through rather than reinterpreted. A YAML list
  skips the split entirely.
* **The value is a** :class:`~adt_ai.shared.secret.Secret` **at the point of
  capture**, never a plain ``str`` handed around for someone downstream to wrap.
  :func:`read_key_text` is the one exception and says why in its own docstring.
* **A failure never echoes output or arguments.** stdout is the channel the
  secret arrives on, while arguments and stderr are controlled by the secret
  provider and may contain tokens or credentials. A diagnostic therefore names
  only the executable and the failure class or exit status.

stdin is closed. A command that stops to prompt fails at once instead of hanging
behind a captured pipe, which is the ``#188`` shape, and the timeout below is the
floor under a command that blocks on the network instead.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Sequence
from typing import Any

from adt_ai.shared.secret import Secret
from adt_ai.shared.subprocess_env import safe_subprocess_environment

# Generous for a vault round trip over a VPN, short enough that an unattended run
# reports a cause instead of sitting there.
DEFAULT_TIMEOUT_SECONDS = 60.0

# Per secret: the keys that name a command, and the stored-value keys that cannot
# stand beside one. The wallet password has two accepted spellings already, so
# both, and both of their `#399` companions, belong on the refused list.
_SOURCES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "pwd": (
        ("pwd_cmd",),
        ("pwd", "pwd!", "pwd_key"),
    ),
    "wallet_pwd": (
        ("wallet_pwd_cmd", "wallet_password_cmd"),
        (
            "wallet_pwd",
            "wallet_password",
            "wallet_pwd!",
            "wallet_password!",
            "wallet_pwd_key",
            "wallet_password_key",
        ),
    ),
}

#: The same map, exported: the connection editor drops the command keys a stored
#: value supersedes, and reads which they are from here rather than keeping a
#: second copy of the pairing `block_secret` refuses on (ADT #651).
SECRET_SOURCES = _SOURCES

# Every command key this module owns, for the `-like` clone strip in
# `connection/runner.py`: a vault path names its environment, so carrying one
# into a cloned environment would point UAT at the DEV credential.
COMMAND_KEYS = tuple(sorted(key for keys, _ in _SOURCES.values() for key in keys))


class SecretCommandError(Exception):
    """Raised when the command supplying a secret cannot produce one."""


def read_secret(
    command: Any,
    *,
    context: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> Secret:
    """Run ``command`` and return its stdout as a masked secret."""
    return Secret(_capture(command, context=context, timeout_seconds=timeout_seconds))


def read_key_text(
    command: Any,
    *,
    context: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """The same capture, as plaintext, for ``ADT_KEY_CMD``.

    An encryption key is not a :class:`Secret` anywhere in this tree:
    ``crypto.resolve_key`` returns ``str``, a key file is read as text, and every
    consumer takes a string. Wrapping the value here only to reveal it one call
    later would add a plaintext exit to
    ``tests/contracts/test_secret_reveal_sites.py`` and hide nothing, so this
    function is deliberately separate and deliberately narrow.
    """
    return _capture(command, context=context, timeout_seconds=timeout_seconds)


def block_secret(block: dict[str, Any], kind: str, *, context: str) -> Secret | None:
    """The secret a resolved ``db:`` block fetches by command, or ``None``.

    ``None`` means the block names no command and the caller should read its
    stored value as before. A block naming both is refused rather than ranked:
    a precedence rule would pick one silently, and connecting with a credential
    the reader did not choose is the failure worth being loud about.
    """
    command_keys, stored_keys = _SOURCES[kind]
    present = [key for key in command_keys if _present(block, key)]
    if not present:
        return None
    if len(present) > 1:
        raise SecretCommandError(
            f"{context}: {_and_list(present)} are both configured, and they are two "
            "spellings of one setting. Keep one."
        )

    conflicting = [key for key in stored_keys if _present(block, key)]
    if conflicting:
        raise SecretCommandError(
            f"{context}: {present[0]} is configured beside {_and_list(conflicting)}, so "
            "the secret has two sources. A block reads its secret from exactly one "
            f"place: remove {_and_list(conflicting)} to fetch it with the command, or "
            f"remove {present[0]} to keep the stored value."
        )
    return read_secret(block[present[0]], context=context)


def _present(block: dict[str, Any], key: str) -> bool:
    # A key written with no value (`pwd:` on its own) loads as None and means the
    # user left it blank, so it counts as absent rather than as a second source.
    return block.get(key) is not None


def _and_list(keys: Sequence[str]) -> str:
    if len(keys) == 1:
        return keys[0]
    return ", ".join(keys[:-1]) + f" and {keys[-1]}"


def _argv(command: Any, *, context: str) -> list[str]:
    if isinstance(command, (list, tuple)):
        argv = [str(part) for part in command]
    else:
        argv = shlex.split(str(command))
    if not argv:
        raise SecretCommandError(f"{context}: the configured command is empty")
    return argv


def _capture(command: Any, *, context: str, timeout_seconds: float) -> str:
    argv = _argv(command, context=context)
    program = shlex.quote(argv[0])
    try:
        completed = subprocess.run(
            argv,
            capture_output = True,
            text           = True,
            stdin          = subprocess.DEVNULL,
            timeout        = timeout_seconds,
            check          = False,
            env            = safe_subprocess_environment(),
        )
    except FileNotFoundError as error:
        raise SecretCommandError(f"{context}: command not found: {program}") from error
    except PermissionError as error:
        raise SecretCommandError(
            f"{context}: command is not executable: {program}"
        ) from error
    except subprocess.TimeoutExpired as error:
        raise SecretCommandError(
            f"{context}: command timed out after {timeout_seconds:g} seconds: {program}"
        ) from error

    if completed.returncode != 0:
        raise SecretCommandError(
            f"{context}: command failed with exit status {completed.returncode}: "
            f"{program}"
        )

    # One line ending, and only one. A password may legitimately end in a space,
    # so a blanket strip would quietly change the credential.
    value = completed.stdout.removesuffix("\n").removesuffix("\r")
    if not value:
        raise SecretCommandError(f"{context}: command produced no output: {program}")
    return value
