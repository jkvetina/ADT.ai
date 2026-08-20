"""Run a throwaway SQLcl script: temp file, owner-only perms, secret scrub.

Split out of ``shared.db`` (ADT #148) to keep both modules context-sized. The
public entry point stays re-exported from ``adt_ai.shared.db``.
"""

from __future__ import annotations

import contextlib
import os
import re
import select
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from adt_ai.shared import text_files

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

# Variables withheld from SQLcl's environment; ``_sqlcl_environment`` says why.
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


class SqlclNotConnectedError(RuntimeError):
    """SQLcl ran the whole script without ever holding a session."""


class SqlclTimeoutError(RuntimeError):
    """SQLcl outlived the deadline the caller gave it and was killed."""


class SqlclScriptError(RuntimeError):
    """SQLcl exited non-zero; the message is its whole captured transcript.

    Named so the CLI can tell it apart from an internal surprise. As a bare
    ``RuntimeError`` it landed under the ``UNEXPECTED ERROR:`` catch-all, which
    renders ``<type>: <message>`` on one line and so promoted the transcript's
    FIRST line to the diagnosis, reporting `Connection <name> has been deleted`
    (SQLcl echoing the script's own `CONNMGR DELETE` preamble) for a deploy that
    actually died on `SP2-0556` several lines later (ADT #271).
    """


def _ran_without_a_session(output: str) -> bool:
    return any(
        line.lstrip().startswith(SQLCL_NOT_CONNECTED_CODE)
        for line in output.splitlines()
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
    environment = dict(os.environ)
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
    database (ADT #434). Everything about the invocation stays here, so the two
    transports below cannot drift on the flags they pass.
    """
    return ["sql", *(["-L", "-oci"] if oci else []), "-S", "/nolog", f"@{script_path}"]


def _stream_on_pty(
    command: Sequence[str],
    root: Path,
    environment: Mapping[str, str],
    timeout_seconds: float | None,
    on_line: Callable[[str], None],
) -> tuple[str, int]:
    """Run ``command`` with its output on a pty, handing over each finished line.

    A pipe cannot serve a live reader, and that is measured rather than assumed:
    `shared/sqlcl_session.py` established it on 2026-08-19 against SQLcl 26.2 --
    "The JVM block-buffers stdout when it is not a terminal ... Measured: 60
    seconds, not one byte." So `patch -deploy` was never withholding progress, it
    had none to print, and moving a print statement could not have fixed it.

    Three mechanics come straight from that module, for the same reasons it gives:
    `TERM=dumb` plus the JLine property, or SQLcl writes a cursor-position query
    at startup and blocks until something answers it; terminal echo off, or the
    script comes back as its own output. The fourth is this function's own.
    **`stdin` stays `DEVNULL` while only stdout and stderr take the pty**: a
    failed CONNECT makes SQLcl fall back to prompting for a username, and on a
    terminal that prompt has nothing to end it, which is the hang ADT #188 fixed
    by pointing stdin at `DEVNULL` in the first place. `sqlcl_session` needs a
    writable stdin because it drives statements; a script run does not.

    Returns the transcript and the exit code, so the caller classifies a failure
    exactly as it does on the pipe path.
    """
    # POSIX-only, imported here rather than at module scope: this module is
    # imported by every command and only a live reader ever reaches the pty.
    import pty
    import termios

    environment = dict(environment)
    environment["TERM"] = "dumb"
    environment["JAVA_TOOL_OPTIONS"] = (
        environment.get("JAVA_TOOL_OPTIONS", "") + " -Dorg.jline.terminal.dumb=true"
    ).strip()

    master, slave = pty.openpty()
    attributes = termios.tcgetattr(slave)
    attributes[3] &= ~termios.ECHO
    termios.tcsetattr(slave, termios.TCSANOW, attributes)
    process = subprocess.Popen(
        list(command),
        cwd    = root,
        stdin  = subprocess.DEVNULL,
        stdout = slave,
        stderr = slave,
        env    = environment,
    )
    os.close(slave)

    collected: list[str] = []
    buffer = b""
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds

    def emit(raw: bytes) -> None:
        # A pty turns every `\n` into `\r\n`, so the marker has to come back off
        # or the transcript stops matching what the pipe path returned and every
        # parser reading it (`_deployment_succeeded`, the progress echoes) sees a
        # different string.
        line = raw.decode("utf-8", "replace").rstrip("\r")
        collected.append(line)
        on_line(line)

    try:
        while True:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                process.kill()
                process.wait()
                raise SqlclTimeoutError(
                    f"SQLcl did not finish within {timeout_seconds:g} seconds and was killed."
                    + (f"\n{chr(10).join(collected).strip()}" if collected else "")
                )
            if not select.select([master], [], [], remaining if remaining else 1.0)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                # The child closed its end. Linux reports EIO here where macOS
                # returns empty; both mean the same thing.
                chunk = b""
            if not chunk:
                break
            buffer += chunk
            *complete, buffer = buffer.split(b"\n")
            for raw in complete:
                emit(raw)
        if buffer.strip():
            emit(buffer)
    finally:
        with contextlib.suppress(OSError):
            os.close(master)
    return "\n".join(collected), process.wait()


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
            # A live reader, so the child goes on a pty. Every line is scrubbed
            # on the way out rather than at the end, because the callback prints
            # to the user's terminal and a credential SQLcl echoed would be on
            # screen long before the returned transcript was scrubbed.
            raw_output, returncode = _stream_on_pty(
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
            # ``stdin=DEVNULL`` is not cosmetic. A CONNECT that fails makes SQLcl
            # fall back to prompting for a username, and without this the child
            # inherits the caller's terminal, so it sat at a prompt that
            # ``capture_output`` had already swallowed, waiting forever while
            # ``export_apex -rest`` printed nothing but a crawling progress bar
            # (ADT #188). At EOF the prompt fails immediately instead, and the
            # ``WHENEVER SQLERROR EXIT FAILURE`` guard turns that into a real error.
            try:
                completed = subprocess.run(
                    command,
                    cwd            = root,
                    check          = False,
                    capture_output = True,
                    text           = True,
                    stdin          = subprocess.DEVNULL,
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
