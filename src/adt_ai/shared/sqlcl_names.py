"""Named SQLcl connections (ADT #148).

SQLcl keeps its own connection storage (``connect -save`` / ``connect -name``,
password in the OS secure store). ADT registers one named connection per
(connection file, environment, schema) tuple so generated SQLcl scripts connect
by name instead of embedding cleartext credentials on every call.

The name is ``ADT_`` + the connection-file basename, qualified with ``_<ENV>``
only when the file defines more than one environment and ``_<SCHEMA>`` only
when that environment defines more than one schema, every tuple gets a unique
deterministic name while single-purpose files keep the bare ``ADT_<BASENAME>``.
The assigned name and a credential fingerprint are recorded back into the
connection YAML (round-trip, comments preserved) so the user can see, and
override, the SQLcl name in use, and so a credential change is detected and
re-registered on the next SQLcl call.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml as pyyaml
from ruamel.yaml import YAML

from adt_ai.shared import text_files

if TYPE_CHECKING:
    from adt_ai.shared.connections import Connection

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
# 26.1's own bundled help text). One shared constant so both the named-connection
# re-registration path (db.py) and diff's ephemeral-connection cleanup
# (diff/queries/commands.py) can't drift onto two different wrong strings again.
CONNMGR_DELETE_COMMAND = "CONNMGR DELETE -conn {name}"


def derive_sqlcl_name(
    source_file: Path,
    environment: str,
    schema: str,
    *,
    multi_environment: bool,
    multi_schema: bool,
) -> str:
    base = source_file.stem
    if base.lower() in _GENERIC_FILE_STEMS:
        base = source_file.parent.name
    parts = [base]
    if multi_environment:
        parts.append(environment)
    if multi_schema:
        parts.append(schema)
    parts.append(path_discriminator(source_file))
    return SQLCL_NAME_PREFIX + "_".join(_sanitize(part) for part in parts)


def path_discriminator(source_file: Path) -> str:
    """Six hex characters of the connection file's own resolved path.

    Everything else in the name comes from the file's stem, or from its parent
    folder for the generic `connections.yaml`, and the SQLcl store is PER USER,
    not per project. So two projects whose folders share a name (or two
    `connections.path` overrides each pointing at a `connections.yaml`) took the
    same `ADT_<name>` in that one store. The fingerprint check
    (`sqlcl_connect.py`) compares only against the project's own YAML, so once
    project B registered, project A connected `-name` and ran, silently, against
    B's database (ADT #660).

    A name already recorded as `sqlcl:` in a connection file wins over this
    derivation (`connections._sqlcl_name`), so an existing registration keeps the
    name it has and a user's own override is untouched; only a project that has
    never registered takes the new shape.
    """
    resolved = str(Path(source_file).expanduser().resolve())
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:6]


def _sanitize(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", value).strip("_")


def credential_fingerprint(connection: Connection) -> str:
    """Short digest over everything a saved SQLcl connection depends on.

    A mismatch against the recorded ``sqlcl_sync`` value means the credentials
    changed since registration and the named connection must be re-saved.
    """
    # This is the third and last approved `Secret.reveal()` site (ADT #400), and
    # it reveals rather than asking `Secret` for a digest of its own so the
    # material stays byte-identical to what earlier versions hashed. A different
    # derivation would mismatch every `sqlcl_sync` value already recorded in a
    # user's connection file, re-registering every named connection and
    # rewriting every file on the first run after an upgrade. Nothing leaves
    # here but the 12 character digest below.
    #
    # One project shape does change, and it changes because it was wrong: an
    # UNENCRYPTED `pwd: !!binary` value used to arrive here as `bytes`, so
    # `str(part or "")` hashed Python's `b'secret'` repr rather than the
    # password. `Secret` decodes at construction, so the material is now the
    # text. Such a project re-registers once, which is the intended behaviour
    # for a fingerprint that was never hashing the credential it named.
    # Encrypted values are unaffected: they are decrypted to `str` before they
    # ever reach a Connection.
    material = "\x00".join(
        str(part or "")
        for part in (
            connection.username,
            connection.password.reveal(),
            connection.hostname,
            connection.port,
            connection.service,
            connection.sid,
            connection.wallet_path,
            connection.wallet_password.reveal(),
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:12]


def record_sqlcl_registration(
    source_file: Path | str,
    environment: str,
    schema: str,
    name: str,
    fingerprint: str,
) -> None:
    """Write ``sqlcl`` / ``sqlcl_sync`` onto the schema's ``db:`` block.

    Round-trip edit (comments and key order preserved). Best-effort by design:
    bookkeeping must never break the export that triggered it, on any failure
    the fingerprint simply stays stale and the next run re-registers.
    """
    try:
        path = Path(source_file)
        text = path.read_text(encoding="utf-8")
        # Safe-load pre-check before the round-tripper touches an externally
        # authored file, same defense-in-depth as connection/runner.py; any
        # unsupported tag raises and lands in the best-effort except below.
        pyyaml.safe_load(text)
        yaml = YAML()
        yaml.preserve_quotes = True
        yaml.indent(mapping=2, sequence=4, offset=2)
        # Same width as the connection editor. This writer rewrites the whole
        # document to record two short keys, so at the default width of 80 it
        # would reflow any encrypted `pwd:` in the file onto a continuation
        # line, and the two writers would flip the layout back and forth.
        yaml.width = SECRET_SAFE_YAML_WIDTH
        data = yaml.load(text)
        schema_node = _schema_node(data, environment, schema)
        if schema_node is None:
            return
        db_node = schema_node.get("db")
        if db_node is None:
            db_node = {}
            schema_node["db"] = db_node
        db_node["sqlcl"] = name
        db_node["sqlcl_sync"] = fingerprint
        with text_files.open_text(path) as handle:
            yaml.dump(data, handle)
    except Exception:
        return


def _schema_node(data: Any, environment: str, schema: str) -> Any:
    if not isinstance(data, dict):
        return None
    environment_node = data.get(environment)
    if not isinstance(environment_node, dict):
        return None
    schemas = environment_node.get("schemas")
    if not isinstance(schemas, dict):
        return None
    node = schemas.get(schema)
    return node if isinstance(node, dict) else None
