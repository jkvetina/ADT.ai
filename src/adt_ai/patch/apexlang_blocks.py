"""The JVM boundary an `ords:` import compiles its tree through (ADT #1071).

`ApexlangBlocks.java`, beside this file, asks SQLcl's own APEXlang transpiler
for the PL/SQL blocks a tree installs as, and this module is the one place that
starts it: Java's single-file source mode, against the jars of the SQLcl on
PATH, so the compiler is the one `validate` and `apex import` already run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from adt_ai.shared.subprocess_env import safe_subprocess_environment

HELPER = Path(__file__).with_name("ApexlangBlocks.java")

# The compiler's deployment key for the application alias, see the helper.
ALIAS_KEY = "1000:8"

# A tree compiles in seconds; this bounds a JVM that never comes back.
TRANSPILE_TIMEOUT_SECONDS = 600


class ApexlangCompileError(RuntimeError):
    """The tree did not compile; the message is the compiler's own report."""


def transpile_blocks(tree: Path, *, alias: str = "") -> list[str]:
    """The PL/SQL blocks SQLcl's compiler makes of ``tree``, in install order.

    ``alias`` is compiled in, the way SQLcl's `apex import -alias` does it.
    """
    lib = _sqlcl_lib()
    classpath = os.pathsep.join([str(lib / "*"), str(lib / "ext" / "*")])
    completed = subprocess.run(
        [
            _java(), "-cp", classpath, str(HELPER), str(tree),
            *([f"{ALIAS_KEY}={alias}"] if alias else []),
        ],
        capture_output = True,
        timeout        = TRANSPILE_TIMEOUT_SECONDS,
        env            = safe_subprocess_environment(),
        check          = False,
    )
    stderr = completed.stderr.decode("utf-8", "replace").strip()
    if completed.returncode == 2:
        raise ApexlangCompileError(stderr)
    if completed.returncode != 0:
        raise RuntimeError(
            f"ApexlangBlocks.java exited {completed.returncode}. Running it needs a "
            "Java development kit, since it starts in single-file source mode.\n"
            + stderr[-2000:]
        )
    text = completed.stdout.decode("utf-8")
    return [block for block in text.split("\0") if block.strip()]


def _sqlcl_lib() -> Path:
    """The `lib` folder of the SQLcl on PATH, the one every other command runs."""
    launcher = shutil.which("sql")
    if launcher is None:
        raise RuntimeError(
            "SQLcl is not on PATH, and an ords import compiles the tree with its jars."
        )
    return Path(launcher).resolve().parent.parent / "lib"


def _java() -> str:
    home = os.environ.get("JAVA_HOME")
    if home and (Path(home) / "bin" / "java").exists():
        return str(Path(home) / "bin" / "java")
    return "java"


__all__ = ["ALIAS_KEY", "HELPER", "ApexlangCompileError", "transpile_blocks"]
