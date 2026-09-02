"""Locating and re-encrypting the secrets a connection file stores (ADT #398).

Split out of ``runner.py`` when that file crossed the 20 KB context budget: the
editor owns structural edits to one named environment or schema, while this
module owns the whole-file credential sweep, which is a different shape of work
(it walks every environment, it never adds or removes a key, and it is the only
action that must not write anything unless it can write everything).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from adt_ai.connection.errors import ConnectionEditError
from adt_ai.shared import crypto
from adt_ai.shared.config import is_enabled

if TYPE_CHECKING:
    from adt_ai.connection.runner import ConnectionEditRequest

# The (value, marker) pairs a rekey walks. `wallet_password` is a read-only
# legacy spelling the loader still accepts, so a rekey has to carry it or a file
# using it would come out half converted.
ENCRYPTABLE_PAIRS = (
    ("pwd", "pwd!"),
    ("wallet_pwd", "wallet_pwd!"),
    ("wallet_password", "wallet_password!"),
)

# Line width for every writer that touches a connection file. Wide enough that a
# stored secret stays on its own key's line: see the note in `runner._yaml`.
SECRET_SAFE_YAML_WIDTH = 4096


def fingerprint_key(value_key: str) -> str:
    """The key recording which encryption key a stored secret sits under."""
    return f"{value_key}_key"


def write_fingerprint(block: dict[str, Any], value_key: str, digest: str) -> None:
    """Record a key fingerprint as an explicitly quoted YAML string.

    Quoting is not cosmetic here. A 12 character hex digest is a number to a
    YAML loader about once in every 440: `123456789012` loads as an int, and
    `1e2345678901` loads as a float that overflows to `inf`. The int survives a
    round trip by luck, the float does not, so an unquoted fingerprint can be
    silently destroyed by the next edit to the file and then read as a wrong
    key forever after. One quoted scalar removes the whole class.
    """
    block[fingerprint_key(value_key)] = DoubleQuotedScalarString(digest)


@dataclass(frozen=True)
class StoredSecret:
    """One marked secret located in the document, named the way a user reads it."""

    label     : str
    block     : dict[str, Any]
    value_key : str

    @property
    def fingerprint_key(self) -> str:
        return fingerprint_key(self.value_key)

    @property
    def stored(self) -> Any:
        return self.block.get(self.value_key)

    @property
    def recorded_fingerprint(self) -> str | None:
        return crypto.readable_fingerprint(self.block.get(self.fingerprint_key))


def _secret_blocks(data: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every mapping in the document that may hold a secret, with its label.

    Walked structurally rather than recursively: the shape is known (environment
    `db`, environment `wallet`, and each `schemas.<name>.db`), and a blind
    recursive search would eventually pick up a `pwd` key somewhere that is not
    a credential.
    """
    blocks: list[tuple[str, dict[str, Any]]] = []
    if not isinstance(data, dict):
        return blocks
    for environment, node in data.items():
        if not isinstance(node, dict):
            continue
        for section in ("db", "wallet"):
            block = node.get(section)
            if isinstance(block, dict):
                blocks.append((f"{environment}.{section}", block))
        schemas = node.get("schemas")
        if not isinstance(schemas, dict):
            continue
        for schema, schema_node in schemas.items():
            if not isinstance(schema_node, dict):
                continue
            block = schema_node.get("db")
            if isinstance(block, dict):
                blocks.append((f"{environment}.{schema}", block))
    return blocks


def marked_secrets(data: Any) -> list[StoredSecret]:
    """Every secret in the document whose encryption marker is enabled."""
    secrets: list[StoredSecret] = []
    for label, block in _secret_blocks(data):
        for value_key, marker_key in ENCRYPTABLE_PAIRS:
            if not is_enabled(block.get(marker_key)):
                continue
            value = block.get(value_key)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            secrets.append(
                StoredSecret(label=f"{label}.{value_key}", block=block, value_key=value_key)
            )
    return secrets


