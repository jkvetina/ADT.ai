from __future__ import annotations

import re
import urllib.error
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from html import unescape
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any, Protocol

from adt_ai.shared.env_check import CheckResult, _first_line, _instant_client_version
from adt_ai.shared.file_list import file_rows

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
# The SQLcl zip link is scraped from a remote HTML page, so a spoofed or
# tampered page could point the download at an attacker-controlled host. Only
# Oracle's official download host is trusted for the binary itself.
SQLCL_DOWNLOAD_HOST = "download.oracle.com"
JAVA_DOWNLOAD_PAGE = "https://www.oracle.com/java/technologies/downloads/"
INSTANT_CLIENT_PAGE = "https://www.oracle.com/database/technologies/instant-client.html"
PYPI_PACKAGE_URL = "https://pypi.org/pypi/{package}/json"
ADT_AI_GITHUB_LATEST_RELEASE_URL = "https://api.github.com/repos/jkvetina/ADT.ai/releases/latest"
# Where a pinned `-update <version>` looks for the release when the install is a
# plain package rather than a git checkout. The public repo carries a `v<version>`
# tag per release, so one ref names every version in both directions.
ADT_AI_GITHUB_REPO_URL = "https://github.com/jkvetina/ADT.ai"

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
# path_objects: database/<schema>/<object_type>

# Alternative export layout example:
# path_objects: <schema>/database/<object_type>

# Optional external connection and wallet locations:
# connections:
#   path: /secure/path/connections
#   file: SALES.yaml
#   wallet_path: /secure/path/connections/wallets
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
    """`doctor -init`'s `CREATED:`/`SKIPPED:` groups, one file per row.

    The rows go through the shared renderer since ADT #504, at the depth below
    the group label, so this block and the `patch` sections share one indent rule.
    Flat rather than grouped by folder: a scaffold is a handful of root-relative
    paths and the reader is checking that each one exists, which a folder line
    would put a level further from the eye.
    """
    if not paths:
        return []
    root_name = root.name or root.anchor.rstrip("/") or "."
    ordered = sorted(paths, key=lambda path: path.as_posix().lstrip(".").lower())
    return [
        "",
        f"  {label}:",
        *file_rows(
            [f"{root_name}/{path.as_posix()}" for path in ordered],
            nested = False,
            depth  = 2,
        ),
    ]


@dataclass(frozen=True)
class DoctorRequest:
    update: bool = False
    # The release `-update` was asked to land on, or None for the latest one.
    # It scopes to the ADT.ai step alone; requirements and SQLcl are unaffected.
    update_version: str | None = None
    sqlcl : bool = False
    offline: bool = False
    init  : bool = False
    root  : Path | None = None
    force : bool = False
    # The project config, when the caller could load one. Doctor diagnoses a
    # setup before any config exists, so this stays optional and a check reading
    # it reports nothing rather than failing when it is absent.
    config: dict[str, Any] | None = None


@dataclass(frozen=True)
class DoctorResult:
    lines            : list[str]
    performed_actions: list[str]
    exit_code        : int = 0


class DoctorHost(Protocol):
    """The concrete runner surface consumed by the Doctor mixins."""

    command_runner        : CommandRunner
    executable_resolver   : ExecutableResolver
    env                   : Mapping[str, str]
    package_version       : str
    oracledb_version      : str | None
    oracle_mode           : str | None
    instant_client        : str | None
    package_root          : Path
    resource_root         : Traversable
    python_executable     : str
    oracle_page_fetcher   : OraclePageFetcher
    file_downloader       : FileDownloader
    latest_version_fetcher: LatestVersionFetcher | None
    line_callback         : LineCallback | None
    action_reporter       : ActionReporter | None
    version_cache_dir     : Path | None
    version_cache_ttl     : float
    _package_root_is_checkout: bool
    _stale_components     : set[str]

    def _add(self, lines: list[str], line: str) -> None: ...
    def _extend(self, lines: list[str], new_lines: Iterable[str]) -> None: ...
    def _begin_action(self, label: str) -> None: ...
    def _end_action(self, lines: list[str], label: str, outcome: str) -> str: ...
    def _fail_action(self, lines: list[str], label: str, detail: str) -> DoctorResult: ...
    def _command_env(self) -> dict[str, str]: ...
    def _check_results(self) -> list[CheckResult]: ...
    def _adt_ai_version(self, repo_root: Path | None) -> str: ...
    def _is_git_repo(self) -> bool: ...
    def _git_repo_root(self) -> Path: ...
    def _git_head(self, repo_root: Path) -> str: ...
    def _latest_sqlcl_release(self) -> SqlclRelease: ...
    def _sqlcl_home(self) -> Path: ...
    def _sqlcl_version(self) -> str: ...


