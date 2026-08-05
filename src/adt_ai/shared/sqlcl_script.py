"""Run a throwaway SQLcl script: temp file, owner-only perms, secret scrub.

Split out of ``shared.db`` (ADT #148) to keep both modules context-sized. The
public entry point stays re-exported from ``adt_ai.shared.db``.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

from adt_ai.shared import text_files

_TEMP_GITIGNORE_ENTRY = "config/temp/"


def _ensure_temp_ignored(root: Path) -> None:
    """Idempotently ensure ``config/temp/`` is git-ignored in ``root``.

    Mirrors ``ensure_discovery_ignored`` for ``config/discovery/`` — appends the
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
# lines we build all embed it the same way -- ``user/"password"@dsn`` -- so we
# can recover it from the script we are about to run and scrub it from anything
# SQLcl echoes back into stdout/stderr.
_CONNECT_PWD_RE = re.compile(r'/"(?P<pwd>[^"\n]+)"@')

# Variables withheld from SQLcl's environment; ``_sqlcl_environment`` says why.
SQLCL_HIDDEN_VARIABLES = ("ORACLE_HOME",)


def _sqlcl_environment() -> dict[str, str]:
    """The process environment, minus what would flip SQLcl to the thick driver.

    SQLcl is a Java program and ADT.ai only ever asks it for JDBC *thin* -- see
    ``shared.env_bootstrap``. Its launcher disagrees: an ``ORACLE_HOME`` holding
    an ``ocijdbc`` library is read as "the thick driver is available", so it
    front-loads that client's ``ojdbc11.jar`` and hands the JVM the OCI
    libraries through ``DYLD_LIBRARY_PATH``. The connect URL then comes out as
    ``jdbc:oracle:oci8:@host:port/service``.

    On macOS that URL can never be satisfied. SIP strips ``DYLD_*`` when
    exec'ing a protected binary, and both the launcher (``/bin/bash``) and the
    JVM (``/usr/bin/java``) are protected -- so the library path the launcher
    exports is gone before Java starts and every connect dies with
    ``no ocijdbc23 in java.library.path``, whatever the client version is.
    ADT.ai itself creates the condition: it exports ``ORACLE_HOME`` for
    python-oracledb's thick mode, a different process that is unaffected.

    Withholding the variable from this one child restores the thin driver ADT
    documents. ``PATH`` is untouched, so the launcher is still found, and the
    parent environment is not mutated, so python-oracledb keeps its client.
    """
    environment = dict(os.environ)
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


def run_sqlcl_script(script: str, root: Path, project_root: Path | None = None) -> str:
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
    try:
        # ``-S`` (silent) suppresses the banner and command echo so SQLcl does not
        # print the connect line in the first place; the scrub below is the
        # belt-and-braces backstop for the cases where it still does.
        #
        # ``stdin=DEVNULL`` is not cosmetic. A CONNECT that fails makes SQLcl
        # fall back to prompting for a username, and without this the child
        # inherits the caller's terminal — so it sat at a prompt that
        # ``capture_output`` had already swallowed, waiting forever while
        # ``export_apex -rest`` printed nothing but a crawling progress bar
        # (ADT #188). At EOF the prompt fails immediately instead, and the
        # ``WHENEVER SQLERROR EXIT FAILURE`` guard turns that into a real error.
        completed = subprocess.run(
            ["sql", "-S", "/nolog", f"@{script_path}"],
            cwd            = root,
            check          = False,
            capture_output = True,
            text           = True,
            stdin          = subprocess.DEVNULL,
            env            = _sqlcl_environment(),
        )
    finally:
        script_path.unlink(missing_ok=True)
    output = _scrub_secrets((completed.stdout or "") + (completed.stderr or ""), secrets)
    if completed.returncode != 0:
        raise RuntimeError(
            output.strip() or f"SQLcl failed with exit code {completed.returncode}"
        )
    return output
