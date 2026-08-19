"""One credential, masked everywhere except where a driver needs the plaintext.

``Connection`` used to carry its two passwords as plain ``str`` on a frozen
dataclass, so the generated ``repr`` printed both in cleartext. Nothing in the
tree printed one, and nothing stopped it either: a ``print(connection)`` in a
debug branch, an f-string in an error message, or pytest ``--showlocals`` on a
test holding a resolved connection puts a database password on stdout, which is
the stream an agent reads back. That is the gap this type closes, and it closes
it structurally rather than by care: the plaintext has exactly one exit,
``reveal()``, and ``tests/contracts/test_secret_reveal_sites.py`` pins the three
call sites allowed to use it.

What it does not do is hide anything from an agent that can run ``adtai`` at all,
because such an agent can simply open its own session. The claim ADT.ai makes is
narrower and worth keeping honest: no plaintext credential reaches stdout, a log,
a generated script, or a repr.
"""

from __future__ import annotations

MASK = "***"


class Secret:
    """A credential that renders as ``***`` and yields plaintext only on request.

    Construction accepts ``str``, ``bytes``, ``None``, or another ``Secret``, so
    every caller can hand over whatever the connection file gave it. ``bytes`` is
    the ``pwd: !!binary`` YAML case: embedding those raw in a connect string
    would write the literal ``b'secret'``, so they are decoded here, at the one
    place that owns the value, rather than at each consumer.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str | bytes | Secret | None = None) -> None:
        if isinstance(value, Secret):
            value = value._value
        elif isinstance(value, bytes):
            value = value.decode("utf-8")
        self._value: str | None = value

    def reveal(self) -> str | None:
        """The plaintext. Every call site is on the approved contract list."""
        return self._value

    def __bool__(self) -> bool:
        """Absent and empty both read as absent.

        ``db.py`` gates its ``wallet_password`` connect kwarg on truthiness, so a
        connection with no wallet password has to be falsy rather than a present
        wrapper around ``None``.
        """
        return bool(self._value)

    def __repr__(self) -> str:
        return MASK

    __str__ = __repr__

    def __eq__(self, other: object) -> bool:
        """Compare two secrets, never a secret against a bare string.

        Returning ``NotImplemented`` for anything else keeps ``Secret("a") == "a"``
        false on purpose: an equality that quietly accepts plaintext is the shape
        that invites treating the wrapper as the string it hides.
        """
        if isinstance(other, Secret):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self._value)
