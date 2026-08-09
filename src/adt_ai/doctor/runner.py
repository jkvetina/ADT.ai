from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from adt_ai.doctor._base import (
    ADT_AI_GITHUB_LATEST_RELEASE_URL,
    ActionReporter,
    DoctorRequest,
    DoctorResult,
    SqlclRelease,
    _certificate_error,
    _is_certificate_error,
    format_action_line,
    format_status_line,
)
from adt_ai.doctor.init import DoctorInitMixin
from adt_ai.doctor.upgrade import DoctorUpgradeMixin
from adt_ai.doctor.version_check import DoctorVersionMixin

__all__ = [
    "DoctorRequest",
    "DoctorResult",
    "DoctorRunner",
    "ActionReporter",
    "format_action_line",
    "ADT_AI_GITHUB_LATEST_RELEASE_URL",
]

# Doctor stays project-config-free by design — it must diagnose broken setups
# before any config exists — so its network timeouts are constants, not keys.
_FETCH_TIMEOUT_SECONDS = 30
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _run_command(
    command: Sequence[str],
    cwd    : Path | None = None,
    env    : Mapping[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd            = cwd,
        env            = env,
        check          = True,
        capture_output = True,
        text           = True,
    )
    return (completed.stdout or completed.stderr).strip()


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ADT.ai doctor"})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        if _is_certificate_error(error):
            raise _certificate_error(url, error) from error
        raise


def _download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ADT.ai doctor"})
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            target.write_bytes(response.read())
    except urllib.error.URLError as error:
        if _is_certificate_error(error):
            raise _certificate_error(url, error) from error
        raise


class DoctorRunner(DoctorVersionMixin, DoctorUpgradeMixin, DoctorInitMixin):
    def __init__(
        self,
        command_runner     : object = None,
        executable_resolver: object = None,
        env                : Mapping[str, str] | None = None,
        package_version    : str = "",
        oracledb_version   : str | None = None,
        oracle_mode        : str | None = None,
        instant_client     : str | None = None,
        package_root       : Path | None = None,
        oracle_page_fetcher: object = None,
        file_downloader    : object = None,
        latest_version_fetcher: object = None,
        line_callback      : object = None,
        action_reporter    : ActionReporter | None = None,
        version_cache_dir  : Path | None = None,
        version_cache_ttl  : float = 3600,
    ) -> None:
        self.command_runner      = command_runner or _run_command
        self.executable_resolver = executable_resolver or shutil.which
        self.env                 = env or os.environ
        self.package_version     = package_version
        self.oracledb_version    = oracledb_version
        self.oracle_mode         = oracle_mode
        self.instant_client      = instant_client
        self.package_root        = package_root or Path(__file__).resolve().parents[3]
        self.oracle_page_fetcher = oracle_page_fetcher or _fetch_text
        self.file_downloader     = file_downloader or _download_file
        self.latest_version_fetcher = latest_version_fetcher
        self.line_callback       = line_callback
        self.action_reporter     = action_reporter
        # When set, latest-version lookups are memoized to disk with a short TTL so
        # repeated `doctor` runs within the window skip the network entirely. Left
        # unset (None) by tests, which inject deterministic fetchers instead.
        self.version_cache_dir   = version_cache_dir
        self.version_cache_ttl   = version_cache_ttl

    def run(self, request: DoctorRequest) -> DoctorResult:
        if request.init:
            return self._init_project(request)

        lines: list[str] = []
        check_results = self._check_results()
        self._extend(
            lines,
            self._version_lines(
                check_results,
                online=not request.offline and not request.update and not request.sqlcl,
            ),
        )
        self._add(lines, "")
        self._extend(lines, self._environment_lines(check_results))
        performed_actions: list[str] = []
        exit_code = 1 if any(result.status == "FAIL" for result in check_results) else 0

        if request.sqlcl:
            self._begin_actions_section(lines)
            sqlcl_result = self._upgrade_sqlcl(lines)
            performed_actions.extend(sqlcl_result.performed_actions)
            exit_code = max(exit_code, sqlcl_result.exit_code)
            return DoctorResult(lines, performed_actions, exit_code)

        if not request.update:
            # Read-only run: the section exists to offer upgrades, so with
            # nothing stale it is not an empty section — it is no section.
            action_lines = self._status_action_lines()
            if action_lines:
                self._begin_actions_section(lines)
                self._extend(lines, action_lines)
            return DoctorResult(lines, performed_actions, exit_code)

        self._begin_actions_section(lines)
        for action in (
            self._upgrade_adt_ai,
            self._install_requirements,
            self._upgrade_sqlcl,
        ):
            action_result = action(lines)
            performed_actions.extend(action_result.performed_actions)
            exit_code = max(exit_code, action_result.exit_code)

        return DoctorResult(lines, performed_actions, exit_code)

    def _begin_actions_section(self, lines: list[str]) -> None:
        """Open `ACTIONS:` with the blank line that separates it from ENVIRONMENT.

        Header and separator move together so a run with nothing to offer leaves
        neither behind — the shared timer footer then owns the trailing spacing.
        """
        self._add(lines, "")
        self._add(lines, "ACTIONS:")

    def _command_env(self) -> dict[str, str]:
        env = dict(self.env)
        java_options = env.get("JAVA_TOOL_OPTIONS", "").strip()
        if "-Duser.language=en" not in java_options:
            java_options = f"{java_options} -Duser.language=en".strip()
        env["JAVA_TOOL_OPTIONS"] = java_options
        env["LANG"] = "en_US.UTF-8"
        env["NLS_LANG"] = "AMERICAN_AMERICA.AL32UTF8"
        env["PYTHONIOENCODING"] = "utf-8"
        env["SQLCL_HOME"] = str(self._sqlcl_home())
        env.pop("LC_MESSAGES", None)
        env.pop("LC_ALL", None)
        env.pop("PYTHONUTF8", None)
        return env

    def _begin_action(self, label: str) -> None:
        if self.action_reporter:
            self.action_reporter.begin(label)

    def _end_action(self, lines: list[str], label: str, outcome: str) -> str:
        line = format_action_line(label, outcome)
        lines.append(line)
        if self.action_reporter:
            self.action_reporter.end(label, outcome)
        elif self.line_callback:
            self.line_callback(line)
        return line

    def _fail_action(self, lines: list[str], label: str, detail: str) -> DoctorResult:
        self._end_action(lines, label, "FAILED")
        if detail:
            self._add(lines, f"    {detail}")
        return DoctorResult(lines=[], performed_actions=[], exit_code=1)

    def _add(self, lines: list[str], line: str) -> None:
        lines.append(line)
        if self.line_callback:
            self.line_callback(line)

    def _extend(self, lines: list[str], new_lines: Iterable[str]) -> None:
        for line in new_lines:
            self._add(lines, line)


# Re-export names used by other modules / tests but defined in _base:
# SqlclRelease and format_status_line are not in __all__ but may be used indirectly.
_reexport = (SqlclRelease, format_status_line)
