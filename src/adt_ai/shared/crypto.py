"""Encryption for the secrets a connection file stores (ADT #399).

A stored secret is written as ``adt2$<salt_b64>$<fernet_token>``: a format
version, a per-secret random salt, and the ciphertext derived from it. The
version prefix is the part worth understanding, because everything else follows
from it.

Before ``#399`` the derivation used an empty PBKDF2 salt, deliberately, so that
``ADT_KEY`` alone reproduced the same Fernet key on any machine with nothing
stored beside the ciphertext. That stateless round trip is preserved for an
encrypted connection file transferred through an approved secret channel: the
salt now travels *inside* the stored value, so the recipient needs only the
separately supplied key. Connection files remain local secret material and are
never meant for Git. What the empty salt cost was offline resistance to a weak
``ADT_KEY``, because every ADT project shared one derivation and so one
dictionary table covered all of them. A per-secret salt removes that table.

The version prefix does two more jobs. It makes the KDF raisable: ``adt2``
derives at 600000 iterations, current OWASP guidance for PBKDF2-HMAC-SHA256,
where the old format sat at 100000, and the next raise (HKDF, which is what a
high-entropy key file actually wants, rather than stretching something already
random) can ship as ``adt3`` without touching a single stored value. And it
makes a value written by a *newer* ADT.ai say so, instead of surfacing as a
corrupt password.

A value with no recognised prefix is read as the pre-``#399`` format, so every
connection file anyone already holds keeps working untouched. Nothing rewrites
those in place: ``connection -set-pwd -encrypt`` writes the new format on the
next write, and ``connection -rekey`` (ADT #398) converts a whole file at once.

``fingerprint()`` is the other half of the card. It derives a short digest from
a stored value's own key material, which lets the loader tell a wrong ``ADT_KEY``
apart from a corrupt ciphertext, two cases Fernet reports identically. It is
recorded beside the secret as ``pwd_key:`` / ``wallet_pwd_key:``. Because it
comes from the value's *salted* derivation, testing a candidate key against a
recorded fingerprint costs the same 600000 iterations as testing it against the
ciphertext, so it adds no cheaper attack than the ciphertext already offers.
That property does not make either value suitable for Git. A digest over the
key alone would have been cheaper to attack than the thing it guards, and would
have handed back exactly the single shared derivation the per-secret salt exists
to remove.
"""

from __future__ import annotations

import base64
import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from adt_ai.shared.secret_command import SecretCommandError, read_key_text

KEY_ENV = "ADT_KEY"
# The key fetched from the customer's own secret manager instead of held on the
# machine (ADT #397). Same custody argument as `pwd_cmd`, one level up.
KEY_COMMAND_ENV = "ADT_KEY_CMD"

FORMAT_VERSION = 2
FORMAT_PREFIX = f"adt{FORMAT_VERSION}"
SALT_BYTES = 16
FINGERPRINT_LENGTH = 12

# A fingerprint is 12 hex characters, and roughly one in 440 of them reads as a
# NUMBER to a YAML loader: `123456789012` is an int, and `1e2345678901` is a
# float that overflows to `inf`, which destroys the value on the next round trip
# through the connection editor. Writers quote it for that reason, and readers
# check the shape before trusting it, so a recorded value that came back as
# anything other than a digest counts as ABSENT rather than as a mismatch. An
# unreadable fingerprint has to degrade to the behaviour of a file that records
# none, never to a false wrong-key refusal (ADT #398, found on CI by a run whose
# random salt happened to produce one).
_FINGERPRINT_RE = re.compile(rf"^[0-9a-f]{{{FINGERPRINT_LENGTH}}}$")

# Iterations per format version. Raising the cost means adding a version here
# and bumping FORMAT_VERSION, never editing a row: an edited row would change
# what an already-stored value means and silently fail to decrypt it.
ITERATIONS = {
    2: 600000,
}

# The pre-#399 format carried no prefix, no salt, and this count.
LEGACY_ITERATIONS = 100000

# A Fernet token is urlsafe base64 and can never contain `$`, so a value that
# matches this really is versioned rather than a legacy token that happens to
# look like one.
_VERSIONED = re.compile(r"^adt(\d+)\$([A-Za-z0-9_=-]*)\$(.+)$", re.DOTALL)

# Domain separation, so a fingerprint can never collide with some other digest
# taken over the same derived key.
_FINGERPRINT_DOMAIN = b"adt.key-fingerprint.v1\x00"


class CryptoError(Exception):
    """Raised when an encrypted ADT secret cannot be processed."""


def resolve_key(explicit: str | None = None) -> str:
    """The encryption key, from ``-key``, ``ADT_KEY``, or ``ADT_KEY_CMD``.

    ``-key`` outranks both environment variables, as it always has: it is a
    per-invocation override, not a second piece of configuration. The two
    variables are the same standing as each other, so setting both is an error
    rather than a silent precedence rule (ADT #397), the same posture ``pwd_cmd``
    beside ``pwd`` takes on a connection block.
    """
    if explicit:
        return _key_from(explicit)

    raw = os.getenv(KEY_ENV)
    command = os.getenv(KEY_COMMAND_ENV)
    if raw and command:
        raise CryptoError(
            f"Both {KEY_ENV} and {KEY_COMMAND_ENV} are set, so which one holds the "
            f"encryption key is ambiguous. Unset one of them, or pass -key to override "
            f"both for this run."
        )
    if command:
        # The command IS the indirection, so its output is the key itself and is
        # never read as a path to one. A second lookup nobody asked for, on a
        # value a vault happened to return, is not a helpful default.
        try:
            return read_key_text(command, context=KEY_COMMAND_ENV)
        except SecretCommandError as error:
            raise CryptoError(str(error)) from error
    if raw:
        return _key_from(raw)
    raise CryptoError(
        f"Encryption key not provided; pass -key or set {KEY_ENV} or {KEY_COMMAND_ENV}"
    )


