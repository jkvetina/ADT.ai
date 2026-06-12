from __future__ import annotations

import importlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from adt_ai.env_check import CheckResult, EnvironmentChecker

CommandRunner = Callable[[Sequence[str], Path | None, Mapping[str, str]], str]
ExecutableResolver = Callable[[str], str | None]
OraclePageFetcher = Callable[[str], str]
FileDownloader = Callable[[str, Path], None]
LineCallback = Callable[[str], None]
LatestVersionFetcher = Callable[[str], str]


class ActionReporter:
    """Streams a single action line in two halves: the label/prefix is printed the
    moment the work starts, and the dots + outcome are appended once it finishes.
    Lets the console show what's running before the result is known."""

    def begin(self, label: str) -> None:  # pragma: no cover - interface
        ...

    def end(self, label: str, outcome: str) -> None:  # pragma: no cover - interface
        ...

SQLCL_DOWNLOAD_PAGE = "https://www.oracle.com/database/sqldeveloper/technologies/sqlcl/download/"
JAVA_DOWNLOAD_PAGE = "https://www.oracle.com/java/technologies/downloads/"
INSTANT_CLIENT_PAGE = "https://www.oracle.com/database/technologies/instant-client.html"
PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"

# Each action renders as a single line: a two-space indent, the label, a run of
# dots, and the outcome whose final character lands on column 72.
ACTION_LINE_WIDTH = 72
STATUS_LINE_WIDTH = 78
STATUS_LABEL_WIDTH = 20

PROJECT_CONFIG_TEMPLATE = """
# ADT.ai project override config.
#
# Defaults are loaded from the ADT.ai repository first. Keep this file small and
# store only project-specific overrides here.
#
# Keep sensitive connection files and wallets outside the project repo.

# Default export layout:
# path_template: database/<schema>/<object_type>

# Alternative export layout example:
# path_template: <schema>/database/<object_type>

# Optional external connection and wallet locations:
# connections:
#   path: /secure/path/connections
#   file: CORE23.yaml
#   wallet_path: /secure/path/connections/wallets
""".lstrip()

PROJECT_GITIGNORE_TEMPLATE = """
.DS_Store
.temp.nosync/
connections/*
!connections/.gitkeep
!connections/wallets/
connections/wallets/*
!connections/wallets/.gitkeep
""".lstrip()


def format_action_line(label: str, outcome: str) -> str:
    prefix = f"  {label} "
    suffix = f" {outcome}"
    dots = max(1, ACTION_LINE_WIDTH - len(prefix) - len(suffix))
    return f"{prefix}{'.' * dots}{suffix}"


def format_status_line(label: str, value: str, status: str | None = None) -> str:
    line = f"  {label:<{STATUS_LABEL_WIDTH}} | {value or '<empty>'}"
    if not status:
        return line
    suffix = f" {status}"
    dots = max(1, STATUS_LINE_WIDTH - len(line) - 1 - len(suffix))
    return f"{line} {'.' * dots}{suffix}"


def _init_group_lines(label: str, root: Path, paths: Sequence[Path]) -> list[str]:
    if not paths:
        return []
    root_name = root.name or root.anchor.rstrip("/") or "."
    lines = ["", f"  {label}:"]
    for relative_path in sorted(paths, key=lambda path: path.as_posix().lstrip(".").lower()):
        lines.append(f"    - {root_name}/{relative_path.as_posix()}")
    return lines


@dataclass(frozen=True)
class DoctorRequest:
    update: bool = False
    sqlcl : bool = False
    offline: bool = False
    init  : bool = False
    root  : Path | None = None
    force : bool = False


@dataclass(frozen=True)
class DoctorResult:
    lines            : list[str]
    performed_actions: list[str]
    exit_code        : int = 0


@dataclass(frozen=True)
class SqlclRelease:
    version     : str
    download_url: str


