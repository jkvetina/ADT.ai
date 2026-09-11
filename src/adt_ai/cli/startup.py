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
    # Spelled out rather than imported: this path exists precisely for the case
    # where importing adt_ai is what failed, so it must not reach for
    # shared.progress or shared.error_screen. It is therefore the one hand-kept
    # copy of the shared shapes, and `tests/cli/test_error_family_call_sites.py`
    # asserts the copy still renders what the real renderers would (ADT #764).
    banner = "APEX DEPLOYMENT TOOL"
    header = "ERROR - STARTUP FAILED:"
    print(file=sys.stderr)
    print(banner, file=sys.stderr)
    print("-" * len(banner), file=sys.stderr)
    print(file=sys.stderr)
    print(header, file=sys.stderr)
    print("-" * len(header), file=sys.stderr)
    print(f"  ADT.ai failed to start: {type(error).__name__}: {error}", file=sys.stderr)
    print(file=sys.stderr)
    print("  Use -debug to show the Python traceback.", file=sys.stderr)
    print(file=sys.stderr)