def rekey_secrets(data: Any, request: ConnectionEditRequest) -> tuple[str, str]:
    """Re-encrypt every marked secret in the document under a new key.

    Two properties carry this action, and both are about the failure it must not
    have. It **decrypts everything before it writes anything**, because a rekey
    that wrote as it went would leave a file no single key can open, which is
    strictly worse than one that refused. And it **checks each recorded
    `pwd_key:` fingerprint first**, which is what turns "the key is wrong" and
    "this file is already half rotated" into two different messages instead of
    one puzzling decrypt failure.

    Re-encrypting is not rotating the database password. They are separate acts,
    and after a suspected leak only the second one matters.
    """
    if not request.old_key:
        raise ConnectionEditError("-rekey requires -old-key")
    if not request.new_key:
        raise ConnectionEditError("-rekey requires -new-key")
    try:
        old_key = crypto.resolve_key(request.plaintext("old_key"))
        new_key = crypto.resolve_key(request.plaintext("new_key"))
    except crypto.CryptoError as error:
        raise ConnectionEditError(str(error)) from error

    secrets = marked_secrets(data)
    if not secrets:
        raise ConnectionEditError(
            f"no encrypted secrets in {request.path.name}: -rekey rewrites values carrying "
            "pwd!/wallet_pwd!, and this file has none. A cleartext password is changed "
            "with -set-pwd -encrypt, not with -rekey"
        )

    count = len(secrets)
    summary = f"re-encrypt {count} secret{'' if count == 1 else 's'} under the new key"
    # The preview names each secret and renders neither its plaintext nor its
    # ciphertext, and it deliberately decrypts nothing: the listing answers
    # "what would this touch", and a preview costing 160 ms per secret to
    # produce would discourage the habit of running one.
    listing = "\n".join(f"  {secret.label}" for secret in secrets)
    if not request.apply:
        return summary, listing

    _verify_old_key(secrets, old_key, request)

    plaintext: list[str] = []
    for secret in secrets:
        try:
            plaintext.append(crypto.decrypt(secret.stored, old_key))
        except crypto.CryptoError as error:
            raise ConnectionEditError(
                f"{secret.label} did not open with the given -old-key: wrong key, or a "
                f"damaged value. Nothing was written ({error})"
            ) from error

    for secret, value in zip(secrets, plaintext, strict=True):
        stored = crypto.encrypt(value, new_key)
        secret.block[secret.value_key] = stored
        write_fingerprint(secret.block, secret.value_key, crypto.fingerprint(stored, new_key))

    return summary, listing


def _verify_old_key(
    secrets: list[StoredSecret],
    old_key: str,
    request: ConnectionEditRequest,
) -> None:
    """Separate a wrong `-old-key` from a file that is already under two keys.

    Both look identical at the decrypt call and they need opposite responses:
    the first is a typo to correct, the second is a partial rotation to finish.
    Counting how many recorded fingerprints disagree is what tells them apart,
    and it is the reason `#399` records one fingerprint per secret rather than
    one per key.
    """
    recorded = [secret for secret in secrets if secret.recorded_fingerprint]
    mismatched = [
        secret.label
        for secret in recorded
        if crypto.fingerprint(secret.stored, old_key) != secret.recorded_fingerprint
    ]
    if not mismatched:
        return
    if len(mismatched) == len(recorded):
        raise ConnectionEditError(
            f"wrong -old-key: it matches none of the {len(recorded)} recorded key "
            f"fingerprints in {request.path.name}. Nothing was written"
        )
    raise ConnectionEditError(
        f"{request.path.name} is already under more than one key, so no single -old-key "
        f"opens all of it: {', '.join(mismatched)} did not match the given -old-key while "
        f"{len(recorded) - len(mismatched)} other secret(s) did. Settle the odd one out "
        "first with -set-pwd -encrypt, then rekey the file. Nothing was written"
    )