class DoctorRunner:
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
        oracle_page_fetcher: OraclePageFetcher | None = None,
        file_downloader    : FileDownloader | None = None,
        latest_version_fetcher: LatestVersionFetcher | None = None,
        line_callback      : LineCallback | None = None,
        action_reporter    : ActionReporter | None = None,
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
        self._add(lines, "")
        self._add(lines, "ACTIONS:")
        performed_actions: list[str] = []
        exit_code = 1 if any(result.status == "FAIL" for result in check_results) else 0

        if request.sqlcl:
            sqlcl_result = self._upgrade_sqlcl(lines)
            performed_actions.extend(sqlcl_result.performed_actions)
            exit_code = max(exit_code, sqlcl_result.exit_code)
            return DoctorResult(lines, performed_actions, exit_code)

        if not request.update:
            self._extend(lines, self._status_action_lines())
            return DoctorResult(lines, performed_actions, exit_code)

        for action in (
            self._upgrade_adt_ai,
            self._install_requirements,
            self._upgrade_sqlcl,
        ):
            action_result = action(lines)
            performed_actions.extend(action_result.performed_actions)
            exit_code = max(exit_code, action_result.exit_code)

        return DoctorResult(lines, performed_actions, exit_code)

    def _init_project(self, request: DoctorRequest) -> DoctorResult:
        root = (request.root or Path(".")).expanduser().resolve()
        lines: list[str] = []
        self._add(lines, "PROJECT INIT:")
        created: list[Path] = []
        skipped: list[Path] = []

        for relative_path, content in (
            (Path("config/config.yaml"), PROJECT_CONFIG_TEMPLATE),
            (Path(".gitignore"), PROJECT_GITIGNORE_TEMPLATE),
            (Path("connections/.gitkeep"), ""),
            (Path("connections/wallets/.gitkeep"), ""),
        ):
            path = root / relative_path
            if path.exists() and not request.force:
                skipped.append(relative_path)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            created.append(relative_path)

        self._extend(lines, _init_group_lines("CREATED", root, created))
        self._extend(lines, _init_group_lines("SKIPPED", root, skipped))

        if skipped:
            self._add(lines, "")
            self._add(lines, "Use `adtai doctor -init -force` to overwrite generated files.")

        return DoctorResult(lines, performed_actions=[], exit_code=0)

    def _version_lines(self, check_results: list[CheckResult], *, online: bool) -> Iterable[str]:
        checks = self._checks_by_name(check_results)
        yield "CURRENT VERSIONS:"
        adt_ai_status = self._online_update_status("adt-ai", self.package_version, online=online)
        yield format_status_line("ADT.ai", self.package_version or "unknown", adt_ai_status)
        yield format_status_line(
            "Python",
            self._check_value(checks, "Python"),
            self._display_status(checks, "Python"),
        )
        yield format_status_line(
            "Git",
            self._check_value(checks, "Git", normalizer=_normalize_git_version),
            self._display_status(checks, "Git"),
        )
        java_value = self._check_value(checks, "Java", normalizer=_normalize_java_version)
        yield format_status_line(
            "Java",
            java_value,
            self._display_status(
                checks,
                "Java",
                online_status=self._online_update_status("java", java_value, online=online),
            ),
        )
        oracledb_value = self._check_value(
            checks,
            "Oracle DB module",
            normalizer=_normalize_oracledb_version,
        )
        yield format_status_line(
            "oracledb",
            oracledb_value,
            self._display_status(
                checks,
                "Oracle DB module",
                online_status=self._online_update_status(
                    "oracledb",
                    oracledb_value,
                    online=online,
                ),
            ),
        )
        instant_client_value = self._check_value(checks, "Instant Client")
        yield format_status_line(
            "Instant Client",
            instant_client_value,
            self._display_status(
                checks,
                "Instant Client",
                online_status=self._online_update_status(
                    "instant-client",
                    instant_client_value,
                    online=online,
                ),
            ),
        )
        sqlcl_value = self._check_value(checks, "SQLcl")
        yield format_status_line(
            "SQLcl",
            sqlcl_value,
            self._display_status(
                checks,
                "SQLcl",
                online_status=self._online_update_status("sqlcl", sqlcl_value, online=online),
            ),
        )

    def _status_action_lines(self) -> list[str]:
        return [
            "  Run `adtai doctor -update` for full ADT.ai + requirements + SQLcl upgrade.",
            "  Run `adtai doctor -sqlcl` to upgrade SQLcl only.",
        ]

    def _environment_lines(self, check_results: list[CheckResult]) -> list[str]:
        env = self._command_env()
        sqlcl_executable = self._sqlcl_executable()
        # SQLCL is the real `sql` launcher resolved from PATH, not the unreliable
        # SQLCL_HOME env var (which may be unset). This is also the exact path the
        # SQLcl upgrade replaces, so the displayed location and the upgrade target
        # are always the same.
        return [
            "ENVIRONMENT:",
            format_status_line(
                "ADT_ENV",
                self._env_value("ADT_ENV"),
                self._visible_status(self._env_status("ADT_ENV")),
            ),
            format_status_line(
                "ADT_KEY",
                self._adt_key_value(),
                self._visible_status(self._env_status("ADT_KEY")),
            ),
            format_status_line("ARCH", platform.machine() or "unknown"),
            format_status_line("JAVA_TOOL_OPTIONS", env["JAVA_TOOL_OPTIONS"]),
            format_status_line("LANG", env["LANG"], None if env["LANG"] else "WARN"),
            format_status_line("NLS_LANG", env["NLS_LANG"]),
            format_status_line(
                "ORACLE_HOME",
                self._oracle_home(),
                None if self._oracle_home() else "WARN",
            ),
            format_status_line(
                "SQLCL",
                sqlcl_executable or "not found",
                None if sqlcl_executable else "WARN",
            ),
        ]

    def _check_results(self) -> list[CheckResult]:
        checker = EnvironmentChecker(
            command_runner      = lambda command: self.command_runner(
                command,
                None,
                self._command_env(),
            ),
            executable_resolver = self.executable_resolver,
            env                 = self.env,
            python_version      = sys.version.split()[0],
            oracledb_version    = self.oracledb_version,
            oracle_mode         = self.oracle_mode,
            instant_client      = self.instant_client,
        )
        return checker.run()

    def _checks_by_name(self, results: list[CheckResult]) -> dict[str, CheckResult]:
        return {result.name: result for result in results}

    def _check_status(self, checks: Mapping[str, CheckResult], name: str) -> str:
        result = checks.get(name)
        return result.status if result else "INFO"

    def _display_status(
        self,
        checks: Mapping[str, CheckResult],
        name: str,
        *,
        online_status: str | None = None,
    ) -> str | None:
        local_status = self._check_status(checks, name)
        if local_status in {"FAIL", "WARN"}:
            return local_status
        return online_status or self._visible_status(local_status)

    def _visible_status(self, status: str) -> str | None:
        return None if status == "OK" else status

    def _online_update_status(
        self,
        component: str,
        current_version: str,
        *,
        online: bool,
    ) -> str | None:
        if (
            not online
            or not current_version
            or current_version in {"unknown", "not found", "not installed"}
        ):
            return None
        try:
            latest_version = self._latest_version(component)
        except Exception:
            return "WARN"
        if not latest_version:
            return None
        return "UPDATE" if _is_newer_version(latest_version, current_version) else None

    def _latest_version(self, component: str) -> str:
        if self.latest_version_fetcher:
            return self.latest_version_fetcher(component)
        if component == "adt-ai":
            return self._latest_adt_ai_version()
        if component == "java":
            return self._latest_java_version()
        if component == "sqlcl":
            return self._latest_sqlcl_release().version
        if component == "oracledb":
            return self._latest_pypi_version("oracledb")
        if component == "instant-client":
            return self._latest_instant_client_version()
        raise ValueError(f"unknown online check component: {component}")

    def _check_value(
        self,
        checks: Mapping[str, CheckResult],
        name: str,
        *,
        normalizer: Callable[[str], str] | None = None,
    ) -> str:
        result = checks.get(name)
        if not result:
            return "unknown"
        value = result.detail or ""
        if "not found on PATH" in value or value.startswith("not found;"):
            value = "not found"
        elif not value:
            value = "not found" if result.status in {"FAIL", "WARN"} else "unknown"
        return normalizer(value) if normalizer else value

    def _env_value(self, name: str) -> str:
        return self.env.get(name) or "<empty>"

    def _adt_key_value(self) -> str:
        return "<redacted>" if self.env.get("ADT_KEY") else "<empty>"

    def _env_status(self, name: str) -> str:
        return "OK" if self.env.get(name) else "WARN"

    def _command_version(self, command: str, args: list[str]) -> str:
        if not self.executable_resolver(command):
            return "not found"
        try:
            return _first_line(self.command_runner(args, None, self._command_env()))
        except Exception:
            return "unknown"

    def _install_requirements(self, lines: list[str]) -> DoctorResult:
        label = "Python requirements"
        requirements = self.package_root / "requirements.txt"
        self._begin_action(label)
        if not requirements.exists():
            return self._fail_action(lines, label, f"requirements.txt missing: {requirements}")
        try:
            output = self.command_runner(
                ["pip3", "install", "-r", str(requirements), "--upgrade"],
                None,
                self._command_env(),
            )
        except Exception as error:
            return self._fail_action(lines, label, str(error))
        # pip prints "Successfully installed ..." only when it actually installed
        # or upgraded a package; an all-satisfied run changes nothing, so report
        # CURRENT to match the ADT.ai action instead of a misleading DONE.
        if "Successfully installed" not in output:
            self._end_action(lines, label, "CURRENT")
            return DoctorResult(lines=[], performed_actions=[], exit_code=0)
        self._end_action(lines, label, "UPDATED")
        return DoctorResult(lines=[], performed_actions=["requirements"], exit_code=0)

    def _upgrade_adt_ai(self, lines: list[str]) -> DoctorResult:
        label = "ADT.ai"
        self._begin_action(label)
        if self._is_git_repo():
            stashed = False
            repo_root: Path | None = None
            try:
                repo_root = Path(
                    self.command_runner(
                        ["git", "rev-parse", "--show-toplevel"],
                        self.package_root,
                        self._command_env(),
                    ).strip()
                )
                head_before = self.command_runner(
                    ["git", "rev-parse", "HEAD"],
                    repo_root,
                    self._command_env(),
                ).strip()
                # The user may have local edits in the ADT.ai checkout. Stash them
                # (tracked + untracked) so `git pull` fast-forwards cleanly, then
                # re-apply them on top of the update.
                dirty = bool(
                    self.command_runner(
                        ["git", "status", "--porcelain"],
                        repo_root,
                        self._command_env(),
                    ).strip()
                )
                if dirty:
                    stash_command = [
                        "git",
                        "stash",
                        "push",
                        "--include-untracked",
                        "-m",
                        "adtai-update-autostash",
                    ]
                    self.command_runner(
                        stash_command,
                        repo_root,
                        self._command_env(),
                    )
                    stashed = True
                self.command_runner(["git", "pull"], repo_root, self._command_env())
                head_after = self.command_runner(
                    ["git", "rev-parse", "HEAD"],
                    repo_root,
                    self._command_env(),
                ).strip()
                if stashed:
                    self.command_runner(["git", "stash", "pop"], repo_root, self._command_env())
                    stashed = False
                self.command_runner(
                    ["pip3", "install", "-e", str(repo_root)],
                    None,
                    self._command_env(),
                )
            except Exception as error:
                if stashed:
                    restore_message = (
                        "    Local changes saved in git stash; "
                        "run `git stash pop` to restore them."
                    )
                    self._add(
                        lines,
                        restore_message,
                    )
                return self._fail_action(lines, label, str(error))
            if head_before == head_after:
                self._end_action(lines, label, "CURRENT")
                return DoctorResult(lines=[], performed_actions=[], exit_code=0)
            version = self._adt_ai_version(repo_root)
            self._end_action(lines, label, self._updated_outcome(version))
            return DoctorResult(lines=[], performed_actions=["adt-ai"], exit_code=0)

        try:
            output = self.command_runner(
                ["pip3", "install", "--upgrade", "adt-ai"],
                None,
                self._command_env(),
            )
        except Exception as error:
            return self._fail_action(lines, label, str(error))
        version, upgraded = self._parse_pip_upgrade(output)
        if upgraded:
            self._end_action(lines, label, self._updated_outcome(version))
            return DoctorResult(lines=[], performed_actions=["adt-ai"], exit_code=0)
        self._end_action(lines, label, "CURRENT")
        return DoctorResult(lines=[], performed_actions=[], exit_code=0)

    def _upgrade_sqlcl(self, lines: list[str]) -> DoctorResult:
        label = "SQLcl"
        self._begin_action(label)
        sqlcl_dir = self._sqlcl_home()
        if sqlcl_dir == sqlcl_dir.parent:
            return self._fail_action(
                lines, label, f"cannot locate a SQLcl install to upgrade ({sqlcl_dir})"
            )
        sqlcl_parent = sqlcl_dir.parent
        current_version = self._sqlcl_version()
        try:
            release = self._latest_sqlcl_release()
        except Exception as error:
            return self._fail_action(lines, label, f"cannot fetch SQLcl release metadata: {error}")

        if current_version and _version_key(current_version) >= _version_key(release.version):
            self._end_action(lines, label, "CURRENT")
            return DoctorResult(lines=[], performed_actions=[], exit_code=0)

        backup_dir = self._sqlcl_backup_dir(sqlcl_dir)
        try:
            with tempfile.TemporaryDirectory(prefix="adtai-sqlcl-") as temp_dir:
                sqlcl_zip = Path(temp_dir) / Path(release.download_url).name
                self.file_downloader(release.download_url, sqlcl_zip)
                if sqlcl_dir.exists():
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir)
                    shutil.move(str(sqlcl_dir), str(backup_dir))
                with zipfile.ZipFile(sqlcl_zip) as archive:
                    self._extract_archive(archive, sqlcl_parent)
                self._ensure_executable(sqlcl_dir / "bin" / "sql")
            self._end_action(lines, label, f"UPGRADED TO {release.version}")
            return DoctorResult(lines=[], performed_actions=["sqlcl"], exit_code=0)
        except Exception as error:
            if backup_dir.exists() and not sqlcl_dir.exists():
                shutil.move(str(backup_dir), str(sqlcl_dir))
                self._add(lines, "    Restored previous SQLcl backup.")
            return self._fail_action(lines, label, str(error))

    @staticmethod
    def _extract_archive(archive: zipfile.ZipFile, target: Path) -> None:
        """Extract a zip while preserving Unix permission bits.

        ``ZipFile.extractall`` drops the stored mode, so the SQLcl ``bin/sql``
        launcher lands as ``0644`` and the very next ``sql -V`` fails with
        "permission denied" — which is why a freshly upgraded SQLcl reported
        ``not found`` on the following run. Re-apply each member's stored mode.
        """
        for member in archive.infolist():
            extracted = Path(archive.extract(member, target))
            mode = member.external_attr >> 16
            if mode:
                extracted.chmod(mode & 0o7777)

    @staticmethod
    def _ensure_executable(launcher: Path) -> None:
        """Guarantee the SQLcl launcher is runnable even if the archive carried
        no mode bits (e.g. a Windows-built zip), so version detection — which
        runs the executable — succeeds right after an upgrade."""
        if launcher.exists():
            current = launcher.stat().st_mode
            launcher.chmod(current | 0o111)

    def _sqlcl_version(self) -> str:
        sqlcl_executable = self._sqlcl_executable()
        if not sqlcl_executable:
            return ""
        try:
            output = self.command_runner([sqlcl_executable, "-V"], None, self._command_env())
        except Exception:
            return ""
        match = re.search(r"SQLcl:\s+Release\s+([^\s]+)", output)
        return match.group(1) if match else _first_line(output)

    def _oracledb_version(self) -> str:
        if self.oracledb_version is not None:
            return self.oracledb_version or "not installed"
        try:
            return str(importlib.import_module("oracledb").__version__)
        except Exception:
            return "not installed"

    def _oracle_mode(self) -> str:
        if self.oracle_mode is not None:
            return self.oracle_mode or "unknown"
        try:
            oracledb = importlib.import_module("oracledb")
            return "thin" if oracledb.is_thin_mode() else "thick"
        except Exception:
            return "unknown"

    def _instant_client_version(self) -> str:
        if self.instant_client is not None:
            return self.instant_client or "not found"
        version = _instant_client_version(self.env)
        return version or "not found"

    def _oracle_home(self) -> str:
        return self.env.get("ORACLE_HOME") or str(Path.home() / "instantclient_19_16")

    def _sqlcl_executable(self) -> str:
        """Resolve the `sql` launcher.

        PATH is authoritative: whatever `sql` the user's shell runs is the
        SQLcl we report a version for and upgrade in place. Only when `sql`
        is not on PATH at all do we fall back to a SQLCL_HOME / ORACLE_HOME
        guess. We never require SQLCL_HOME to be set.
        """
        path_sql = self.executable_resolver("sql")
        if path_sql:
            return path_sql
        fallback = self._fallback_sqlcl_home() / "bin" / "sql"
        return str(fallback) if fallback.exists() else ""

    def _sqlcl_home(self) -> Path:
        """SQLcl install directory, derived from the resolved `sql` launcher.

        A launcher lives at ``<home>/bin/sql``; following any symlink and
        stepping up two levels yields the real install directory. Detection and
        upgrade therefore target the same place, so a reported
        ``UPGRADED TO <version>`` is detected on the next run. Falls back to the
        SQLCL_HOME / ORACLE_HOME guess only when no launcher is resolvable.
        """
        executable = self._sqlcl_executable()
        if executable:
            return Path(executable).expanduser().resolve().parent.parent
        return self._fallback_sqlcl_home()

    def _fallback_sqlcl_home(self) -> Path:
        configured_home = self.env.get("SQLCL_HOME")
        if configured_home:
            return Path(configured_home).expanduser()
        return Path(self._oracle_home()).expanduser() / "sqlcl"

    def _is_git_repo(self) -> bool:
        try:
            inside_work_tree = self.command_runner(
                ["git", "rev-parse", "--is-inside-work-tree"],
                self.package_root,
                self._command_env(),
            ).strip()
            return inside_work_tree == "true"
        except Exception:
            return False

    def _latest_sqlcl_release(self) -> SqlclRelease:
        page = self.oracle_page_fetcher(SQLCL_DOWNLOAD_PAGE)
        version_match = re.search(
            r"Download\s+the\s+latest\s+version\s+-\s+SQLcl\s+([0-9]+(?:\.[0-9]+)+)",
            page,
            re.IGNORECASE,
        )
        if not version_match:
            version_match = re.search(r"SQLcl\s+([0-9]+(?:\.[0-9]+)+)", page, re.IGNORECASE)
        if not version_match:
            raise ValueError("SQLcl version not found on Oracle download page")

        version = version_match.group(1)
        url_match = re.search(
            r'href=["\']([^"\']*sqlcl-[^"\']*\.zip[^"\']*)["\']',
            page,
            re.IGNORECASE,
        )
        if not url_match:
            raise ValueError("SQLcl download link not found on Oracle download page")

        return SqlclRelease(version=version, download_url=unescape(url_match.group(1)))

    def _latest_adt_ai_version(self) -> str:
        remote_version = self._adt_ai_remote_git_version()
        if remote_version:
            return remote_version
        return self._latest_pypi_version("adt-ai")

    def _adt_ai_remote_git_version(self) -> str:
        if not self._is_git_repo():
            return ""
        try:
            repo_root = Path(
                self.command_runner(
                    ["git", "rev-parse", "--show-toplevel"],
                    self.package_root,
                    self._command_env(),
                ).strip()
            )
            local_head = self.command_runner(
                ["git", "rev-parse", "HEAD"],
                repo_root,
                self._command_env(),
            ).strip()
            remote_head_line = self.command_runner(
                ["git", "ls-remote", "origin", "HEAD"],
                repo_root,
                self._command_env(),
            ).strip()
        except Exception:
            return ""
        remote_head = remote_head_line.split()[0] if remote_head_line.split() else ""
        # For editable/git installs the package version often stays constant while
        # the repository advances. Returning a synthetic greater version lets the
        # normal comparison mark the row as UPDATE without exposing a commit SHA
        # in the version column.
        return "999999" if remote_head and remote_head != local_head else self.package_version

    def _latest_pypi_version(self, package: str) -> str:
        payload = json.loads(self.oracle_page_fetcher(PYPI_PACKAGE_URL.format(package=package)))
        return str(payload.get("info", {}).get("version", ""))

    def _latest_java_version(self) -> str:
        page = self.oracle_page_fetcher(JAVA_DOWNLOAD_PAGE)
        for pattern in (
            r"JDK\s+([0-9]+(?:\.[0-9]+)*)\s+is\s+the\s+latest\s+release",
            r"Java\s+SE\s+Development\s+Kit\s+([0-9]+(?:\.[0-9]+)*)\s+downloads",
        ):
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                return match.group(1)
        raise ValueError("Java version not found on Oracle download page")

    def _latest_instant_client_version(self) -> str:
        page = self.oracle_page_fetcher(INSTANT_CLIENT_PAGE)
        for pattern in (
            r"latest\s+([0-9]+)(?:ai)?\s+Release\s+Update",
            r"Instant\s+Client[^0-9]+([0-9]+(?:\.[0-9]+)+)",
        ):
            match = re.search(pattern, page, re.IGNORECASE)
            if match:
                return match.group(1)
        raise ValueError("Instant Client version not found on Oracle download page")

    def _sqlcl_backup_dir(self, sqlcl_dir: Path) -> Path:
        version = self._sqlcl_version()
        suffix = version.replace(".", "-") if version else "backup"
        return sqlcl_dir.parent / f"sqlcl{suffix}"

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

    def _updated_outcome(self, version: str) -> str:
        return f"UPDATED TO {version}" if version else "UPDATED"

    def _adt_ai_version(self, repo_root: Path | None) -> str:
        if repo_root is not None:
            init_file = repo_root / "src" / "adt_ai" / "__init__.py"
            try:
                content = init_file.read_text(encoding="utf-8")
            except OSError:
                content = ""
            match = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", content)
            if match:
                return match.group(1)
        return self.package_version

    def _parse_pip_upgrade(self, output: str) -> tuple[str, bool]:
        if "Successfully installed" in output:
            match = re.search(r"adt[-_]ai-(\S+)", output)
            return (match.group(1) if match else self.package_version), True
        return self.package_version, False

    def _add(self, lines: list[str], line: str) -> None:
        lines.append(line)
        if self.line_callback:
            self.line_callback(line)

    def _extend(self, lines: list[str], new_lines: Iterable[str]) -> None:
        for line in new_lines:
            self._add(lines, line)


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


