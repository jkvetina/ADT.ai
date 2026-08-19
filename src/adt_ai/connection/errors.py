from __future__ import annotations


class ConnectionEditError(Exception):
    """Raised when a requested connection edit is invalid for the current file.

    Distinct from connections.py's ``ConnectionError`` so the CLI facade's
    star-imports never collide; the handler catches this and exits 2.

    It lives in its own module so that `runner` and `stored_secrets` can both
    raise it without importing each other.
    """
