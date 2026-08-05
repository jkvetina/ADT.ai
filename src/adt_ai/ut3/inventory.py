"""What the ut3 module discovered and what came back from running it.

Three record types, in the order the command prints them: a ``SuiteTest`` is one
``%test`` annotation, a ``SuitePackage`` is one ``_UT`` package with the tests it
holds (and, when it holds none that can run, the reason it was skipped), and a
``TestOutcome`` is one test's verdict after the suite ran.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# Skip reasons, in the order they are checked. **Neither reaches the console.**
# A `_UT` package that did not compile and a `_UT` package utPLSQL never parsed
# are both "not a runnable suite", and Jan's standing instruction is that `ut3`
# ignores those completely — no table row, no problem stanza, no effect on the
# verdict. They stay named rather than collapsing to a bare `runnable` flag
# because `-debug` and any future diagnostic still needs to say which it was.
SKIP_INVALID = "INVALID"
SKIP_NOT_A_SUITE = "NOT A SUITE"

RESULT_PASSED = "PASSED"
RESULT_FAILED = "FAILED"
# The status word a test row prints. A failed expectation and a raised exception
# are different things and stay counted apart, but both have to fit the same
# fixed-width status column, and `ERROR` is what the reader is looking for.
RESULT_ERRORED = "ERROR"
RESULT_SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class SuiteTest:
    package         : str
    name            : str
    description     : str = ""
    line            : int | None = None
    path            : str = ""
    disabled        : bool = False
    disabled_reason : str = ""


@dataclass(frozen=True)
class SuitePackage:
    name        : str
    status      : str = "VALID"
    description : str = ""
    tests       : tuple[SuiteTest, ...] = field(default_factory=tuple)
    skip_reason : str = ""

    @property
    def runnable(self) -> bool:
        return not self.skip_reason


@dataclass(frozen=True)
class TestOutcome:
    package : str
    test    : str
    result  : str
    seconds : float | None = None
    message : str = ""

    @property
    def ok(self) -> bool:
        return self.result in {RESULT_PASSED, RESULT_SKIPPED}


__all__ = [name for name in globals() if not name.startswith("__")]
