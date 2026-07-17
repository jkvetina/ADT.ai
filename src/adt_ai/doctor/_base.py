from __future__ import annotations

import re
import urllib.error
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from html import unescape
from pathlib import Path

from adt_ai.shared.env_check import _first_line, _instant_client_version

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
#   file: CORE23.yaml
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


def _normalize_git_version(value: str) -> str:
    return re.sub(r"^git version\s+", "", value, flags=re.IGNORECASE)


def _normalize_java_version(value: str) -> str:
    return re.sub(r"^(?:java|openjdk)\s+", "", value, flags=re.IGNORECASE)


def _normalize_oracledb_version(value: str) -> str:
    return re.sub(r"^oracledb\s+", "", value, flags=re.IGNORECASE)


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
    "_is_newer_version",
    "_is_certificate_error",
    "_certificate_error",
    "unescape",
]