def _key_from(raw: str) -> str:
    """A key given as a value, or as the path of a file holding one."""
    key_path = Path(raw).expanduser()
    if key_path.is_file():
        value = key_path.read_text(encoding="utf-8").strip()
        if not value:
            raise CryptoError(f"Encryption key file is empty: {key_path}")
        return value
    return raw


def encrypt(value: str, key: str) -> str:
    """Encrypt into the current versioned, salted format.

    Returns ``str`` where earlier versions returned ``bytes``, so the connection
    editor now writes a plain YAML scalar instead of a ``!!binary`` blob.
    Reading is unaffected either way: ``decrypt`` takes both.
    """
    salt = os.urandom(SALT_BYTES)
    token = _fernet(key, salt, ITERATIONS[FORMAT_VERSION]).encrypt(str(value).encode("utf-8"))
    stored = "$".join(
        (
            FORMAT_PREFIX,
            base64.urlsafe_b64encode(salt).decode("ascii"),
            token.decode("ascii"),
        )
    )
    if decrypt(stored, key) != value:
        raise CryptoError("Encrypted value failed round-trip verification")
    return stored


def decrypt(value: bytes | str, key: str) -> str:
    salt, iterations, token = _split(value)
    try:
        plaintext: bytes = _fernet(key, salt, iterations).decrypt(token)
        return plaintext.decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as error:
        raise CryptoError("Could not decrypt encrypted value") from error


def fingerprint(value: bytes | str, key: str) -> str:
    """Short digest identifying the key a stored value was encrypted under.

    Recorded beside the secret so the loader can report a wrong key rather than
    a corrupt value, and so a rekey can be verified secret by secret instead of
    hoped over. Derived from the value's own salt, so it leaves neither the key
    nor the derived material recoverable, and never becomes cheaper to attack
    than the ciphertext it sits next to.
    """
    salt, iterations, _ = _split(value)
    derived = _derive(key, salt, iterations)
    return hashlib.sha256(_FINGERPRINT_DOMAIN + derived).hexdigest()[:FINGERPRINT_LENGTH]


def readable_fingerprint(value: Any) -> str | None:
    """A recorded fingerprint, or ``None`` when it is not one.

    A YAML loader may hand back an ``int`` or an overflowed ``float`` for a
    digest that happens to look numeric, and a value mangled that way carries no
    information about the key. Returning ``None`` for it makes the caller behave
    as though nothing were recorded, which is the honest reading.
    """
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    return text if _FINGERPRINT_RE.match(text) else None


def _split(value: bytes | str) -> tuple[bytes, int, bytes]:
    """Read a stored value into its salt, iteration count, and Fernet token."""
    if isinstance(value, bytes):
        try:
            text = value.decode("ascii")
        except UnicodeDecodeError:
            # Not ASCII, so it carries no version prefix. Hand it to the legacy
            # path and let Fernet reject it with the usual message.
            return b"", LEGACY_ITERATIONS, value
    else:
        text = str(value)

    match = _VERSIONED.match(text)
    if match is None:
        return b"", LEGACY_ITERATIONS, text.encode("utf-8")

    version = int(match.group(1))
    iterations = ITERATIONS.get(version)
    if iterations is None:
        raise CryptoError(
            f"Encrypted value is in format adt{version}, which this ADT.ai cannot read. "
            "Upgrade ADT.ai, or re-encrypt the value with the version you have."
        )
    try:
        salt = base64.urlsafe_b64decode(match.group(2))
    except (ValueError, TypeError) as error:
        raise CryptoError(
            "Could not decrypt encrypted value: the stored salt is not valid base64"
        ) from error
    return salt, iterations, match.group(3).encode("ascii")


def _fernet(key: str, salt: bytes, iterations: int) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_derive(key, salt, iterations)))


@lru_cache(maxsize=64)
def _derive(key: str, salt: bytes, iterations: int) -> bytes:
    """PBKDF2 key derivation, memoised per (key, salt, iterations).

    600000 iterations measures at roughly 160 ms per derivation, and a per-secret
    salt means a run pays that once per secret rather than once per process. The
    cache is what keeps a command that resolves the same connection more than
    once from paying twice. It is bounded, and it holds nothing the process was
    not already holding: ``ConnectionResult`` carries the key in memory for its
    whole lifetime regardless.

    The key is repeated seven times before derivation. That is an OLD ADT quirk
    with no cryptographic effect (PBKDF2 absorbs any input length), kept because
    the legacy path has to reproduce it exactly and one derivation function is
    easier to keep correct than two.
    """
    derivation = PBKDF2HMAC(
        algorithm  = hashes.SHA256(),
        length     = 32,
        salt       = salt,
        iterations = iterations,
    )
    derived: bytes = derivation.derive((7 * key).encode("utf-8"))
    return derived
