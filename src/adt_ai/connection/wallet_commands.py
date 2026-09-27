"""The wallet password commands a stored `set-wallet-pwd` value replaces (ADT #979).

`resolve()` layers an environment's `db:`, then `wallet:`, then the schema's
`db:`, so a `wallet_pwd_cmd` in any of them stands beside a stored `wallet_pwd`
and the next run refuses the file as a secret with two sources (1.5.1 audit
F11). `#651` cleared the command from the `wallet:` block only; the editor now
clears every block the loader merges, and says so, because the removed command
is not kept anywhere.
"""

from __future__ import annotations

from typing import Any

from adt_ai.shared.secret_command import SECRET_SOURCES

WALLET_COMMAND_KEYS = SECRET_SOURCES["wallet_pwd"][0]


def wallet_command_sites(env_node: Any) -> list[tuple[str, dict[str, Any]]]:
    """Every block of one environment that names a wallet password command.

    Labels name the block without the environment, which the caller prefixes:
    `wallet`, `db`, or `.APP db` for a schema.
    """
    sites: list[tuple[str, dict[str, Any]]] = []
    for label, node in (("wallet", env_node.get("wallet")), ("db", env_node.get("db"))):
        if isinstance(node, dict):
            sites.append((label, node))
    schemas = env_node.get("schemas")
    if isinstance(schemas, dict):
        for name, schema_node in schemas.items():
            db = schema_node.get("db") if isinstance(schema_node, dict) else None
            if isinstance(db, dict):
                sites.append((f".{name} db", db))
    return [
        (label, node)
        for label, node in sites
        if any(key in node for key in WALLET_COMMAND_KEYS)
    ]


def drop_wallet_commands(env_node: Any) -> None:
    """Remove every wallet password command from one environment."""
    for _label, node in wallet_command_sites(env_node):
        for key in WALLET_COMMAND_KEYS:
            node.pop(key, None)


def replaced_command_notes(environment: str, env_node: Any) -> tuple[str, ...]:
    """What `set-wallet-pwd` removes, and that going back means adding it again.

    Jan's condition on approving the removal (ADT #985): "it should be clear
    that if you want to go back, you have to provide the pwd again". The command
    itself is never printed, since a vault path names the credential.
    """
    sites = wallet_command_sites(env_node)
    if not sites:
        return ()
    keys = sorted({key for _, node in sites for key in WALLET_COMMAND_KEYS if key in node})
    places = ", ".join(
        f"{environment}{label}" if label.startswith(".") else f"{environment} {label}"
        for label, _ in sites
    )
    return (
        f"The stored wallet password replaces {' and '.join(keys)} in {places}.",
        "The command is not kept: to fetch the password by command again, add it again.",
    )
