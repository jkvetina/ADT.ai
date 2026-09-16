"""The watch loop: look at the folder, upload what moved, sleep, look again.

Polling rather than a filesystem event API, exactly as old ADT did it. The
folder is small, the interval is the user's, and a poll behaves the same on a
network share and inside a container, where the event APIs quietly deliver
nothing.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from adt_ai.live_upload import files, queries
from adt_ai.live_upload.minify import Minifiers
from adt_ai.shared import text_files
from adt_ai.shared.db import QueryGateway
from adt_ai.shared.file_list import print_file_rows

# Through the `progress` facade rather than `fixed_width` directly: that module
# re-exports the printer at its own foot, so importing the other way round hits
# a partially initialised module (`progress.py:348`).
from adt_ai.shared.progress import FixedWidthProgressPrinter

# What closes an upload row. The work has no count of its own: one file went up.
UPLOADED_STATUS = "OK"


@dataclass(frozen=True)
class LiveUploadRequest:
    folder    : Path
    app_id    : int
    workspace : bool = False
    interval  : int = 1
    show      : bool = False


@dataclass(frozen=True)
class LiveUploadResult:
    uploaded : int
    minified : int
    failed   : int = 0


class LiveUploadReporter(Protocol):
    def listing(self, names: Sequence[str]) -> None: ...

    def uploading(self, name: str) -> None: ...

    def uploaded(self, name: str) -> None: ...

    def upload_failed(self, name: str) -> None: ...

    def minify_failed(self, name: str) -> None: ...


class ConsoleLiveUploadReporter:
    """One streamed row per file, opened before the upload and closed after it.

    The open line is also what the console guard reads as the announcement, so
    the row has to be started before the statement runs rather than printed once
    it comes back (`shared/announce.py`).
    """

    def __init__(self, progress: FixedWidthProgressPrinter) -> None:
        self.progress = progress

    def listing(self, names: Sequence[str]) -> None:
        print_file_rows(names, nested=False)

    def uploading(self, name: str) -> None:
        self.progress.begin(name)

    def uploaded(self, name: str) -> None:
        self.progress.status(name, UPLOADED_STATUS)

    def upload_failed(self, name: str) -> None:
        # `uploading()` already opened this row; `status()` only ever closes
        # the row `begin()` opened; it never prints a label of its own, so
        # completing THIS row is what puts the name on the line (#670).
        self.progress.status(name, "UPLOAD FAILED")

    def minify_failed(self, name: str) -> None:
        # Minifying happens after `uploaded()` already closed the upload row,
        # so this failure gets a row of its own rather than reusing a closed
        # one (#670).
        self.progress.begin(name)
        self.progress.status(name, "MINIFY FAILED")


class LiveUploadRunner:
    def __init__(
        self,
        gateway: QueryGateway,
        *,
        reporter: LiveUploadReporter,
        minifiers: Minifiers | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        self.gateway = gateway
        self.reporter = reporter
        self.minifiers = minifiers
        # Resolved here rather than as a default argument, which would bind
        # `time.sleep` once at import and leave a test with no way to end the
        # loop it is driving.
        self.sleep = sleep if sleep is not None else time.sleep

    def run(self, request: LiveUploadRequest) -> LiveUploadResult:
        """Watch until the user interrupts, and report what went up.

        The folder is scanned once before the loop and nothing in that first
        scan is uploaded: the command exists to ship what you save while it
        runs, and pushing a whole folder on startup would overwrite APEX with
        whatever the repository happened to hold.
        """
        known = self._start(request)

        uploaded = 0
        minified = 0
        while True:
            try:
                for path, stamp in files.changed(request.folder, known).items():
                    known[path] = stamp
                    name = files.upload_name(request.folder, path)
                    # One bad file must not take the whole watch down: caught
                    # per file so a gateway error or an unreadable save is
                    # reported and the pass moves on to the next one (#670).
                    # `Exception` only, so Control+C still reaches the outer
                    # `except KeyboardInterrupt` below and ends the watch.
                    try:
                        self._upload(request, path, name)
                    except Exception:
                        self.reporter.upload_failed(name)
                        continue
                    uploaded += 1
                    if self._minify(path, name):
                        minified += 1
                self.sleep(request.interval)
            except KeyboardInterrupt:
                break
        return LiveUploadResult(uploaded=uploaded, minified=minified)

    def upload_all(self, request: LiveUploadRequest) -> LiveUploadResult:
        """Upload every file the folder holds, once, and return (`-once`).

        Nothing is minified: a one-shot push ships the folder as it is on disk
        and must not write into the repository it reads. A `.min.` file already
        there is uploaded like any other. A failed file is counted and the rest
        still go up, so the caller can exit non-zero once the pass is done.
        """
        known = self._start(request)
        uploaded = 0
        failed = 0
        for path in known:
            name = files.upload_name(request.folder, path)
            try:
                self._upload(request, path, name)
            except Exception:
                self.reporter.upload_failed(name)
                failed += 1
                continue
            uploaded += 1
        return LiveUploadResult(uploaded=uploaded, minified=0, failed=failed)

    def _start(self, request: LiveUploadRequest) -> dict[Path, float]:
        """Bind the session to the application, and read what the folder holds."""
        self.gateway.execute(queries.APEX_SECURITY_CONTEXT, {"app_id": request.app_id})
        known = files.scan(request.folder)
        if request.show:
            self.reporter.listing([files.upload_name(request.folder, path) for path in known])
        return known

    def _upload(self, request: LiveUploadRequest, path: Path, name: str) -> None:
        self.reporter.uploading(name)
        params: dict[str, object] = {
            "name"    : name,
            "mime"    : files.mime_type(path),
            "payload" : path.read_bytes(),
        }
        if request.workspace:
            self.gateway.execute(queries.UPLOAD_WORKSPACE_FILE, params)
        else:
            self.gateway.execute(queries.UPLOAD_APP_FILE, {"app_id": request.app_id, **params})
        self.reporter.uploaded(name)

    def _minify(self, path: Path, name: str) -> bool:
        """Write the minified sibling, and leave uploading it to the next pass.

        The sibling is a new file in the watched folder, so the loop finds it
        the way it finds anything else you save. That is how one edit ships both
        copies, and it is old ADT's own behaviour rather than a shortcut.
        """
        target = files.minified_target(path)
        minifier = self.minifiers.for_suffix(path.suffix) if self.minifiers else None
        if target is None or minifier is None:
            return False
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # The raw bytes already reached APEX in `_upload`; minifying a file
            # that is not UTF-8 (a Latin-1 `.css`, say) would need a guess at
            # its real encoding, so skip the sibling and say so rather than
            # crash the whole watch over one file (#670).
            self.reporter.minify_failed(name)
            return False
        text_files.write_text(target, minifier(text))
        return True
