"""The console runtime's stdout and stderr wrappers.

Split out of `cli/constants.py` by ADT #494, which is the card that first hit
the wall: that file was 171 bytes under the 20 000 byte context guard, so the
next explanation written into this class was always going to break it, and the
guard's own remedy is a bounded module rather than a debt entry (project SOP
'cli.py size, tiny facade + bounded cli_*.py modules').

It is also the honest home. `constants.py` holds parser tables, module lists
and withdrawn-flag sets; this is a stateful writer that decides where the
cursor is and how many newlines are still retractable, which is the question
`shared/announce.py` and `shared/progress.py` both ask it. Both names keep
their leading underscore and `constants.py` re-exports them, so every existing
importer and every test that reaches for the real tracker is unchanged.
"""

from __future__ import annotations

from typing import TextIO


class _StdoutTracker:
    def __init__(self, wrapped: TextIO) -> None:
        self.wrapped = wrapped
        self._pending_newlines = ""
        self._committed_trailing_newlines = 0
        self.had_output = False
        # Set by print_adt_header through shared/announce.py and retired by the
        # blank line that closes the section. A section header is the one
        # announcement that ends its own line, so it is the one the cursor
        # position cannot see.
        self._announced_header = False
        # Set by a redrawable row that has reached its own end, and cleared by
        # the next thing printed. The cursor is still mid-line, so the open-line
        # rule below would read it as an announcement; the row is a result.
        self._open_line_finished = False
        # Has anything printed under the current header yet? A header may lay
        # down its own trailing blank before the first row (`start_export`
        # does), and that blank is part of the header block, not the end of the
        # section it opened.
        self._section_has_body = False
        # How many headers this stream has carried, the module banner included.
        # `print_adt_header` sizes its leading gap off it (ADT #494).
        self._sections_opened = 0
        # Set by run_schema_sections() once every schema segment of a
        # multi-schema command has printed its own TIMER footer, so the
        # runtime's shared teardown does not print a second, grand-total one.
        self.final_timer_emitted = False

    @property
    def sections_opened(self) -> int:
        """How many headers this stream has carried, the module banner included.

        `print_adt_header` sizes its gap off this. A header's second blank
        belongs to whatever stood above it, which is Jan's arithmetic on `#456`,
        and under the banner nothing does: a banner is the command's title, not
        a section. Jan, 2026-08-23: *"Two empty lines above first header is wrong
        (We are not counting module header)"*. The banner is `1`, the run's first
        real section is `2` and is the one call that opens on a single blank, and
        everything from `3` up is separated from a section above it and keeps the
        pair `#468` established.

        **Counted, rather than derived from whether the section above printed
        rows.** That was the first shape of this fix and it reached further than
        the report: on a screen whose connection block renders no version rows it
        also moved `CONNECTING TO SCHEMA <s>:` and `DEPLOYING PATCH:`, headers
        nobody had complained about. `#372` is the standing warning here, *"I did
        not asked you to ADD NEW HEADERS, I asked you to print PRECEEDING
        header!"*, and its general form is that a predicate finer than the
        instruction ends up writing the design.

        A property rather than the raw counter, so the duck-typed `getattr` in
        `print_adt_header` has one name to ask for and a stream that is not this
        tracker degrades to the wider gap instead of raising.
        """
        return self._sections_opened

    @property
    def trailing_newlines(self) -> int:
        return len(self._pending_newlines)

    @property
    def announced(self) -> bool:
        """Does the screen say what the process is doing?

        Two shapes count. A line the cursor is still sitting on is a label
        waiting for its own result, which is what `FixedWidthProgressPrinter.
        begin`, the dotted bar and a streamed table half-row all leave behind:
        nothing has to opt in, because leaving the line open IS the
        announcement. And a section header, which ends its line like any
        finished row and so says so explicitly through `mark_announced()`.

        **A header announces the whole section under it, not just the next
        call.** `#360` cleared the flag on every real write, so a result row
        printed under a header retired that header's claim and the next
        database call read as unannounced. The guard sits on the gateway and
        fires per `fetch_all`/`execute`, so that rule demanded a printed label
        for all 118 of them, and the sweep supplied 32 new ones. Jan, 2026-08-16
        (`#372`): *"I did not asked you to ADD NEW HEADERS, I asked you to print
        PRECEEDING header!"* The header is the announcement; its rows are the
        answer to it, and the blank line under the last of them is where the
        section ends and the claim with it (see `_expire_header_at_section_end`).

        Read from the cursor rather than from the text: `#359` tried to classify
        the last printed line and could not tell a header from a data row, which
        is exactly the case that kept shipping (`#360`).

        Both counters matter. `_pending_newlines` holds the newlines still
        retractable for the TIMER footer, and `commit_pending()` moves them to
        `_committed_trailing_newlines` without moving the cursor back up, so a
        row that closed and then flushed would otherwise read as still open.

        **An open line announces the work that will CLOSE it.** A redrawable row
        holds the cursor mid-line whatever it says, so `ut` covered a 9.9 second
        coverage read with a bar reading `100%  0:00:00` (`#379`, and see
        `shared/announce.py`). `mark_finished()` is how such a row says so.
        """
        if not self.had_output:
            return False
        if self._announced_header:
            return True
        if self._open_line_finished:
            return False
        return not self._pending_newlines and not self._committed_trailing_newlines

    def mark_finished(self) -> None:
        """The open line has finished its own work (shared/announce.py).

        Only a redrawable row needs it: every other open line is closed by the
        result that answers it, and a closed line already reads as a result.
        """
        self._open_line_finished = True

    def mark_announced(self) -> None:
        """Record a header as the newest thing on screen (shared/announce.py).

        The section starts empty: whatever stood under the previous header is
        not this one's body, and the blank a header lays down before its first
        row must not read as that row.

        The count is what `sections_opened` reports, and it is kept here rather
        than in `print_adt_header` for the reason that method's own stdout guard
        gives: a header written to another stream announces nothing about this
        one, and it opens no section on this one either.
        """
        self._announced_header = True
        self._section_has_body = False
        self._sections_opened += 1

    def _expire_header_at_section_end(self) -> None:
        """A blank line under a printed row closes the section, and the claim.

        Two trailing newlines mean a line ended and an empty one followed, which
        is the console's only punctuation for "that subject is finished". Read
        from the counters rather than from the text, for the reason the property
        above gives, and both are summed because `commit_pending()` moves them
        from one to the other: a section that flushed its own trailing blank
        through `print_adt_table` holds it in `_committed_trailing_newlines` and
        nowhere else.

        **The body flag is what tells the two blanks apart.** A header may print
        its own blank before the first row -- `EXPORTING <n> OBJECTS:` does, and
        the DBMS_METADATA setup and comment pre-read that follow it print
        nothing, so that header is all the announcement they get. A blank there
        opens the section; a blank after a row closes it.

        Without any of this the latch was write-once: `mark_announced()` set it,
        the banner every command opens with goes through `print_adt_header`, and
        so `announced` answered `True` from the first line of every run and
        `AnnouncedGateway.guard()` could never fire again (`#372`, inert from
        `d30f088` until this commit).
        """
        if not self._section_has_body:
            return
        if len(self._pending_newlines) + self._committed_trailing_newlines >= 2:
            self._announced_header = False

    def write(self, text: str) -> int:
        if not text:
            return 0
        self.had_output = True
        stripped = text.rstrip("\n")
        if not stripped:
            self._pending_newlines += text
            self._expire_header_at_section_end()
            return len(text)

        trailing_count = len(text) - len(stripped)
        body = text[:-trailing_count] if trailing_count else text
        self._section_has_body = True
        # Whatever a finished row was claiming, this write replaces it: a bar
        # that starts moving again is a new wait with its own open line.
        self._open_line_finished = False
        self._flush_pending()
        self.wrapped.write(body)
        # Flush the visible body immediately. A header line printed right before a
        # long silent operation (e.g. rebuild's per-commit hashing) carries its
        # line-ending newline in _pending_newlines, so without this flush the body
        # stays in the TTY line buffer, invisible until the first progress line
        # commits the pending newline. Flushing only the already-written body keeps
        # the trailing newlines retractable for the shared footer normalizer.
        self.wrapped.flush()
        self._committed_trailing_newlines = 0
        self._pending_newlines = "\n" * trailing_count
        self._expire_header_at_section_end()
        return len(text)

    def normalize_trailing_newlines(self, count: int) -> None:
        # Already-committed trailing newlines count toward the total in BOTH
        # cases. This used to ignore them whenever anything was still pending,
        # so a section that committed its own trailing blank through
        # commit_pending() (print_adt_table does, to flush the table) and
        # then printed one more blank got the committed pair *underneath* the
        # normalized three: `patch -patch 65` printed four empty lines before
        # TIMER where the contract allows two (ADT #269).
        #
        # write() resets the committed count to 0 on any real body text, so
        # outside that window the two branches were always equal anyway, the
        # split was the bug, not a distinction worth keeping.
        self._pending_newlines = "\n" * max(count - self._committed_trailing_newlines, 0)

    def flush(self) -> None:
        # Keep trailing newlines retractable: a mid-stream flush (e.g. the
        # progress bar's print(flush=True)) must not commit the line-ending
        # newlines, or normalize_trailing_newlines() can no longer trim them
        # and the shared footer ends up with an extra blank line. Pending
        # newlines are emitted when real content follows or at finalize().
        self.wrapped.flush()

    def commit_pending(self) -> None:
        self._flush_pending()
        self.wrapped.flush()

    def finalize(self) -> None:
        self.commit_pending()

    def _flush_pending(self) -> None:
        if self._pending_newlines:
            self.wrapped.write(self._pending_newlines)
            self._committed_trailing_newlines = len(self._pending_newlines)
            self._pending_newlines = ""

    def __getattr__(self, name: str) -> object:
        return getattr(self.wrapped, name)


class _StderrTracker(_StdoutTracker):
    def __init__(self, wrapped: TextIO, stdout_tracker: _StdoutTracker) -> None:
        super().__init__(wrapped)
        self._stdout_tracker = stdout_tracker

    def write(self, text: str) -> int:
        if text:
            self._stdout_tracker.commit_pending()
        return super().write(text)
