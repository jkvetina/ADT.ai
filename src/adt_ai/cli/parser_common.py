from __future__ import annotations


def add_connection_key_argument(parser) -> None:
    parser.add_argument(
        "--key",
        "-key",
        help="encryption key or path to a key file for encrypted connection passwords",
    )
