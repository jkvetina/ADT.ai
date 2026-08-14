from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from adt_ai.doctor._base import DoctorResult, _version_key


class DoctorUpgradeMixin:
    def _install_requirements(self, lines: list[str]) -> DoctorResult:
        label = "Python requirements"
        requirements = self.package_root / "requirements.txt"  # type: ignore[attr-defined]
        self._begin_action(label)  # type: ignore[attr-defined]
        if not requirements.exists():
            return self._fail_action(lines, label, f"requirements.txt missing: {requirements}")  # type: ignore[attr-defined]
        try:
            output = self.command_runner(  # type: ignore[attr-defined]
                ["pip3", "install", "-r", str(requirements), "--upgrade"],
                None,
                self._command_env(),  # type: ignore[attr-defined]
            )
        except Exception as error:
            return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]
        # pip prints "Successfully installed ..." only when it actually installed
        # or upgraded a package; an all-satisfied run changes nothing, so report
        # CURRENT to match the ADT.ai action instead of a misleading DONE.
        if "Successfully installed" not in output:
            self._end_action(lines, label, "CURRENT")  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=[], exit_code=0)
        self._end_action(lines, label, "UPDATED")  # type: ignore[attr-defined]
        return DoctorResult(lines=[], performed_actions=["requirements"], exit_code=0)

    def _upgrade_adt_ai(self, lines: list[str]) -> DoctorResult:
        label = "ADT.ai"
        self._begin_action(label)  # type: ignore[attr-defined]
        if self._is_git_repo():  # type: ignore[attr-defined]
            stashed = False
            repo_root: Path | None = None
            try:
                repo_root = self._git_repo_root()  # type: ignore[attr-defined]
                head_before = self._git_head(repo_root)  # type: ignore[attr-defined]
                # The user may have local edits in the ADT.ai checkout. Stash them
                # (tracked + untracked) so `git pull` fast-forwards cleanly, then
                # re-apply them on top of the update.
                dirty = bool(
                    self.command_runner(  # type: ignore[attr-defined]
                        ["git", "status", "--porcelain"],
                        repo_root,
                        self._command_env(),  # type: ignore[attr-defined]
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
                    self.command_runner(  # type: ignore[attr-defined]
                        stash_command,
                        repo_root,
                        self._command_env(),  # type: ignore[attr-defined]
                    )
                    stashed = True
                self.command_runner(["git", "pull"], repo_root, self._command_env())  # type: ignore[attr-defined]
                head_after = self._git_head(repo_root)  # type: ignore[attr-defined]
                if stashed:
                    self.command_runner(["git", "stash", "pop"], repo_root, self._command_env())  # type: ignore[attr-defined]
                    stashed = False
                self.command_runner(  # type: ignore[attr-defined]
                    ["pip3", "install", "-e", str(repo_root)],
                    None,
                    self._command_env(),  # type: ignore[attr-defined]
                )
            except Exception as error:
                if stashed:
                    restore_message = (
                        "    Local changes saved in git stash; "
                        "run `git stash pop` to restore them."
                    )
                    self._add(  # type: ignore[attr-defined]
                        lines,
                        restore_message,
                    )
                return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]
            if head_before == head_after:
                self._end_action(lines, label, "CURRENT")  # type: ignore[attr-defined]
                return DoctorResult(lines=[], performed_actions=[], exit_code=0)
            version = self._adt_ai_version(repo_root)  # type: ignore[attr-defined]
            self._end_action(lines, label, self._updated_outcome(version))  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=["adt-ai"], exit_code=0)

        try:
            output = self.command_runner(  # type: ignore[attr-defined]
                ["pip3", "install", "--upgrade", "adt-ai"],
                None,
                self._command_env(),  # type: ignore[attr-defined]
            )
        except Exception as error:
            return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]
        version, upgraded = self._parse_pip_upgrade(output)
        if upgraded:
            self._end_action(lines, label, self._updated_outcome(version))  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=["adt-ai"], exit_code=0)
        self._end_action(lines, label, "CURRENT")  # type: ignore[attr-defined]
        return DoctorResult(lines=[], performed_actions=[], exit_code=0)

    def _upgrade_sqlcl(self, lines: list[str]) -> DoctorResult:
        label = "SQLcl"
        self._begin_action(label)  # type: ignore[attr-defined]
        sqlcl_dir = self._sqlcl_home()  # type: ignore[attr-defined]
        if sqlcl_dir == sqlcl_dir.parent:
            return self._fail_action(  # type: ignore[attr-defined]
                lines, label, f"cannot locate a SQLcl install to upgrade ({sqlcl_dir})"
            )
        sqlcl_parent = sqlcl_dir.parent
        current_version = self._sqlcl_version()  # type: ignore[attr-defined]
        try:
            release = self._latest_sqlcl_release()  # type: ignore[attr-defined]
        except Exception as error:
            return self._fail_action(lines, label, f"cannot fetch SQLcl release metadata: {error}")  # type: ignore[attr-defined]

        if current_version and _version_key(current_version) >= _version_key(release.version):
            self._end_action(lines, label, "CURRENT")  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=[], exit_code=0)

        backup_dir = self._sqlcl_backup_dir(sqlcl_dir)
        try:
            with tempfile.TemporaryDirectory(prefix="adtai-sqlcl-") as temp_dir:
                sqlcl_zip = Path(temp_dir) / Path(release.download_url).name
                self.file_downloader(release.download_url, sqlcl_zip)  # type: ignore[attr-defined]
                if sqlcl_dir.exists():
                    if backup_dir.exists():
                        shutil.rmtree(backup_dir)
                    shutil.move(str(sqlcl_dir), str(backup_dir))
                with zipfile.ZipFile(sqlcl_zip) as archive:
                    self._extract_archive(archive, sqlcl_parent)
                self._ensure_executable(sqlcl_dir / "bin" / "sql")
            self._end_action(lines, label, f"UPGRADED TO {release.version}")  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=["sqlcl"], exit_code=0)
        except Exception as error:
            if backup_dir.exists() and not sqlcl_dir.exists():
                shutil.move(str(backup_dir), str(sqlcl_dir))
                self._add(lines, "    Restored previous SQLcl backup.")  # type: ignore[attr-defined]
            return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]

    @staticmethod
    def _extract_archive(archive: zipfile.ZipFile, target: Path) -> None:
        """Extract a zip while preserving Unix permission bits.

        ``ZipFile.extractall`` drops the stored mode, so the SQLcl ``bin/sql``
        launcher lands as ``0644`` and the very next ``sql -V`` fails with
        "permission denied", which is why a freshly upgraded SQLcl reported
        ``not found`` on the following run. Re-apply each member's stored mode.

        Each member is checked before extraction so a crafted ``../`` (or
        absolute) path cannot escape ``target`` and overwrite files elsewhere.
        """
        target_root = target.resolve()
        for member in archive.infolist():
            member_path = (target / member.filename).resolve()
            if target_root != member_path and target_root not in member_path.parents:
                raise RuntimeError(f"Unsafe SQLcl zip entry: {member.filename}")
            extracted = Path(archive.extract(member, target))
            mode = member.external_attr >> 16
            if mode:
                extracted.chmod(mode & 0o7777)

    @staticmethod
    def _ensure_executable(launcher: Path) -> None:
        """Guarantee the SQLcl launcher is runnable even if the archive carried
        no mode bits (e.g. a Windows-built zip), so version detection, which
        runs the executable, succeeds right after an upgrade."""
        if launcher.exists():
            current = launcher.stat().st_mode
            launcher.chmod(current | 0o111)

    def _sqlcl_backup_dir(self, sqlcl_dir: Path) -> Path:
        version = self._sqlcl_version()  # type: ignore[attr-defined]
        suffix = version.replace(".", "-") if version else "backup"
        return sqlcl_dir.parent / f"sqlcl{suffix}"

    def _updated_outcome(self, version: str) -> str:
        return f"UPDATED TO {version}" if version else "UPDATED"

    def _parse_pip_upgrade(self, output: str) -> tuple[str, bool]:
        if "Successfully installed" in output:
            match = re.search(r"adt[-_]ai-(\S+)", output)
            return (match.group(1) if match else self.package_version), True  # type: ignore[attr-defined]
        return self.package_version, False  # type: ignore[attr-defined]
