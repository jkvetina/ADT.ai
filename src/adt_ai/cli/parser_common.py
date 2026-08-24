from __future__ import annotations

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


def add_connection_key_argument(parser) -> None:
    parser.add_argument(
        "--key",
        "-key",
        help="encryption key or path to a key file for encrypted connection passwords",
    )
