from __future__ import annotations

import json
import re
import time
import urllib.parse
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from adt_ai.doctor._base import (
    ADT_AI_GITHUB_LATEST_RELEASE_URL,
    INSTANT_CLIENT_PAGE,
    JAVA_DOWNLOAD_PAGE,
    PYPI_PACKAGE_URL,
    SQLCL_DOWNLOAD_HOST,
    SQLCL_DOWNLOAD_PAGE,
    DoctorHost,
    SqlclRelease,
    unescape,
)
from adt_ai.shared import text_files


class DoctorLatestVersionMixin(DoctorHost):
    """Resolve the latest remote version of each tracked component.

    The online lookups are the slow part of a `doctor` run, so they are pulled
    out of the display mixin into this dedicated unit: it owns the concurrency
    (one worker per pending check), the short-TTL disk cache, and the per-source
    scraping. `DoctorVersionMixin` composes this and only consumes the resolved
    values via `self`.
    """

    def _needs_online_check(self, current_version: str) -> bool:
        return bool(current_version) and current_version not in {
            "unknown",
            "not found",
            "not installed",
        }

    def _prefetch_latest_versions(
        self,
        components: list[tuple[str, str]],
        *,
        online: bool,
    ) -> None:
        # Submit each gated online check to its own worker so the fetches overlap.
        # `_latest_version` then reads the in-flight future instead of fetching
        # serially, while components that do not need an online check (offline run,
        # or a missing/unknown current version) are skipped just as before.
        self._latest_version_futures: dict[str, Future[str]] = {}
        self._version_executor: ThreadPoolExecutor | None = None
        if not online:
            return
        pending = [
            component for component, current in components if self._needs_online_check(current)
        ]
        if not pending:
            return
        executor = ThreadPoolExecutor(max_workers=len(pending), thread_name_prefix="adtai-doctor")
        self._version_executor = executor
        for component in pending:
            self._latest_version_futures[component] = executor.submit(
                self._fetch_latest_version, component
            )

    def _shutdown_version_executor(self) -> None:
        executor = getattr(self, "_version_executor", None)
        if executor is not None:
            executor.shutdown(wait=True)
            self._version_executor = None

    def _latest_version(self, component: str) -> str:
        futures: dict[str, Future[str]] | None = getattr(
            self, "_latest_version_futures", None
        )
        if futures is not None and component in futures:
            return futures[component].result()
        return self._fetch_latest_version(component)

    def _fetch_latest_version(self, component: str) -> str:
        if self.latest_version_fetcher:
            return self.latest_version_fetcher(component)
        cached = self._read_cached_version(component)
        if cached is not None:
            return cached
        version = self._resolve_latest_version(component)
        if version and re.match(r"^\d+\.\d+", version):
            self._write_cached_version(component, version)
        return version

    def _resolve_latest_version(self, component: str) -> str:
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

    def _version_cache_file(self, component: str) -> Path | None:
        cache_dir = getattr(self, "version_cache_dir", None)
        if not cache_dir:
            return None
        return Path(cache_dir) / f"latest_{component.replace('/', '_')}.json"

    def _read_cached_version(self, component: str) -> str | None:
        path = self._version_cache_file(component)
        if path is None:
            return None
        try:
            payload     = json.loads(path.read_text(encoding="utf-8"))
            fetched_at  = float(payload["fetched_at"])
            version     = str(payload["version"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return None
        ttl = getattr(self, "version_cache_ttl", 3600)
        if time.time() - fetched_at > ttl:
            return None
        return version

    def _write_cached_version(self, component: str, version: str) -> None:
        path = self._version_cache_file(component)
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_text(
                path,
                json.dumps({"version": version, "fetched_at": time.time()}),
            )
        except OSError:
            pass

    def _is_git_repo(self) -> bool:
        # A wheel inside a project's .venv is physically below that project's
        # .git directory. It is still a package install, never an editable ADT.ai
        # checkout that doctor may pull, stash, or check out to a release tag.
        if not self._package_root_is_checkout:
            return False
        try:
            inside_work_tree = self.command_runner(
                ["git", "rev-parse", "--is-inside-work-tree"],
                self.package_root,
                self._command_env(),
            ).strip()
            return inside_work_tree == "true"
        except Exception:
            return False

    def _git_repo_root(self) -> Path:
        return Path(
            self.command_runner(
                ["git", "rev-parse", "--show-toplevel"],
                self.package_root,
                self._command_env(),
            ).strip()
        )

    def _git_head(self, repo_root: Path) -> str:
        return self.command_runner(
            ["git", "rev-parse", "HEAD"],
            repo_root,
            self._command_env(),
        ).strip()

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

        download_url = unescape(url_match.group(1))
        parsed = urllib.parse.urlsplit(download_url)
        if parsed.scheme != "https" or parsed.hostname != SQLCL_DOWNLOAD_HOST:
            raise ValueError(
                f"SQLcl download link is not on https://{SQLCL_DOWNLOAD_HOST}: {download_url}"
            )

        return SqlclRelease(version=version, download_url=download_url)

    def _latest_adt_ai_version(self) -> str:
        remote_version = self._adt_ai_remote_git_version()
        if remote_version:
            return remote_version
        github_version = self._latest_adt_ai_github_release_version()
        if github_version:
            return github_version
        return self._latest_pypi_version("adt-ai")

    def _latest_adt_ai_github_release_version(self) -> str:
        try:
            payload = json.loads(self.oracle_page_fetcher(ADT_AI_GITHUB_LATEST_RELEASE_URL))
        except Exception:
            return ""
        version = str(payload.get("tag_name") or payload.get("name") or "").strip()
        return version.removeprefix("v").removeprefix("V")

    def _adt_ai_remote_git_version(self) -> str:
        if not self._is_git_repo():
            return ""
        try:
            repo_root = self._git_repo_root()
            local_head = self._git_head(repo_root)
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
        payload = json.loads(
            self.oracle_page_fetcher(PYPI_PACKAGE_URL.format(package=package))
        )
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
