from __future__ import annotations

import importlib
import platform
import re
import sys
from collections.abc import Callable, Mapping
from pathlib import Path

from adt_ai.doctor._base import (
    _first_line,
    _instant_client_version,
    _is_newer_version,
    _normalize_git_version,
    _normalize_java_version,
    _normalize_oracledb_version,
    format_status_line,
)
from adt_ai.doctor.version_fetch import DoctorLatestVersionMixin
from adt_ai.shared.env_check import CheckResult


class DoctorVersionMixin(DoctorLatestVersionMixin):
    def _version_lines(
        self,
        check_results: list[CheckResult],
        *,
        online: bool,
    ) -> object:  # Iterable[str]
        checks = self._checks_by_name(check_results)  # type: ignore[attr-defined]
        adt_ai_value         = self.package_version  # type: ignore[attr-defined]
        java_value           = self._check_value(checks, "Java", normalizer=_normalize_java_version)  # type: ignore[attr-defined]
        oracledb_value       = self._check_value(  # type: ignore[attr-defined]
            checks, "Oracle DB module", normalizer=_normalize_oracledb_version
        )
        instant_client_value = self._check_value(checks, "Instant Client")  # type: ignore[attr-defined]
        sqlcl_value          = self._check_value(checks, "SQLcl")  # type: ignore[attr-defined]

        yield "CURRENT VERSIONS:"
        # Fire every online version check concurrently before streaming the rows.
        # A default `doctor` run otherwise made 3 sequential HTTP calls — ADT.ai,
        # oracledb, and SQLcl (each a 30s timeout), so wall-clock was their sum
        # (~90s worst case); concurrent fetches bound it to the slowest single
        # request. The cheap local rows below still stream as they are produced;
        # each online row blocks only on its own in-flight fetch.
        self._prefetch_latest_versions(
            [
                ("adt-ai", adt_ai_value),
                ("oracledb", oracledb_value),
                ("sqlcl", sqlcl_value),
            ],
            online=online,
        )
        try:
            adt_ai_status = self._online_update_status("adt-ai", adt_ai_value, online=online)
            yield format_status_line("ADT.ai", adt_ai_value or "unknown", adt_ai_status)
            yield format_status_line(
                "Python",
                self._check_value(checks, "Python"),  # type: ignore[attr-defined]
                self._display_status(checks, "Python"),  # type: ignore[attr-defined]
            )
            yield format_status_line(
                "Git",
                self._check_value(checks, "Git", normalizer=_normalize_git_version),  # type: ignore[attr-defined]
                self._display_status(checks, "Git"),  # type: ignore[attr-defined]
            )
            yield format_status_line(
                "Java",
                java_value,
                self._display_status(  # type: ignore[attr-defined]
                    checks,
                    "Java",
                    online_status=None,
                ),
            )
            yield format_status_line(
                "oracledb",
                oracledb_value,
                self._display_status(  # type: ignore[attr-defined]
                    checks,
                    "Oracle DB module",
                    online_status=self._online_update_status(
                        "oracledb", oracledb_value, online=online
                    ),
                ),
            )
            yield format_status_line(
                "Instant Client",
                instant_client_value,
                self._display_status(  # type: ignore[attr-defined]
                    checks,
                    "Instant Client",
                    online_status=None,
                ),
            )
            yield format_status_line(
                "SQLcl",
                sqlcl_value,
                self._display_status(  # type: ignore[attr-defined]
                    checks,
                    "SQLcl",
                    online_status=self._online_update_status("sqlcl", sqlcl_value, online=online),
                ),
            )
        finally:
            self._shutdown_version_executor()

    def _status_action_lines(self) -> list[str]:
        return [
            "  Run `adtai doctor -update` for full ADT.ai + requirements + SQLcl upgrade.",
            "  Run `adtai doctor -sqlcl` to upgrade SQLcl only.",
        ]

    def _environment_lines(self, check_results: list[CheckResult]) -> list[str]:
        env = self._command_env()  # type: ignore[attr-defined]
        sqlcl_executable = self._sqlcl_executable()  # type: ignore[attr-defined]
        # SQLCL is the real `sql` launcher resolved from PATH, not the unreliable
        # SQLCL_HOME env var (which may be unset). This is also the exact path the
        # SQLcl upgrade replaces, so the displayed location and the upgrade target
        # are always the same.
        return [
            "ENVIRONMENT:",
            format_status_line(
                "ADT_ENV",
                self._env_value("ADT_ENV"),  # type: ignore[attr-defined]
                self._visible_status(self._env_status("ADT_ENV")),  # type: ignore[attr-defined]
            ),
            format_status_line(
                "ADT_KEY",
                self._adt_key_value(),  # type: ignore[attr-defined]
                self._visible_status(self._env_status("ADT_KEY")),  # type: ignore[attr-defined]
            ),
            format_status_line("ARCH", platform.machine() or "unknown"),
            format_status_line("JAVA_TOOL_OPTIONS", env["JAVA_TOOL_OPTIONS"]),
            format_status_line("LANG", env["LANG"], None if env["LANG"] else "WARN"),
            format_status_line("NLS_LANG", env["NLS_LANG"]),
            format_status_line(
                "ORACLE_HOME",
                self._oracle_home(),  # type: ignore[attr-defined]
                None if self._oracle_home() else "WARN",  # type: ignore[attr-defined]
            ),
            format_status_line(
                "SQLCL",
                sqlcl_executable or "not found",
                None if sqlcl_executable else "WARN",
            ),
        ]

    def _check_results(self) -> list[CheckResult]:
        from adt_ai.shared.env_check import EnvironmentChecker
        checker = EnvironmentChecker(
            command_runner      = lambda command: self.command_runner(  # type: ignore[attr-defined]
                command,
                None,
                self._command_env(),  # type: ignore[attr-defined]
            ),
            executable_resolver = self.executable_resolver,  # type: ignore[attr-defined]
            env                 = self.env,  # type: ignore[attr-defined]
            python_version      = sys.version.split()[0],
            oracledb_version    = self.oracledb_version,  # type: ignore[attr-defined]
            oracle_mode         = self.oracle_mode,  # type: ignore[attr-defined]
            instant_client      = self.instant_client,  # type: ignore[attr-defined]
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
        return online_status or self._visible_status(local_status)  # type: ignore[attr-defined]

    def _visible_status(self, status: str) -> str | None:
        return None if status == "OK" else status

    def _online_update_status(
        self,
        component: str,
        current_version: str,
        *,
        online: bool,
    ) -> str | None:
        if not online or not self._needs_online_check(current_version):
            return None
        try:
            latest_version = self._latest_version(component)
        except Exception:
            return "WARN"
        if not latest_version:
            return None
        return "UPDATE" if _is_newer_version(latest_version, current_version) else None

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
        return self.env.get(name) or "<empty>"  # type: ignore[attr-defined]

    def _adt_key_value(self) -> str:
        return "<redacted>" if self.env.get("ADT_KEY") else "<empty>"  # type: ignore[attr-defined]

    def _env_status(self, name: str) -> str:
        return "OK" if self.env.get(name) else "WARN"  # type: ignore[attr-defined]

    def _command_version(self, command: str, args: list[str]) -> str:
        if not self.executable_resolver(command):  # type: ignore[attr-defined]
            return "not found"
        try:
            return _first_line(self.command_runner(args, None, self._command_env()))  # type: ignore[attr-defined]
        except Exception:
            return "unknown"

    def _sqlcl_version(self) -> str:
        sqlcl_executable = self._sqlcl_executable()  # type: ignore[attr-defined]
        if not sqlcl_executable:
            return ""
        try:
            output = self.command_runner([sqlcl_executable, "-V"], None, self._command_env())  # type: ignore[attr-defined]
        except Exception:
            return ""
        match = re.search(r"SQLcl:\s+Release\s+([^\s]+)", output)
        return match.group(1) if match else _first_line(output)

    def _oracledb_version(self) -> str:
        if self.oracledb_version is not None:  # type: ignore[attr-defined]
            return self.oracledb_version or "not installed"  # type: ignore[attr-defined]
        try:
            return str(importlib.import_module("oracledb").__version__)
        except Exception:
            return "not installed"

    def _oracle_mode(self) -> str:
        if self.oracle_mode is not None:  # type: ignore[attr-defined]
            return self.oracle_mode or "unknown"  # type: ignore[attr-defined]
        try:
            oracledb = importlib.import_module("oracledb")
            return "thin" if oracledb.is_thin_mode() else "thick"
        except Exception:
            return "unknown"

    def _instant_client_version(self) -> str:
        if self.instant_client is not None:  # type: ignore[attr-defined]
            return self.instant_client or "not found"  # type: ignore[attr-defined]
        version = _instant_client_version(self.env)  # type: ignore[attr-defined]
        return version or "not found"

    def _oracle_home(self) -> str:
        return self.env.get("ORACLE_HOME") or str(Path.home() / "instantclient_19_16")  # type: ignore[attr-defined]

    def _sqlcl_executable(self) -> str:
        """Resolve the `sql` launcher.

        PATH is authoritative: whatever `sql` the user's shell runs is the
        SQLcl we report a version for and upgrade in place. Only when `sql`
        is not on PATH at all do we fall back to a SQLCL_HOME / ORACLE_HOME
        guess. We never require SQLCL_HOME to be set.
        """
        path_sql = self.executable_resolver("sql")  # type: ignore[attr-defined]
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
        configured_home = self.env.get("SQLCL_HOME")  # type: ignore[attr-defined]
        if configured_home:
            return Path(configured_home).expanduser()
        return Path(self._oracle_home()).expanduser() / "sqlcl"

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
        return self.package_version  # type: ignore[attr-defined]
