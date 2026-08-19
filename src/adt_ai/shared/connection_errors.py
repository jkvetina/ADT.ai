"""What went wrong with a connection, as a class the console can branch on.

The CLI picks a header and a remedy from the exception's TYPE and never from
reading its message (`cli/context_errors.py`), so a raise site chooses its own
screen here and no printer has to recognise wording. That is the shape ADT #182
introduced for config values, ADT #244 generalised to a base class, and ADT #407
finished for connections.

Four screens, and the boundary between them is what the reader does next:

* `ConnectionNotFoundError` keeps `CONFIGURATION NOT FOUND:` and its "run from a
  project folder that has a connection file" remedy. There is no file yet.
* `InvalidConnectionError` prints `CONFIGURATION INVALID:`. The file was read and
  needs editing.
* `CredentialUnavailableError` prints `CREDENTIAL UNAVAILABLE:`. The file is fine
  and the secret behind it could not be fetched.
* `ConnectFailedError` prints the shared `DATABASE CONNECTION FAILED:` screen.
  ADT.ai has stopped reading configuration and started talking to Oracle.

They live in their own module because `db.py`, `sqlcl_session.py` and the CLI
error printer all need them while none of them needs the loader, and because
`connections.py` sits against the 20 KB context guard: the same reason the schema
matching left it under ADT #395. `connections.py` re-exports every name, so the
`from adt_ai.shared.connections import ...` spelling stays correct everywhere.
"""

from __future__ import annotations


class ConnectionError(Exception):
    """Base error for connection loading failures."""


class ConnectionNotFoundError(ConnectionError):
    """Raised when a requested connection file, environment, or schema is missing."""


class InvalidConnectionError(ConnectionError):
    """The connection file was found and read, and cannot be used as written.

    Unparsable YAML, a document that is not a mapping, and a `db:` block whose
    own keys contradict each other all land here. The reader's next move is to
    edit that file, which is what separates this from the two classes below and
    from `ConnectionNotFoundError`, where there is no file to edit yet.
    """


class CredentialUnavailableError(ConnectionError):
    """The connection is described, and its credential cannot be obtained.

    A vault command that is missing, unauthenticated, slow or silent; an
    encryption key that is absent or wrong; a `db:` block naming two sources for
    one secret, which ADT.ai refuses to rank rather than picking one. The file
    itself may be perfectly fine, so the invalid screen would send the reader
    looking in the wrong place.
    """


class ConnectFailedError(ConnectionError):
    """A connect attempt was made with resolved details and did not succeed.

    Reported on the shared database-connection screen rather than on a
    configuration one: by this point ADT.ai has stopped reading configuration and
    started talking to Oracle or to SQLcl, so the wallet and credential advice
    that screen already carries is the remedy that fits.
    """