def _normalize_git_version(value: str) -> str:
    return re.sub(r"^git version\s+", "", value, flags=re.IGNORECASE)


def _normalize_java_version(value: str) -> str:
    return re.sub(r"^(?:java|openjdk)\s+", "", value, flags=re.IGNORECASE)


def _normalize_oracledb_version(value: str) -> str:
    return re.sub(r"^oracledb\s+", "", value, flags=re.IGNORECASE)


def _fetch_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "ADT.ai doctor"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as error:
        if not _is_certificate_error(error):
            raise
        return _curl_fetch(url).decode("utf-8", errors="replace")


def _download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "ADT.ai doctor"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            target.write_bytes(response.read())
    except urllib.error.URLError as error:
        if not _is_certificate_error(error):
            raise
        _curl_download(url, target)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


def _is_newer_version(latest: str, current: str) -> bool:
    latest_key = _version_key(latest)
    current_key = _version_key(current)
    return bool(latest_key and current_key and latest_key > current_key)


def _is_certificate_error(error: urllib.error.URLError) -> bool:
    message = str(error)
    return (
        "CERTIFICATE_VERIFY_FAILED" in message
        or "unable to get local issuer certificate" in message
    )


def _curl_fetch(url: str) -> bytes:
    completed = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", url],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _curl_download(url: str, target: Path) -> None:
    subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--output", str(target), url],
        check=True,
        capture_output=True,
    )
