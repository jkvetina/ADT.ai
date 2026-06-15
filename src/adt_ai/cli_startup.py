from __future__ import annotations

import sys


def print_startup_failure(error: BaseException) -> None:
    """Print the ADT banner + a friendly message for import/startup failures.

    Kept self-contained: it must not depend on any heavy ``adt_ai`` import,
    because those imports are exactly what may have failed here. Honors
    ``-debug`` / ``--debug`` by re-raising the original traceback.
    """
    if "-debug" in sys.argv or "--debug" in sys.argv:
        raise error
    title = "APEX DEPLOYMENT TOOL: STARTUP ERROR"
    print(file=sys.stderr)
    print(title, file=sys.stderr)
    print("-" * len(title), file=sys.stderr)
    print(f"ADT.ai failed to start: {type(error).__name__}: {error}", file=sys.stderr)
    print("Run the same command with -debug to see the full traceback.", file=sys.stderr)
    print(file=sys.stderr)
