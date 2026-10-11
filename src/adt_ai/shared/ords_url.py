"""The `ords:` key of a connection file, checked as it is read (ADT #1071).

Its own module because `shared/connections.py` sits at the context-size cap,
and this is the one rule the key carries beyond being a URL.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from adt_ai.shared.connection_errors import InvalidConnectionError

_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def ords_url(value: Any, context: str, *, external: bool = False) -> str | None:
    """The `ords:` URL, refused when it would send the password in the clear.

    REST-enabled SQL authenticates with HTTP basic authentication, so plain
    `http://` hands the database password to every hop between here and ORDS.
    That is accepted only on this machine's own loopback, where a local
    container is the normal shape. For the same reason it needs a password,
    which `auth: external` never resolves.
    """
    if value is None or not str(value).strip():
        return None
    if external:
        raise InvalidConnectionError(
            f"CONNECTION {context} COMBINES ORDS WITH AUTH EXTERNAL\n\n"
            "ORDS signs in with user and pwd, and auth: external has no password "
            "to send. Remove one of the two keys."
        )
    url = str(value).strip().rstrip("/")
    parsed = urlsplit(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise InvalidConnectionError(
            f"CONNECTION {context} HAS AN ORDS VALUE THAT IS NOT A URL\n\n"
            "Write the REST-enabled schema URL, "
            "for example https://host/ords/<schema alias>."
        )
    if scheme == "http" and parsed.hostname.lower() not in _LOOPBACK_HOSTS:
        raise InvalidConnectionError(
            f"CONNECTION {context} REACHES ORDS OVER PLAIN HTTP\n\n"
            "Every request carries the database password, so off this "
            "machine the ords URL has to be HTTPS."
        )
    return url


__all__ = ["ords_url"]
