from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

CommandRunner = Callable[[Sequence[str]], str]
ExecutableResolver = Callable[[str], str | None]


@dataclass(frozen=True)
class CheckResult:
    name  : str
    status: str
    detail: str


class EnvironmentChecker:
    adt_variables = (
        "ADT_REPO",
        "ADT_CLIENT",
        "ADT_PROJECT",
        "ADT_ENV",
        "ADT_BRANCH",
        "ADT_SCHEMA",
        "ADT_KEY",
    )

    def __init__(
        self,
        command_runner     : CommandRunner | None = None,
        executable_resolver: ExecutableResolver | None = None,
        env                : Mapping[str, str] | None = None,
        python_version     : str | None = None,
        oracledb_version   : str | None = None,
        oracle_mode        : str | None = None,
        instant_client     : str | None = None,
    ) -> None:
        self.command_runner      = command_runner or _run_command
        self.executable_resolver = executable_resolver or shutil.which
        self.env                 = env or os.environ
        self.python_version      = python_version or sys.version.split()[0]
        self.oracledb_version    = oracledb_version
        self.oracle_mode         = oracle_mode
        self.instant_client      = instant_client

    def run(self) -> list[CheckResult]:
        return [
            CheckResult("Python", "OK", self.python_version),
            self._command_check("Git", "git", ["git", "--version"], required=True),
            self._command_check("Java", "java", ["java", "--version"], required=False),
            self._sqlcl_check(),
            self._oracledb_check(),
            self._oracle_mode_check(),
            self._instant_client_check(),
            self._java_tool_options_check(),
            self._adt_variables_check(),
        ]

    def _command_check(
        self,
        name    : str,
        command : str,
        args    : list[str],
        required: bool,
    ) -> CheckResult:
        if not self.executable_resolver(command):
            return CheckResult(
                name,
                "FAIL" if required else "WARN",
                f"{command} not found on PATH",
            )
        try:
            return CheckResult(name, "OK", _first_line(self.command_runner(args)))
        except Exception as error:
            return CheckResult(
                name,
                "FAIL" if required else "WARN",
                str(error) or f"{command} failed",
            )

    def _sqlcl_check(self) -> CheckResult:
        result = self._command_check("SQLcl", "sql", ["sql", "-V"], required=False)
        if result.status != "OK":
            return result
        for line in result.detail.splitlines():
            if line.startswith("SQLcl:"):
                return CheckResult("SQLcl", "OK", line.split()[2])
        return CheckResult("SQLcl", "OK", result.detail)

    def _oracledb_check(self) -> CheckResult:
        version = self.oracledb_version
        if version is None:
            try:
                version = str(importlib.import_module("oracledb").__version__)
            except Exception as error:
                return CheckResult(
                    "Oracle DB module", "FAIL", str(error) or "oracledb not installed"
                )
        if not version:
            return CheckResult("Oracle DB module", "FAIL", "oracledb not installed")
        return CheckResult("Oracle DB module", "OK", f"oracledb {version}")

    def _oracle_mode_check(self) -> CheckResult:
        mode = self.oracle_mode
        if mode is None:
            try:
                oracledb = importlib.import_module("oracledb")
                mode = "thin" if oracledb.is_thin_mode() else "thick"
            except Exception:
                mode = ""
        if not mode:
            return CheckResult("Oracle mode", "WARN", "unknown")
        return CheckResult("Oracle mode", "OK", mode)

    def _instant_client_check(self) -> CheckResult:
        version = self.instant_client
        if version is None:
            version = _instant_client_version(self.env)
        if version:
            return CheckResult("Instant Client", "OK", version)
        return CheckResult("Instant Client", "WARN", "not found; required only for thick mode")

    def _java_tool_options_check(self) -> CheckResult:
        value = self.env.get("JAVA_TOOL_OPTIONS", "")
        if "-Duser.language=en" not in value:
            return CheckResult("JAVA_TOOL_OPTIONS", "WARN", "missing -Duser.language=en")
        return CheckResult("JAVA_TOOL_OPTIONS", "OK", value)

    def _adt_variables_check(self) -> CheckResult:
        configured = [
            name
            for name in self.adt_variables
            if self.env.get(name)
        ]
        if not configured:
            return CheckResult("ADT variables", "INFO", "none set")
        return CheckResult("ADT variables", "INFO", ", ".join(configured))


def _run_command(command: Sequence[str]) -> str:
    completed = subprocess.run(
        command,
        check        = True,
        capture_output = True,
        text         = True,
    )
    return (completed.stdout or completed.stderr).strip()


def _first_line(text: str) -> str:
    lines = text.splitlines()
    return lines[0] if lines else ""


def _instant_client_version(env: Mapping[str, str]) -> str:
    oracle_home = env.get("ORACLE_HOME")
    if not oracle_home:
        return ""
    readme = Path(oracle_home) / "BASIC_README"
    if not readme.exists():
        return ""
    for line in readme.read_text(encoding="utf-8", errors="ignore").splitlines():
        if "Client Shared Library" in line and " - " in line:
            return line.split(" - ", 1)[1].strip()
    return ""
