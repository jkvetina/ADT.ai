from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from pathlib import Path

from adt_ai.doctor._base import (
    ADT_AI_GITHUB_REPO_URL,
    DoctorResult,
    _normalize_target_version,
    _version_key,
)


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

    def _upgrade_adt_ai(
        self,
        lines         : list[str],
        target_version: str | None = None,
    ) -> DoctorResult:
        """Move the ADT.ai install to the latest release, or to a named one.

        `target_version` is what `-update <version>` was given. It is a request
        for that exact release and nothing else, so a version older than the one
        installed is checked out like any other: which way the version number
        moves is the user's business, not this command's.
        """
        label = "ADT.ai"
        self._begin_action(label)  # type: ignore[attr-defined]
        release = ""
        if target_version is not None:
            release = _normalize_target_version(target_version)
            if not release:
                return self._fail_action(  # type: ignore[attr-defined]
                    lines,
                    label,
                    f"not a version: {target_version}. Pass a release number such as "
                    "0.9.1, or leave -update bare to take the latest release.",
                )
        if self._is_git_repo():  # type: ignore[attr-defined]
            return self._upgrade_adt_ai_checkout(lines, label, release)
        return self._upgrade_adt_ai_package(lines, label, release)

    def _upgrade_adt_ai_checkout(
        self,
        lines  : list[str],
        label  : str,
        release: str,
    ) -> DoctorResult:
        stashed = False
        repo_root: Path | None = None
        try:
            repo_root = self._git_repo_root()  # type: ignore[attr-defined]
            head_before = self._git_head(repo_root)  # type: ignore[attr-defined]
            tag = ""
            if release:
                # Resolved before anything is stashed or moved, so a version with
                # no release leaves the checkout exactly where it was.
                self._git(repo_root, "fetch", "--tags", "--force", "origin")
                tag = self._release_tag(repo_root, release)
                if not tag:
                    origin = self._origin_url(repo_root)
                    return self._fail_action(  # type: ignore[attr-defined]
                        lines,
                        label,
                        f"no release {release} in {origin}: it carries no tag "
                        f"v{release} and no tag {release}.",
                    )
            # The user may have local edits in the ADT.ai checkout. Stash them
            # (tracked + untracked) so the update lands cleanly, then re-apply
            # them on top of it.
            dirty = bool(self._git(repo_root, "status", "--porcelain").strip())
            if dirty:
                self._git(
                    repo_root,
                    "stash",
                    "push",
                    "--include-untracked",
                    "-m",
                    "adtai-update-autostash",
                )
                stashed = True
            if tag:
                self._git(repo_root, "checkout", tag)
            else:
                # A checkout pinned to a tag sits on a detached HEAD, which
                # `git pull` refuses to run on, so without this the way back to
                # latest would be closed and the pin a one way door.
                if self._git_detached(repo_root):
                    branch = self._remote_default_branch(repo_root)
                    if branch:
                        self._git(repo_root, "checkout", branch)
                self._git(repo_root, "pull")
            head_after = self._git_head(repo_root)  # type: ignore[attr-defined]
            if stashed:
                self._git(repo_root, "stash", "pop")
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

    def _upgrade_adt_ai_package(
        self,
        lines  : list[str],
        label  : str,
        release: str,
    ) -> DoctorResult:
        command = (
            ["pip3", "install", f"git+{ADT_AI_GITHUB_REPO_URL}@v{release}"]
            if release
            else ["pip3", "install", "--upgrade", "adt-ai"]
        )
        try:
            output = self.command_runner(  # type: ignore[attr-defined]
                command,
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

    def _git(self, repo_root: Path | None, *arguments: str) -> str:
        return self.command_runner(  # type: ignore[attr-defined]
            ["git", *arguments],
            repo_root,
            self._command_env(),  # type: ignore[attr-defined]
        )

    def _release_tag(self, repo_root: Path, release: str) -> str:
        """The tag naming this release, preferring the `v` form, or "" for none.

        Both spellings are asked for in one `git tag --list`, so a repo that
        tags either way answers in a single command and a repo that tags neither
        (any checkout that is not the public one) answers with nothing.
        """
        listed = self._git(repo_root, "tag", "--list", f"v{release}", release)
        tags = {line.strip() for line in listed.splitlines() if line.strip()}
        for candidate in (f"v{release}", release):
            if candidate in tags:
                return candidate
        return ""

    def _origin_url(self, repo_root: Path) -> str:
        try:
            return self._git(repo_root, "remote", "get-url", "origin").strip() or "origin"
        except Exception:
            return "origin"

    def _git_detached(self, repo_root: Path) -> bool:
        try:
            return self._git(repo_root, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
        except Exception:
            return False

    def _remote_default_branch(self, repo_root: Path) -> str:
        try:
            listed = self._git(repo_root, "ls-remote", "--symref", "origin", "HEAD")
        except Exception:
            return ""
        match = re.search(r"refs/heads/(\S+)", listed)
        return match.group(1) if match else ""

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
