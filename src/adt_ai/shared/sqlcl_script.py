"""Run a throwaway SQLcl script: temp file, owner-only perms, secret scrub.

Split out of ``shared.db`` (ADT #148) to keep both modules context-sized. The
public entry point stays re-exported from ``adt_ai.shared.db``.

**Windows needs a pipe on stdin, and that is measured** (ADT #457). The
reporting customer ran the same script both ways against his own SQLcl install
on 2026-08-21: with ``stdin`` pointed at ``DEVNULL``, which is the ``NUL``
device there, SQLcl died inside its own console builder with
``java.io.IOException: Fonction incorrecte`` and exited **0**, having never run
a line; with an empty closed pipe the script ran and returned its output.
``SqlclConsole.<init>`` calls ``available()`` on stdin at startup and ``NUL``
does not answer that syscall, while a pipe answers on every platform. So the
non-streaming path below hands SQLcl an empty closed pipe, which keeps ADT
#188's property (the child never inherits the caller's terminal, and a fallback
username prompt hits EOF immediately) and drops the device.

The measurement is his, on hardware ADT.ai has none of. What is verified here
is the platform-neutral property that fails there: SQLcl's stdin is a pipe and
is at EOF, pinned by ``tests/shared/test_sqlcl_script_windows.py``.

The live-reader transports moved to ``shared.sqlcl_stream`` in the same card,
when Windows gained a second one and this module went over the context-size
guard; the error classes moved to ``shared.sqlcl_errors`` so that module can
raise a timeout without an import cycle. Both are re-exported here, which is
where callers have always found them.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.sqlcl_errors import (
    SqlclNotConnectedError,
    SqlclScriptError,
    SqlclTimeoutError,
)
from adt_ai.shared.sqlcl_stream import open_stream
from adt_ai.shared.subprocess_env import safe_subprocess_environment

# Re-exported: every caller has always imported these from here, and the split
# into `sqlcl_errors` (ADT #457) is about an import cycle, not about moving the
# public surface.
__all__ = [
    "SQLCL_HIDDEN_VARIABLES",
    "SQLCL_NOT_CONNECTED_CODE",
    "SqlclNotConnectedError",
    "SqlclScriptError",
    "SqlclTimeoutError",
    "run_sqlcl_script",
]

_TEMP_GITIGNORE_ENTRY = "config/temp/"


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


# Pulls the cleartext password out of a SQLcl ``connect`` line. The connect
# lines we build all embed it the same way (``user/"password"@dsn``) so we
# can recover it from the script we are about to run and scrub it from anything
# SQLcl echoes back into stdout/stderr.
_CONNECT_PWD_RE = re.compile(r'/"(?P<pwd>[^"\n]+)"@')

# Variables withheld from ordinary SQLcl sessions. ADT's encryption-key names
# are removed for OCI and thin sessions alike by ``safe_subprocess_environment``.
SQLCL_HIDDEN_VARIABLES = ("ORACLE_HOME",)

# The one diagnostic that means "this script ran against no session at all".
#
# ``WHENEVER SQLERROR EXIT FAILURE`` guards every connect block (ADT #188), but
# it can only trap *SQL* errors. ``SP2-0640`` is a client-level SP2- message, so
# a connect that left no session behind does not fire the guard: SQLcl runs the
# rest of the script, answers every statement with ``SP2-0640: Not connected``,
# and exits **0**. The exit code alone therefore cannot see this failure, and
# ``run_sqlcl_script`` handed the dead transcript back as success, which is how
# ``export_apex -rest`` reported ``SP2-0640`` as its own error three escalations
# running while the connect diagnostic above it was discarded (ADT #232).
SQLCL_NOT_CONNECTED_CODE = "SP2-0640"


def _ran_without_a_session(output: str) -> bool:
    return any(
        line.lstrip().startswith(SQLCL_NOT_CONNECTED_CODE)
        for line in output.splitlines()
    )


# The second way a run exits 0 having done nothing: it never reached SQLcl at
# all. A JVM that dies in startup prints a stack trace and exits 0, so neither
# the exit code nor the ``SP2-0640`` check above sees it, and the trace was
# handed back as the transcript. That is how ``export_apex -rest`` reported
# ``Rest output: java.io.IOException...`` under a progress bar at 67% on the
# customer's Windows machine (ADT #457).
#
# **The condition is the whole transcript, never a substring.** A legitimate
# transcript may quote stack-trace text: an exported package body, a spooled
# log, a row whose value contains it. So a run is only a startup failure when
# it carries a Java failure line AND produced nothing else at all.
_JAVA_FAILURE = re.compile(
    r"^\s*(?:Exception in thread|Caused by:|[a-z][\w.]*\.[A-Z]\w*(?:Exception|Error))\b"
)
_STACK_FRAME = re.compile(r"^\s*at\s+\S+\(")
# The JVM's own notice when JAVA_TOOL_OPTIONS is set, which ADT.ai sets itself.
_JVM_NOTICE = re.compile(r"^\s*Picked up [A-Z_]+:")


def _died_before_the_script_ran(output: str) -> bool:
    lines = [line for line in output.splitlines() if line.strip()]
    if not lines or not any(_JAVA_FAILURE.match(line) for line in lines):
        return False
    return all(
        _JAVA_FAILURE.match(line) or _STACK_FRAME.match(line) or _JVM_NOTICE.match(line)
        for line in lines
    )


def _sqlcl_environment(
    oci: bool = False,
    client_lib_dir: str | None = None,
    tns_admin: str | None = None,
) -> dict[str, str]:
    """The process environment, minus what would flip SQLcl to the thick driver.

    SQLcl is a Java program and ADT.ai only ever asks it for JDBC *thin*, see
    ``shared.env_bootstrap``. Its launcher disagrees: an ``ORACLE_HOME`` holding
    an ``ocijdbc`` library is read as "the thick driver is available", so it
    front-loads that client's ``ojdbc11.jar`` and hands the JVM the OCI
    libraries through ``DYLD_LIBRARY_PATH``. The connect URL then comes out as
    ``jdbc:oracle:oci8:@host:port/service``.

    On macOS that URL can never be satisfied. SIP strips ``DYLD_*`` when
    exec'ing a protected binary, and both the launcher (``/bin/bash``) and the
    JVM (``/usr/bin/java``) are protected, so the library path the launcher
    exports is gone before Java starts and every connect dies with
    ``no ocijdbc23 in java.library.path``, whatever the client version is.
    ADT.ai itself creates the condition: it exports ``ORACLE_HOME`` for
    python-oracledb's thick mode, a different process that is unaffected.

    Withholding the variable from this one child restores the thin driver ADT
    documents. ``PATH`` is untouched, so the launcher is still found, and the
    parent environment is not mutated, so python-oracledb keeps its client.
    """
    environment = safe_subprocess_environment()
    if oci:
        # The one connection shape that needs the opposite of everything above:
        # a Secure External Password Store is read by the OCI client, so SQLcl
        # has to find that client and therefore needs `ORACLE_HOME` present
        # (ADT #395). Decided per connection, off the auth mode, never as a
        # global switch, because every ordinary connection still runs thin and
        # would break exactly the way the docstring above describes.
        #
        # `ORACLE_HOME` alone is not enough on macOS, and the reason is the same
        # SIP behaviour: the launcher exports the client into
        # `DYLD_LIBRARY_PATH`, which is stripped before the JVM starts, so the
        # `jdbc:oracle:oci8:` URL it then builds dies on
        # `no ocijdbc23 in java.library.path`. `JAVA_TOOL_OPTIONS` is read by the
        # JVM itself rather than by dyld, so pointing `java.library.path` at the
        # client directory is what actually puts `libocijdbc23.dylib` in reach.
        # And the alias has to be findable: the OCI client resolves it through
        # `TNS_ADMIN`, which defaults to the client's own `network/admin` folder,
        # not to the wallet the connection names. Without this the driver loads
        # and then reports ORA-12154 against a tnsnames.ora nobody wrote.
        if tns_admin:
            environment["TNS_ADMIN"] = tns_admin
        client = client_lib_dir or environment.get("ORACLE_HOME")
        if client:
            environment["ORACLE_HOME"] = environment.get("ORACLE_HOME") or client
            options = environment.get("JAVA_TOOL_OPTIONS", "")
            environment["JAVA_TOOL_OPTIONS"] = (
                f"{options} -Djava.library.path={client}".strip()
            )
        return environment
    for name in SQLCL_HIDDEN_VARIABLES:
        environment.pop(name, None)
    return environment


def _connect_secrets(script: str) -> set[str]:
    return {match.group("pwd") for match in _CONNECT_PWD_RE.finditer(script)}


def _scrub_secrets(text: str, secrets: set[str]) -> str:
    """Replace any captured connect-line password with ``***``.

    SQLcl may echo the ``connect`` command into its captured output, which we
    both return to callers and embed into ``RuntimeError`` messages and on-disk
    deployment logs. Eliding the password keeps cleartext credentials out of all
    three sinks.
    """
    for secret in secrets:
        if secret:
            text = text.replace(secret, "***")
    return text


def _sqlcl_command(script_path: Path, oci: bool) -> list[str]:
    """The argv SQLcl is launched with.

    Its own function so a test can stand a different child in front of the
    transport, which is the only way to prove the streaming half without a
    database (ADT #434). Everything about the invocation stays here, so the
    three paths out of `run_sqlcl_script` cannot drift on the flags they pass.
    """
    return ["sql", *(["-L", "-oci"] if oci else []), "-S", "/nolog", f"@{script_path}"]


def run_sqlcl_script(
    script: str,
    root: Path,
    project_root: Path | None = None,
    timeout_seconds: float | None = None,
    oci: bool = False,
    client_lib_dir: str | None = None,
    tns_admin: str | None = None,
    on_line: Callable[[str], None] | None = None,
) -> str:
    """Run ``script`` through SQLcl and return its captured, scrubbed output.

    ``timeout_seconds`` bounds the child; ``None`` (the default) leaves it
    unbounded, which is what ``patch -deploy`` and ``diff`` need. Only the REST
    export passes a deadline today.

    ``on_line`` is a live reader (ADT #434). Passing one moves the child onto a
    pty so its output arrives line by line instead of in one block at exit; the
    callback sees each line as it lands and the whole transcript still comes back
    as the return value. **Without one nothing changes**: the `subprocess.run`
    pipe below is the same call `diff`, `validate` and `export_apex -rest` have
    always made, so this card cannot alter how they talk to SQLcl.
    """
    root.mkdir(parents=True, exist_ok=True)
    secrets = _connect_secrets(script)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding = "utf-8",
        newline  = "\n",
        suffix   = ".sql",
        dir      = _sqlcl_temp_dir(project_root),
        delete   = False,
    ) as handle:
        handle.write(script)
        script_path = Path(handle.name)
    # The script may embed a cleartext connect credential; pin owner-only perms
    # even if the platform's tempfile defaults ever differ from mkstemp's 0600.
    os.chmod(script_path, 0o600)
    command = _sqlcl_command(script_path, oci)
    environment = _sqlcl_environment(oci, client_lib_dir, tns_admin)
    try:
        if on_line is not None:
            # A live reader, so the child goes on this platform's streaming
            # transport. Every line is scrubbed on the way out rather than at
            # the end, because the callback prints to the user's terminal and a
            # credential SQLcl echoed would be on screen long before the
            # returned transcript was scrubbed.
            raw_output, returncode = open_stream(
                command,
                root,
                environment,
                timeout_seconds,
                lambda line: on_line(_scrub_secrets(line, secrets)),
            )
        else:
            # ``-S`` (silent) suppresses the banner and command echo so SQLcl does
            # not print the connect line in the first place; the scrub below is
            # the belt-and-braces backstop for the cases where it still does.
            #
            # Refusing the child the caller's stdin is not cosmetic. A CONNECT
            # that fails makes SQLcl fall back to prompting for a username, and
            # without this the child inherits the caller's terminal, so it sat
            # at a prompt that ``capture_output`` had already swallowed, waiting
            # forever while ``export_apex -rest`` printed nothing but a crawling
            # progress bar (ADT #188). At EOF the prompt fails immediately
            # instead, and ``WHENEVER SQLERROR EXIT FAILURE`` turns that into a
            # real error.
            #
            # ``input=""`` and not ``stdin=DEVNULL``, which is the same property
            # through a pipe rather than a device (ADT #457). Both are at EOF at
            # once and neither is the caller's terminal, so nothing about #188
            # changes; what changes is that SQLcl can ask this handle how many
            # bytes are available. ``NUL`` cannot answer that and the whole run
            # died there on Windows. See the module docstring for the
            # measurement.
            try:
                completed = subprocess.run(
                    command,
                    cwd            = root,
                    check          = False,
                    capture_output = True,
                    text           = True,
                    input          = "",
                    env            = environment,
                    timeout        = timeout_seconds,
                )
            except subprocess.TimeoutExpired as expired:
                # `subprocess.run` kills the child before re-raising, so nothing
                # is left running. Whatever it managed to say first is the only
                # clue about where it stalled, so it rides the error rather than
                # being dropped with the process.
                partial = _scrub_secrets(
                    _decode(expired.stdout) + _decode(expired.stderr), secrets
                ).strip()
                raise SqlclTimeoutError(
                    f"SQLcl did not finish within {timeout_seconds:g} seconds and was killed."
                    + (f"\n{partial}" if partial else "")
                ) from expired
            raw_output = (completed.stdout or "") + (completed.stderr or "")
            returncode = completed.returncode
    finally:
        script_path.unlink(missing_ok=True)
    output = _scrub_secrets(raw_output, secrets)
    if _died_before_the_script_ran(output):
        # Exit code 0 with a JVM stack trace and nothing else: SQLcl never
        # started, so the transcript is the failure rather than the answer. It
        # used to be returned, and `_write_rest_export` wrote it into the REST
        # export (ADT #457). Reported in full for the same reason the
        # not-connected error is: the cause is one of these lines.
        raise SqlclScriptError(
            "SQLcl failed before it ran the script "
            f"(exit code {returncode}). Full SQLcl output:\n{output.strip()}"
        )
    if _ran_without_a_session(output):
        # Reported in full, not as the one line a regex picked: the cause is
        # always some earlier line in this same transcript, and asking the user
        # to rerun with -debug to see it is not a diagnosis (Jan, 2026-08-07).
        # The output is already secret-scrubbed above.
        raise SqlclNotConnectedError(
            "SQLcl ran the script without a connected session "
            f"(exit code {returncode}). Full SQLcl output:\n{output.strip()}"
        )
    if returncode != 0:
        raise SqlclScriptError(output.strip() or f"SQLcl failed with exit code {returncode}")
    return output


def _decode(stream: str | bytes | None) -> str:
    if stream is None:
        return ""
    return stream if isinstance(stream, str) else stream.decode("utf-8", "replace")
