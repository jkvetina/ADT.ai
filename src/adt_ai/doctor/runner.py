from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterable, Mapping, Sequence
from functools import partial
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import cast

from adt_ai.doctor._base import (
    ADT_AI_GITHUB_LATEST_RELEASE_URL,
    ActionReporter,
    CommandRunner,
    DoctorRequest,
    DoctorResult,
    ExecutableResolver,
    FileDownloader,
    LatestVersionFetcher,
    LineCallback,
    OraclePageFetcher,
    SqlclRelease,
    _certificate_error,
    _is_certificate_error,
    format_action_line,
    format_status_line,
)
from adt_ai.doctor.init import DoctorInitMixin
from adt_ai.doctor.layout_check import schema_case_action_lines
from adt_ai.doctor.upgrade import DoctorUpgradeMixin
from adt_ai.doctor.version_check import DoctorVersionMixin
from adt_ai.shared.subprocess_env import safe_subprocess_environment

__all__ = [
    "DoctorRequest",
    "DoctorResult",
    "DoctorRunner",
    "ActionReporter",
    "format_action_line",
    "ADT_AI_GITHUB_LATEST_RELEASE_URL",
]

# Doctor stays project-config-free by design, it must diagnose broken setups
# before any config exists, so its network timeouts are constants, not keys.
_FETCH_TIMEOUT_SECONDS = 30
_DOWNLOAD_TIMEOUT_SECONDS = 120


def _default_package_root() -> Path:
    """Source checkout root when editable, package folder when installed."""
    module = Path(__file__).resolve()
    for candidate in module.parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "src" / "adt_ai").is_dir():
            return candidate
    return module.parent


def _is_source_checkout(root: Path) -> bool:
    return (root / "pyproject.toml").is_file() and (root / "src" / "adt_ai").is_dir()


def _run_command(
    command: Sequence[str],
    cwd    : Path | None = None,
    env    : Mapping[str, str] | None = None,
) -> str:
    completed = subprocess.run(
        command,
        cwd            = cwd,
        env            = safe_subprocess_environment(env),
        check          = True,
        capture_output = True,
        text           = True,
    )
    return (completed.stdout or completed.stderr).strip()


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ADT.ai doctor"})
    try:
        with urllib.request.urlopen(request, timeout=_FETCH_TIMEOUT_SECONDS) as response:
            return cast(bytes, response.read()).decode("utf-8", errors="replace")
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
        command_runner     : CommandRunner | None = None,
        executable_resolver: ExecutableResolver | None = None,
        env                : Mapping[str, str] | None = None,
        package_version    : str = "",
        oracledb_version   : str | None = None,
        oracle_mode        : str | None = None,
        instant_client     : str | None = None,
        package_root       : Path | None = None,
        resource_root      : Traversable | None = None,
        python_executable  : str | None = None,
        oracle_page_fetcher: OraclePageFetcher | None = None,
        file_downloader    : FileDownloader | None = None,
        latest_version_fetcher: LatestVersionFetcher | None = None,
        line_callback      : LineCallback | None = None,
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
        self.package_root        = package_root or _default_package_root()
        self._package_root_is_checkout = package_root is not None or _is_source_checkout(
            self.package_root
        )
        # Tests and editable installs intentionally keep using a supplied/source
        # root. A wheel has no repository beside it, so Hatch places the same
        # files under this package-owned resource folder instead.
        if resource_root is not None:
            self.resource_root = resource_root
        elif self._package_root_is_checkout:
            self.resource_root = self.package_root
        else:
            self.resource_root = resources.files("adt_ai.doctor").joinpath("resources")
        self.python_executable   = python_executable or sys.executable
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
            # nothing stale it is not an empty section, it is no section. The
            # layout rows join it because a rename is an offer too, and the
            # section is already the place a read-only run puts its offers.
            action_lines = self._status_action_lines()
            action_lines.extend(self._layout_action_lines(request))
            if action_lines:
                self._begin_actions_section(lines)
                self._extend(lines, action_lines)
            return DoctorResult(lines, performed_actions, exit_code)

        self._begin_actions_section(lines)
        for action in (
            partial(self._upgrade_adt_ai, target_version=request.update_version),
            self._install_requirements,
            self._upgrade_sqlcl,
        ):
            action_result = action(lines)
            performed_actions.extend(action_result.performed_actions)
            exit_code = max(exit_code, action_result.exit_code)

        return DoctorResult(lines, performed_actions, exit_code)

    def _layout_action_lines(self, request: DoctorRequest) -> list[str]:
        """Rows offering the rename when the repo tree disagrees with the template.

        Empty whenever there is no config, no schema level, or no folder whose
        case the next export would change, which is every setup that has not hit
        this. See `doctor/layout_check.py` for why a switch alone leaves work.
        """
        if request.root is None:
            return []
        return schema_case_action_lines(request.root, request.config)

    def _begin_actions_section(self, lines: list[str]) -> None:
        """Open `ACTIONS:` with the blank line that separates it from ENVIRONMENT.

        Header and separator move together so a run with nothing to offer leaves
        neither behind, the shared timer footer then owns the trailing spacing.
        """
        self._add(lines, "")
        self._add(lines, "ACTIONS:")

    def _command_env(self) -> dict[str, str]:
        env = safe_subprocess_environment(self.env)
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
