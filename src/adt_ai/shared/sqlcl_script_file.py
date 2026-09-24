"""The throwaway file a SQLcl script travels in (ADT #148, #760, #923).

Split out of ``shared.sqlcl_script`` by ADT #923, to make room for the sweep
below, taking the one responsibility that module could hand over whole: where a
script is written, what it carries, and how long it may outlive the run that
wrote it. ``write_sqlcl_script`` is still importable from ``shared.sqlcl_script``,
which is where callers have always found it.

**A script carrying a password is written outside the project.** Every script
used to land in ``<project_root>/config/temp/``, which keeps a stray ``.sql`` away
from exported code and out of git, the reason that folder was chosen in June
2026. Neither helps a script whose connect line holds ``user/"password"``: the
registration run, or any run on a connection that is not saved by name. A project
is often a synced folder, so the file could be uploaded before the run ended and
kept by the sync client after the ``finally`` deleted it. And that ``finally`` was
the only cleanup there was: SIGTERM or SIGHUP ends the process without running
it, and the script stays behind with the password inside. So such a script now
goes to the operating system's temporary folder, named for the process that wrote
it, and every script written afterwards first removes the ones whose writer is
gone. A script without a password keeps its documented place under
``config/temp/``.

**A statement too long for one terminal line travels as a file.** ``SqlclSession``
types its statements into a pty, and a pty is line disciplined. Measured on
2026-09-23 against SQLcl 26.2.1 on ``sql -S /nolog``: a 1,023-byte line answered
in 0.05 s, a 1,024-byte one never did and the terminal took nothing after it,
while the same line through an ``@`` file answered intact. 1,024 bytes with the
newline is macOS's ``MAX_CANON``; Linux keeps 4,095. ``console_body`` hands such
a statement over the way every ``sqlcl_request`` script has crossed since #760.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.sqlcl_quoting import quote_sqlcl_argument

_TEMP_GITIGNORE_ENTRY = "config/temp/"

# Pulls the cleartext password out of a SQLcl ``connect`` line. The connect
# lines we build all embed it the same way (``user/"password"@dsn``) so we
# can recover it from the script we are about to run and scrub it from anything
# SQLcl echoes back into stdout/stderr.
_CONNECT_PWD_RE = re.compile(r'/"(?P<pwd>[^"\n]+)"@')

# What a script carrying a password is called, followed by the id of the process
# that wrote it, so a later sweep can tell a live run's script from a dead one's.
CREDENTIAL_SCRIPT_PREFIX = "adt-sqlcl-connect-"
_CREDENTIAL_SCRIPT_NAME = re.compile(
    rf"^{re.escape(CREDENTIAL_SCRIPT_PREFIX)}(?P<pid>[1-9][0-9]{{0,6}})-"
)

# How old a password script has to be before the sweep removes it, whoever wrote
# it. SQLcl opens its script within seconds of being handed it, and removing the
# file after that does not take it away from the run: POSIX keeps an open file
# readable, and Windows either refuses the delete or leaves the open handle
# readable. So this only has to outlast a JVM start. On Windows, where no process
# id can be asked about safely, it is the whole rule.
STALE_CREDENTIAL_SCRIPT_SECONDS = 600

# The longest line typed into a driven SQLcl's terminal, in bytes. Below macOS's
# 1,024-byte canonical line, which counts the newline; a longer line is dropped
# whole rather than cut, and the terminal takes nothing after it.
CONSOLE_LINE_BYTES = 1_000

# How much of an old ``config/temp/`` script is read to find a connect line. The
# connect block opens every script ADT.ai writes, a few hundred bytes in at most.
_HEAD_BYTES = 65_536


def _connect_secrets(script: str) -> set[str]:
    return {match.group("pwd") for match in _CONNECT_PWD_RE.finditer(script)}


def _ensure_temp_ignored(root: Path) -> None:
    """Idempotently ensure ``config/temp/`` is git-ignored in ``root``.

    Mirrors ``ensure_discovery_ignored`` for ``config/discovery/``, appends the
    entry to an existing ``.gitignore`` (fixing a missing trailing newline) or
    creates the file when absent.
    """
    gitignore = root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if _TEMP_GITIGNORE_ENTRY in {line.strip() for line in existing.splitlines()}:
        return
    prefix = existing
    if prefix and not prefix.endswith("\n"):
        prefix += "\n"
    text_files.write_text(gitignore, prefix + _TEMP_GITIGNORE_ENTRY + "\n")


def _sqlcl_temp_dir(project_root: Path | None) -> Path | None:
    """Return the gitignored scratch dir for throwaway SQLcl scripts.

    SQLcl ``@`` scripts are ephemeral; they must never land beside exported code
    in the project repo. When the project root is known, route them to
    ``<project_root>/config/temp/`` and ensure that folder is git-ignored
    (mirroring ``config/discovery/``). Otherwise fall back to the OS temp dir
    (``dir=None``) so the script still never touches the repo.
    """
    if project_root is None:
        return None
    temp_dir = project_root / "config" / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    _ensure_temp_ignored(project_root)
    return temp_dir


def write_sqlcl_script(script: str, project_root: Path | None) -> Path:
    """``script`` on disk as a throwaway ``.sql``, owner-only, for SQLcl to ``@``.

    Its own function because a reused SQLcl process needs exactly this file and
    none of the process handling around it (ADT #760): a pty is line disciplined
    and an exported package body is far past ``MAX_CANON``, so the body goes to
    SQLcl as a file there too and only its path crosses the terminal. Keeping one
    writer also keeps one answer about where the file lands and what it is
    chmod'ed to.

    A script whose connect line carries a password lands in the operating
    system's temporary folder instead of the project, and every call first sweeps
    the ones earlier runs left behind (ADT #923, see the module docstring).
    """
    _sweep_stale_password_scripts(project_root)
    carries_password = bool(_connect_secrets(script))
    with tempfile.NamedTemporaryFile(
        "w",
        encoding = "utf-8",
        newline  = "\n",
        prefix   = f"{CREDENTIAL_SCRIPT_PREFIX}{os.getpid()}-" if carries_password else None,
        suffix   = ".sql",
        dir      = None if carries_password else _sqlcl_temp_dir(project_root),
        delete   = False,
    ) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    # The script may embed a cleartext connect credential; pin owner-only perms
    # even if the platform's tempfile defaults ever differ from mkstemp's 0600.
    os.chmod(script_path, 0o600)
    return script_path


@contextlib.contextmanager
def console_body(body: str, project_root: Path | None) -> Iterator[str]:
    """``body`` as it can cross a terminal intact: itself, or ``@"<file>"`` holding it.

    Typed when every line fits ``CONSOLE_LINE_BYTES``, which is byte for byte
    what the driven session always typed. Otherwise the body is written as a
    throwaway script, exactly as a ``sqlcl_request`` script is, only its path
    crosses the terminal, and the file goes when the exchange ends, however it
    ends. Counted in bytes, because the terminal counts bytes.
    """
    lines = body.rstrip().split("\n")
    if all(len(line.encode("utf-8")) <= CONSOLE_LINE_BYTES for line in lines):
        yield body
        return
    path = write_sqlcl_script(body.rstrip() + "\n", project_root)
    try:
        yield "@" + quote_sqlcl_argument(path, role="SQLcl script path")
    finally:
        path.unlink(missing_ok=True)


def _sweep_stale_password_scripts(project_root: Path | None) -> None:
    """Remove the password scripts earlier runs left behind (ADT #923).

    A run's ``finally`` was the only thing that removed one, and SIGTERM, SIGHUP
    or a kill never runs it. A password script goes once the process that wrote
    it is gone, or once it is older than any run needs to hand it to SQLcl. Under
    ``config/temp/``, where every earlier version wrote them, only the age rule
    applies, and only to a script that carries a password. Best effort: a file
    another run or another user still owns stays where it is.
    """
    now = time.time()
    for path in Path(tempfile.gettempdir()).glob(f"{CREDENTIAL_SCRIPT_PREFIX}*.sql"):
        if _writer_is_gone(path) or _older_than_any_run(path, now):
            _remove(path)
    if project_root is None:
        return
    for path in (project_root / "config" / "temp").glob("*.sql"):
        if _older_than_any_run(path, now) and _carries_password(path):
            _remove(path)


def _writer_is_gone(path: Path) -> bool:
    match = _CREDENTIAL_SCRIPT_NAME.match(path.name)
    if match is None:
        return False
    pid = int(match.group("pid"))
    return pid != os.getpid() and not _process_exists(pid)


def _process_exists(pid: int) -> bool:
    """Whether process ``pid`` exists, asked only where asking is harmless.

    Signal 0 probes a POSIX process without touching it. On Windows ``os.kill``
    ends the process instead, so there every writer counts as running and the
    age rule alone decides.
    """
    if os.name == "nt":
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    return True


def _older_than_any_run(path: Path, now: float) -> bool:
    try:
        return now - path.stat().st_mtime > STALE_CREDENTIAL_SCRIPT_SECONDS
    except OSError:
        return False


def _carries_password(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError:
        return False
    return bool(_connect_secrets(head.decode("utf-8", "replace")))


def _remove(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()


__all__ = [
    "CONSOLE_LINE_BYTES",
    "CREDENTIAL_SCRIPT_PREFIX",
    "STALE_CREDENTIAL_SCRIPT_SECONDS",
    "console_body",
    "write_sqlcl_script",
]