@dataclass(frozen=True)
class SqlclRelease:
    version     : str
    download_url: str


def _normalize_git_version(value: str) -> str:
    return re.sub(r"^git version\s+", "", value, flags=re.IGNORECASE)


def _normalize_java_version(value: str) -> str:
    return re.sub(r"^(?:java|openjdk)\s+", "", value, flags=re.IGNORECASE)


def _normalize_oracledb_version(value: str) -> str:
    return re.sub(r"^oracledb\s+", "", value, flags=re.IGNORECASE)


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", version))


# A release number, optionally spelled with the tag's own `v` prefix.
_TARGET_VERSION_RE = re.compile(r"^v?\d+(?:\.\d+)*$", re.IGNORECASE)


def _normalize_target_version(value: str) -> str:
    """The bare release number `-update <version>` asked for, or "" when the
    value is not a version at all.

    The empty return is what keeps a typo (`-update latest`) from reaching git as
    a ref: the value becomes an argument to `git checkout`, so a token starting
    with a dash would be read as a flag, and a token naming a branch would move
    the checkout somewhere no release lives.
    """
    text = str(value).strip()
    if not _TARGET_VERSION_RE.match(text):
        return ""
    return text.removeprefix("v").removeprefix("V")


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


def _certificate_error(url: str, error: Exception) -> RuntimeError:
    """Build a remediation error for a TLS certificate-verification failure.

    Previously the fetch/download helpers retried with ``curl --location`` on a
    cert failure, quietly routing around Python's own certificate validation.
    Instead we surface the failure with a fix: the usual cause is that this
    Python has no root certificates installed, not a genuinely bad server cert.
    """
    return RuntimeError(
        f"TLS certificate verification failed for {url}: {error}. "
        "Python could not verify the server's certificate. Install root "
        'certificates for this Python (on macOS run the "Install '
        'Certificates.command" bundled with your python.org install, or '
        "`pip install --upgrade certifi`), then retry. ADT.ai will not bypass "
        "certificate verification."
    )


# unescape is re-exported so version_check.py can import it from here
__all__ = [
    "ActionReporter",
    "ADT_AI_GITHUB_LATEST_RELEASE_URL",
    "ADT_AI_GITHUB_REPO_URL",
    "ACTION_LINE_WIDTH",
    "STATUS_LINE_WIDTH",
    "STATUS_LABEL_WIDTH",
    "SQLCL_DOWNLOAD_PAGE",
    "SQLCL_DOWNLOAD_HOST",
    "JAVA_DOWNLOAD_PAGE",
    "INSTANT_CLIENT_PAGE",
    "PYPI_PACKAGE_URL",
    "PROJECT_CONFIG_TEMPLATE",
    "CommandRunner",
    "ExecutableResolver",
    "OraclePageFetcher",
    "FileDownloader",
    "LineCallback",
    "LatestVersionFetcher",
    "DoctorRequest",
    "DoctorResult",
    "DoctorHost",
    "SqlclRelease",
    "format_action_line",
    "format_status_line",
    "_init_group_lines",
    "_first_line",
    "_instant_client_version",
    "_normalize_git_version",
    "_normalize_java_version",
    "_normalize_oracledb_version",
    "_version_key",
    "_normalize_target_version",
    "_is_newer_version",
    "_is_certificate_error",
    "_certificate_error",
    "unescape",
]
