from __future__ import annotations

import re
import shutil
import tempfile
import zipfile
from importlib import resources
from pathlib import Path

from adt_ai.doctor._base import (
    ADT_AI_GITHUB_REPO_URL,
    DoctorResult,
    _normalize_target_version,
    _version_key,
)


def _matching_tag(tags: set[str], release: str) -> str:
    """The tag `release` names, or the newest tag on the LINE it names, or "".

    An exact spelling wins outright when it exists, `v{release}` preferred
    over the bare form, matching what `_release_tag` has always done for a
    fully-specified release. Failing that, every tag whose own release prefix
    equals `release` at a genuine version-segment boundary is a candidate
    (`0.3` names the line `v0.3.0`/`v0.3.1`/... starts, never `v0.30.0`, which
    shares the string `0.3` but not the version), and the highest by
    `_version_key` wins, the answer to "the newest 0.3.x release" once `tags`
    actually carries the 0.3.x line.
    """
    exact = {candidate for candidate in (f"v{release}", release) if candidate in tags}
    if exact:
        return f"v{release}" if f"v{release}" in exact else release
    prefix = f"{release}."
    matching = [tag for tag in tags if tag.removeprefix("v").startswith(prefix)]
    return max(matching, key=_version_key) if matching else ""


class DoctorUpgradeMixin:
    def _pip(self, *arguments: str) -> list[str]:
        """Run pip in the interpreter that imported and is running ADT.ai."""
        return [self.python_executable, "-m", "pip", *arguments]  # type: ignore[attr-defined]

    def _install_requirements(self, lines: list[str]) -> DoctorResult:
        label = "Python requirements"
        requirements_resource = self.resource_root.joinpath("requirements.txt")  # type: ignore[attr-defined]
        self._begin_action(label)  # type: ignore[attr-defined]
        if not requirements_resource.is_file():
            return self._fail_action(  # type: ignore[attr-defined]
                lines,
                label,
                f"requirements.txt missing: {requirements_resource}",
            )
        try:
            with resources.as_file(requirements_resource) as requirements:
                output = self.command_runner(  # type: ignore[attr-defined]
                    self._pip("install", "-r", str(requirements), "--upgrade"),
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
                    wanted = (
                        f"v{release}.* or {release}.*"
                        if release.count(".") < 2
                        else f"v{release} or {release}"
                    )
                    return self._fail_action(  # type: ignore[attr-defined]
                        lines,
                        label,
                        f"no release {release} in {origin}: it carries no tag matching {wanted}.",
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
                self._pip("install", "-e", str(repo_root)),
                None,
                self._command_env(),  # type: ignore[attr-defined]
            )
        except Exception as error:
            if stashed:
                restore_message = (
                    "    Local changes saved in git stash; run `git stash pop` to restore them."
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
        if release and release.count(".") < 2:
            # A short version (`0.3`) names a LINE, and pip's `git+URL@ref`
            # cannot resolve an ambiguous ref itself, so the exact tag is
            # settled first, against the public repo, the only one that ever
            # carries a release tag.
            tag = self._public_release_tag(release)
            if not tag:
                return self._fail_action(  # type: ignore[attr-defined]
                    lines,
                    label,
                    f"no release {release} in {ADT_AI_GITHUB_REPO_URL}: it carries no "
                    f"tag matching v{release}.* or {release}.*.",
                )
            return self._run_pip_install(lines, label, f"git+{ADT_AI_GITHUB_REPO_URL}@{tag}")
        if not release:
            return self._run_pip_install(lines, label, "--upgrade", "adt-ai")
        # A fully-specified release still tries the `v`-prefixed spelling
        # first and falls back to the bare one, the two spellings a real tag
        # has ever used, with no extra network round trip to tell them apart.
        try:
            output = self.command_runner(  # type: ignore[attr-defined]
                self._pip("install", f"git+{ADT_AI_GITHUB_REPO_URL}@v{release}"),
                None,
                self._command_env(),  # type: ignore[attr-defined]
            )
        except Exception:
            return self._run_pip_install(lines, label, f"git+{ADT_AI_GITHUB_REPO_URL}@{release}")
        return self._pip_install_result(lines, label, output)

    def _public_release_tag(self, release: str) -> str:
        """`_matching_tag` against the PUBLIC repo's own tags, fetched with no
        local checkout: `pip install` never has one to ask."""
        listed = self.command_runner(  # type: ignore[attr-defined]
            ["git", "ls-remote", "--tags", ADT_AI_GITHUB_REPO_URL],
            None,
            self._command_env(),  # type: ignore[attr-defined]
        )
        tags = {
            ref.rsplit("refs/tags/", 1)[-1]
            for ref in listed.splitlines()
            if "refs/tags/" in ref and not ref.endswith("^{}")
        }
        return _matching_tag(tags, release)

    def _run_pip_install(self, lines: list[str], label: str, *args: str) -> DoctorResult:
        try:
            output = self.command_runner(  # type: ignore[attr-defined]
                self._pip("install", *args),
                None,
                self._command_env(),  # type: ignore[attr-defined]
            )
        except Exception as error:
            return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]
        return self._pip_install_result(lines, label, output)

    def _pip_install_result(self, lines: list[str], label: str, output: str) -> DoctorResult:
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
        """The tag naming this release, or the newest one on that line, or "".

        A fully-specified release (`0.9.1`, three parts) asks for exactly the
        two spellings `git tag --list` might carry, unchanged from before this
        query ever had to consider anything shorter. A shorter one (`0.3`,
        `-update`'s whole point per Jan, 2026-08-24: *"any version support
        upgrading to latest version and from that he can downgrade to anything
        again"*) asks the same two plus their `.*` extensions, since ADT.ai has
        never tagged a two-part release and never will, every real tag is
        three parts, so a two-part ask is a LINE, not a release, and
        `_matching_tag` picks the newest one on it.
        """
        queries = [f"v{release}", release]
        if release.count(".") < 2:
            queries = [f"v{release}.*", f"{release}.*", *queries]
        listed = self._git(repo_root, "tag", "--list", *queries)
        tags = {line.strip() for line in listed.splitlines() if line.strip()}
        return _matching_tag(tags, release)

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
        restored = False
        try:
            # Stage beside the live install so the final rename stays on one
            # filesystem. The old install is not touched until the archive has
            # been extracted and its launcher has been validated.
            with tempfile.TemporaryDirectory(
                prefix=".adtai-sqlcl-",
                dir=sqlcl_parent,
                ignore_cleanup_errors=True,
            ) as temp_dir:
                staging_dir = Path(temp_dir)
                sqlcl_zip = staging_dir / Path(release.download_url).name
                extracted_dir = staging_dir / "extracted"
                self.file_downloader(release.download_url, sqlcl_zip)  # type: ignore[attr-defined]
                with zipfile.ZipFile(sqlcl_zip) as archive:
                    self._extract_archive(archive, extracted_dir)

                candidate_dir = extracted_dir / "sqlcl"
                candidate_launcher = candidate_dir / "bin" / "sql"
                if not candidate_dir.is_dir() or not candidate_launcher.is_file():
                    raise RuntimeError("Downloaded SQLcl archive has no sqlcl/bin/sql launcher")
                self._ensure_executable(candidate_launcher)

                stale_backup = staging_dir / "previous-backup"
                stale_backup_saved = False
                current_backed_up = False
                promotion_attempted = False
                try:
                    if backup_dir.exists():
                        shutil.move(str(backup_dir), str(stale_backup))
                        stale_backup_saved = True
                    if sqlcl_dir.exists():
                        shutil.move(str(sqlcl_dir), str(backup_dir))
                        current_backed_up = True
                    promotion_attempted = True
                    shutil.move(str(candidate_dir), str(sqlcl_dir))
                except Exception as promotion_error:
                    rollback_errors: list[str] = []
                    if promotion_attempted and sqlcl_dir.exists():
                        try:
                            self._remove_path(sqlcl_dir)
                        except Exception as rollback_error:
                            rollback_errors.append(str(rollback_error))
                    if current_backed_up and backup_dir.exists():
                        try:
                            shutil.move(str(backup_dir), str(sqlcl_dir))
                            restored = True
                        except Exception as rollback_error:
                            rollback_errors.append(str(rollback_error))
                    if stale_backup_saved and stale_backup.exists():
                        try:
                            shutil.move(str(stale_backup), str(backup_dir))
                        except Exception as rollback_error:
                            rollback_errors.append(str(rollback_error))
                    if rollback_errors:
                        detail = "; ".join(rollback_errors)
                        raise RuntimeError(
                            f"{promotion_error}; rollback failed: {detail}"
                        ) from promotion_error
                    raise
            self._end_action(lines, label, f"UPGRADED TO {release.version}")  # type: ignore[attr-defined]
            return DoctorResult(lines=[], performed_actions=["sqlcl"], exit_code=0)
        except Exception as error:
            if restored:
                self._add(lines, "    Restored previous SQLcl backup.")  # type: ignore[attr-defined]
            return self._fail_action(lines, label, str(error))  # type: ignore[attr-defined]

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)

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
