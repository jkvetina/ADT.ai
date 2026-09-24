#!/usr/bin/env python3
"""Download a published release's files from PyPI, waiting out a cold CDN.

`release.yml`'s `verify the published artifact` job makes the claim the whole
workflow exists for: what PyPI serves back is byte-for-byte what CI built. To
compare, it first has to HOLD the published files, and PyPI accepts an upload
minutes before every CDN edge will serve it.

v1.4.0 (ADT `#915`) spent 10:38 failing that fetch on a wheel that downloaded
cleanly at 13:06, and the identical manual re-run passed. The step it replaced
asked `pip` for the INDEX — a page an edge answers from cache — and retried the
wheel only: the sdist fetch sat below the loop with no retry at all, so a cold
edge on that side failed the release outright.

This asks the JSON API which files the version HAS and then fetches each one
from its own URL. A file URL answers 404 until its bytes are really servable
and 200 once they are, which is the question the job needs answered, and both
distributions wait under one budget.

    python .github/scripts/await_pypi_files.py --package adt-ai \
        --version 1.4.0 --dest published
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

#: A release is a wheel and an sdist. A listing carrying one of them is an
#: upload still in flight, never a complete release to compare against.
REQUIRED_PACKAGE_TYPES = ("bdist_wheel", "sdist")

#: v1.4.0 was still cold after 10:38 of waiting and warm by 13:06, so ten
#: minutes is the one budget measured to be too small.
DEFAULT_TIMEOUT_SECONDS = 1800
DEFAULT_INTERVAL_SECONDS = 15

API_TEMPLATE = "https://pypi.org/pypi/{package}/{version}/json"

Opener = Callable[[str], bytes]


class IncompleteRelease(Exception):
    """PyPI has not listed the whole release yet — keep waiting, and say why."""


def _read(url: str) -> bytes:
    """Fetch `url`, turning an absent file into the FileNotFoundError this
    module treats as "not servable from this edge yet"."""
    request = urllib.request.Request(url, headers={"User-Agent": "adt-ai-release"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return response.read()
    except urllib.error.HTTPError as error:
        if error.code in (403, 404, 503):
            raise FileNotFoundError(url) from error
        raise
    except urllib.error.URLError as error:
        raise FileNotFoundError(url) from error


def published_files(
    *, package: str, version: str, opener: Opener
) -> list[tuple[str, str]]:
    """The `(filename, url)` pairs PyPI lists for `version`.

    Raises `IncompleteRelease` while the API has not listed the release yet, or
    lists only one distribution; the caller keeps waiting on both.
    """
    api = API_TEMPLATE.format(package=package, version=version)
    try:
        payload = json.loads(opener(api).decode("utf-8"))
    except FileNotFoundError:
        raise IncompleteRelease(f"no PyPI listing for {package} {version}") from None
    files = payload.get("urls") or []
    kinds = {str(entry.get("packagetype")) for entry in files}
    absent = [kind for kind in REQUIRED_PACKAGE_TYPES if kind not in kinds]
    if absent:
        raise IncompleteRelease(
            f"{package} {version} lists no " + " and no ".join(absent)
        )
    return [(str(entry["filename"]), str(entry["url"])) for entry in files]


def await_files(
    *,
    package: str,
    version: str,
    dest: Path,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    interval: float = DEFAULT_INTERVAL_SECONDS,
    opener: Opener = _read,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[Path]:
    """Write every file of `version` into `dest`, waiting out a cold edge.

    Raises `TimeoutError` naming what never arrived once `timeout` seconds of
    waiting have passed: this job's verdict is the release's, so an unanswered
    fetch is a failure and never a shrug.
    """
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    deadline = clock() + timeout
    written: dict[str, Path] = {}
    missing: list[str] = [f"no PyPI listing for {package} {version}"]

    while True:
        try:
            listing = published_files(package=package, version=version, opener=opener)
        except IncompleteRelease as pending:
            missing = [str(pending)]
        else:
            missing = []
            for filename, url in listing:
                if filename in written:
                    continue
                try:
                    payload = opener(url)
                except FileNotFoundError:
                    missing.append(filename)
                    continue
                target = dest / filename
                target.write_bytes(payload)
                written[filename] = target
            if not missing:
                return [written[name] for name in sorted(written)]
        if clock() >= deadline:
            raise TimeoutError(
                f"PyPI did not serve {package} {version} within {timeout:.0f}s: "
                + ", ".join(missing)
            )
        for name in missing:
            print(f"still waiting on PyPI: {name}", flush=True)
        sleep(interval)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download a published release from PyPI.")
    parser.add_argument("--package", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--dest", required=True, type=Path)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL_SECONDS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        files = await_files(
            package=args.package,
            version=args.version,
            dest=args.dest,
            timeout=args.timeout,
            interval=args.interval,
        )
    except TimeoutError as error:
        print(f"::error::{error}", file=sys.stderr)
        return 1
    for path in files:
        print(f"downloaded {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
