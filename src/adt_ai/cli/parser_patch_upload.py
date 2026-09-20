"""`patch -upload`'s own flags, declared where `parser_patch.py` calls for them.

Was `parser_live_upload.py` until ADT #903 folded the command into `patch`. It
stayed a file of its own for the reason its docs page did: `parser_patch.py` was
already within 1300 bytes of the context-size cap
(`tests/contracts/test_context_file_size.py`), and the verb and its four knobs
are one surface a reader can take in on its own.

Two entry points rather than one, because a help section renders its rows in
PARSER DECLARATION ORDER (`cli/help.py`). The verb belongs among the ACTIONS,
between `-install` and `-archive`; the four knobs belong at the end of the
MODIFIERS, after `-app`. One call could not put them in both places.
"""

from __future__ import annotations

import argparse


def add_upload_verb(patch: argparse.ArgumentParser) -> None:
    """The verb, declared among `patch`'s other ACTIONS.

    Jan, 2026-09-20, on chips: `live_upload` was one verb on a folder of static
    files, and every verb that ships something to an environment is a `patch`
    verb. It keeps its own docs page (`docs/patch_upload.md`) rather than growing
    `docs/patch.md`.

    An ACTION beside `-install`, `-archive` and `-drop`: it reads no commit, so
    it returns before the commit store is levelled, and it names no patch folder.
    """
    patch.add_argument(
        "--upload",
        "-upload",
        action = "store_true",
        help   = "upload the application's static files into APEX as you save them, "
                 "or the whole folder once with -once",
    )


def add_upload_flags(patch: argparse.ArgumentParser) -> None:
    """The four knobs the verb reads, declared at the end of the MODIFIERS.

    Jan chose to reuse `patch`'s own `-app` and `-files_ws` rather than carry
    `live_upload`'s `-app` and `-workspace` spellings of them, so these four are
    all that needed a home. Each is refused on a run without the verb
    (`patch_build.upload_flag_refusal`): a flag that parses and does nothing is
    not shipped (SOP §Command surface).
    """
    patch.add_argument(
        "--folder",
        "-folder",
        metavar = "PATH",
        help    = "with -upload, folder to watch instead of the exported static files folder",
    )
    # `-interval` paces the watch and `-once` replaces the watch, so the pair is
    # refused by the parser rather than accepted with one of them doing nothing.
    # Carried over from `parser_live_upload.py` unchanged.
    mode = patch.add_mutually_exclusive_group()
    mode.add_argument(
        "--interval",
        "-interval",
        type    = int,
        metavar = "SECONDS",
        help    = "with -upload, seconds to wait between passes over the folder, one by default",
    )
    mode.add_argument(
        "--once",
        "-once",
        action = "store_true",
        help   = "with -upload, upload every file in the folder once and exit "
                 "instead of watching",
    )
    patch.add_argument(
        "--show",
        "-show",
        action = "store_true",
        help   = "with -upload, list what the folder already holds before the watch starts",
    )
