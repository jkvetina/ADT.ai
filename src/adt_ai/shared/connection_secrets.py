"""How a schema's password is read out of a resolved connection block.

A `_cmd` key fetches it from the customer's vault (ADT #397), a `pwd!` marker
decrypts it (ADT #399), and `pwd_pattern:` derives it from the schema's name
(ADT #1081). Split out of `shared/connections.py`, which sits at the context
budget.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from adt_ai.shared import crypto
from adt_ai.shared.config import is_enabled
from adt_ai.shared.connection_errors import CredentialUnavailableError
from adt_ai.shared.secret import Secret
from adt_ai.shared.secret_command import SecretCommandError, block_secret


def fetched_secret(db: dict[str, Any], kind: str, context: str) -> Secret | None:
    """The secret a `_cmd` key fetches, as a connection failure when it cannot.

    A vault CLI that is missing, unauthenticated, or slow is a connect failure
    like a wrong password, so it re-raises as `CredentialUnavailableError` and
    lands on the credential banner rather than surfacing as an unexpected error.
    `block_secret`'s other refusal, a block naming two sources for one secret, is
    a file defect and is reported the same way on purpose: separating it would
    cost an exception class one module down, and the message names the keys to
    edit either way.
    """
    try:
        return block_secret(db, kind, context=context)
    except SecretCommandError as error:
        raise CredentialUnavailableError(str(error)) from error


def decrypt_if_enabled(
    value: Any,
    marker: Any,
    recorded_fingerprint: Any = None,
    *,
    key: str | None,
    context: str,
) -> Any:
    if not is_enabled(marker):
        return value

    def cannot_decrypt(error: Exception) -> CredentialUnavailableError:
        # The crypto error's own headline leads, and the secret it was reading
        # goes on the line under it (ADT #934): a headline wrapped inside another
        # sentence reads as shouting mid-line.
        headline, _, rest = str(error).partition("\n")
        remedy = rest.strip() or f"Pass -key or set {crypto.KEY_ENV}."
        return CredentialUnavailableError(f"{headline}\n\nFor {context}.\n{remedy}")

    try:
        resolved_key = crypto.resolve_key(key)
        # Fernet answers a wrong key and a damaged value with the same
        # exception, so without a recorded fingerprint (`pwd_key:`, ADT #399)
        # the commonest mistake of all reads as a corrupt connection file. The
        # comparison is close to free: it needs the same PBKDF2 derivation the
        # decrypt below is about to do, and `crypto` memoises that. A file
        # written before `#399` records nothing, and keeps the older, honestly
        # vaguer message rather than a guess about which case it hit.
        #
        # `readable_fingerprint` also returns None for a recorded value that is
        # not a digest, which a YAML loader can produce from one that happens to
        # look numeric. Such a value says nothing about the key, so it counts as
        # absent rather than as a mismatch: an unreadable fingerprint must never
        # refuse a correct key (ADT #398).
        expected = crypto.readable_fingerprint(recorded_fingerprint)
        actual = crypto.fingerprint(value, resolved_key) if expected else None
    except crypto.CryptoError as error:
        raise cannot_decrypt(error) from error

    if expected and actual != expected:
        raise CredentialUnavailableError(
            f"WRONG ENCRYPTION KEY FOR {context}\n\n"
            f"The stored value carries key fingerprint {expected}, the key in use\n"
            f"fingerprints as {actual}. Pass -key or set {crypto.KEY_ENV} to the key\n"
            "this value was encrypted with."
        )

    try:
        return crypto.decrypt(value, resolved_key)
    except crypto.CryptoError as error:
        raise cannot_decrypt(error) from error


# Oracle INITCAP: a word is a run of letters and digits, anything else splits.
_INITCAP_WORD = re.compile(r"[^\W_]+")


def _initcap(name: str) -> str:
    return _INITCAP_WORD.sub(lambda match: match[0].capitalize(), name)


_PLACEHOLDERS: dict[str, Callable[[str], str]] = {
    "<schema>": str.lower,
    "<Schema>": _initcap,
    "<SCHEMA>": str.upper,
}
_PLACEHOLDER = re.compile("|".join(map(re.escape, _PLACEHOLDERS)))


def expand_pwd_pattern(pattern: str, schema: str) -> str:
    """`<schema>`, `<Schema>` and `<SCHEMA>` become `schema` in lower, Oracle
    INITCAP and upper case; every other character, a placeholder in any other
    casing included, stays exactly as written."""
    return _PLACEHOLDER.sub(lambda match: _PLACEHOLDERS[match[0]](schema), pattern)
