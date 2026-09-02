from __future__ import annotations

import argparse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Annotation only. `constants` is the CLI's re-export hub and importing it
    # here at runtime would drag the whole monolith into six small parser
    # modules that need one name from it.
    from adt_ai.cli.constants import AdtArgumentParser

#: What `build_parser` hands each `add_*_parsers` below: argparse's subparsers
#: action, parameterised by the parser class the CLI registers with it.
type SubParsers = argparse._SubParsersAction[AdtArgumentParser]

# Where a git-backed `-my` gets its answer, spelled once (ADT #469).
#
# Four commands used to say "matched against git config user.email" in four
# separate strings, which was the documentation half of the same defect: the
# identity moved into `config/IDENTITY.yaml` and four rows would have had to be
# found and edited together to stay true. A constant cannot drift, and
# `tests/contracts/test_by_my_rows.py` reads the rendered rows, so a command
# that grows `-my` later inherits the sentence rather than inventing a fifth.
#
# `export_db -my` is deliberately NOT on it: that flag resolves the DATABASE
# identity (`db_schema`), a genuinely different fact, and one sentence covering
# both would be consistent and wrong, which is the failure `#228` recorded.
COMMIT_IDENTITY_HELP = "matched against IDENTITY.yaml email, or git config user.email"


def add_connection_key_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--key",
        "-key",
        help="encryption key or path to a key file for encrypted connection passwords",
    )
