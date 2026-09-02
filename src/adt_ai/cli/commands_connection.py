from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from adt_ai.cli.constants import (
    ConfigLoader,
    ConnectionLoader,
    print_module_banner,
)
from adt_ai.cli.context import (
    StartupContext,
    _config_search_paths,
    _connection_file_candidates,
    _connection_search_paths,
    _load_startup_context,
    _print_startup_debug,
    _remember_completion_config,
    _repo_root,
    _wallet_roots,
)
from adt_ai.connection.runner import (
    ConnectionEditError,
    ConnectionEditor,
    ConnectionEditRequest,
)
from adt_ai.shared import crypto
from adt_ai.shared.connections import ConnectionNotFoundError
from adt_ai.shared.secret import Secret

_CONNECTION_ACTIONS = (
    ("create", "create"),
    ("add-env", "add_env"),
    ("add-schema", "add_schema"),
    ("set-pwd", "set_pwd"),
    ("set-wallet-pwd", "set_wallet_pwd"),
    ("rekey", "rekey"),
)

_CONNECTION_ACTION_NAMES = [f"-{name}" for name, _ in _CONNECTION_ACTIONS]
_CONNECTION_ACTION_LIST = ", ".join(
    [*_CONNECTION_ACTION_NAMES[:-1], f"or {_CONNECTION_ACTION_NAMES[-1]}"]
)

# `-encrypt` says what to do with a password the run supplies, so it means
# nothing for an action that supplies none. `-rekey` is the sharper case: it
# encrypts by definition, and accepting the flag would imply there is a variant
# of it that does not.
_ACTIONS_WITHOUT_ENCRYPT = {"add-env", "rekey"}

# A rekey rewrites the whole file, so it takes no environment or schema selector.
_WHOLE_FILE_ACTIONS = {"rekey"}


def _selected_connection_action(args: argparse.Namespace) -> str | None:
    selected = [name for name, dest in _CONNECTION_ACTIONS if getattr(args, dest)]
    return selected[0] if len(selected) == 1 else None


def _missing_connection_selector(action: str, args: argparse.Namespace) -> str | None:
    if action in _WHOLE_FILE_ACTIONS:
        return None
    if action == "add-env":
        if not args.env:
            return "-add-env requires -env"
        return None
    if action == "create":
        if not args.env or not args.schema:
            return "-create requires -env and -schema"
        return None
    if action == "set-wallet-pwd":
        if not args.env:
            return "-set-wallet-pwd requires -env"
        return None
    if not args.env or not args.schema:
        return f"-{action} requires -env and -schema"
    return None


def _connection_request(
    action: str,
    args: argparse.Namespace,
    path: Path,
    *,
    password: str | None,
    wallet_password: str | None = None,
    apply: bool,
) -> ConnectionEditRequest:
    return ConnectionEditRequest(
        path        = path,
        action      = action,
        environment = args.env,
        schema      = args.schema,
        username    = args.user,
        password    = Secret(password),
        wallet      = args.wallet,
        wallet_password = Secret(wallet_password),
        hostname    = args.host,
        port        = args.port,
        service     = args.service,
        sid         = args.sid,
        workspace   = args.workspace,
        app         = args.app,
        prefix      = args.prefix,
        ignore      = args.ignore,
        like        = args.like,
        default     = args.default,
        apply       = apply,
        encrypt     = args.encrypt,
        key         = Secret(args.key),
        old_key     = Secret(getattr(args, "old_key", None)),
        new_key     = Secret(getattr(args, "new_key", None)),
    )


def _prompt_password(prompt: str) -> str | None:
    """One hidden password, or None when stdin has nothing left to give.

    With no usable terminal `getpass` falls back to reading a line off stdin,
    and that read raises `EOFError` the moment the stream is closed or spent,
    which is the ordinary state of a CI job, a deployment script and an agent
    session alike. Nothing caught it, so every password action died on
    `UNEXPECTED ERROR: EOFError` and wrote no file at all (`#675`). The callers
    below decide what an exhausted stdin means for their own action; here it is
    only reported, never guessed at.

    `KeyboardInterrupt` deliberately passes through. Ctrl-C is an abort rather
    than an empty answer, and the runtime already ends on 130 and a one-line
    "Interrupted by user."
    """
    try:
        return getpass.getpass(prompt)
    except EOFError:
        return None


def _no_password_available(action: str) -> str:
    return (
        f"-{action} needs a password, and nothing arrived at the prompt; "
        "run it from a terminal"
    )


def _collect_connection_password(
    action: str, args: argparse.Namespace
) -> tuple[str | None, str | None]:
    label = args.env if action == "set-wallet-pwd" else f"{args.env}.{args.schema}"
    if action in {"set-pwd", "set-wallet-pwd"}:
        # These two write a password by definition, so a blank is already a
        # refusal and an exhausted stdin cannot be read as one either way. It
        # takes its own message: "did not match" would name the wrong problem,
        # and a confirmation that never ran matched nothing.
        first = _prompt_password(f"New password for {label}: ")
        if first is None:
            return None, _no_password_available(action)
        if not first:
            return None, f"a password is required for -{action}"
        confirmation = _prompt_password("Confirm password: ")
        if confirmation is None:
            return None, _no_password_available(action)
        if first != confirmation:
            return None, "passwords did not match"
        return first, None
    # add-schema: a password is optional; a blank entry skips writing pwd, and
    # so does a stdin with nothing on it.
    return _prompt_password(f"Password for {label} (leave blank to skip): ") or None, None


def _collect_create_passwords(args: argparse.Namespace) -> tuple[str | None, str | None]:
    schema_password = _prompt_password(
        f"Password for {args.env}.{args.schema} (leave blank to skip): "
    )
    wallet_password = None
    # A blank schema password still leaves a wallet worth asking about. An
    # exhausted stdin does not, so the second prompt is skipped rather than
    # printed at a reader with no way to answer it.
    if args.wallet and schema_password is not None:
        wallet_password = _prompt_password(
            f"Wallet password for {args.env} (leave blank to skip): "
        )
    return (schema_password or None), (wallet_password or None)


def _connection_edit_path(
    args: argparse.Namespace, *, allow_missing: bool
) -> tuple[StartupContext | None, Path]:
    if not allow_missing:
        startup = _load_startup_context(args)
        return startup, startup.connection_files[0]

    repo_root = _repo_root()
    root = Path(args.root).expanduser().resolve()
    config_search_paths = _config_search_paths(args.config_dir, root, repo_root)
    config_result = ConfigLoader(config_search_paths).load()
    _remember_completion_config(args, config_result.data)
    connection_search_paths = _connection_search_paths(
        config_result.data, args.config_dir, root, repo_root
    )
    candidates = _connection_file_candidates(
        config_result.data, args.config_dir, root, repo_root
    )
    try:
        connections = ConnectionLoader(
            connection_search_paths,
            wallet_roots = _wallet_roots(
                config_result.data, root, repo_root, connection_search_paths
            ),
            key          = args.key,
        ).load(candidates=candidates)
        return None, connections.files[0]
    except ConnectionNotFoundError:
        return None, candidates[0]


def _run_connection(args: argparse.Namespace) -> int:
    print_module_banner("CONNECTION")

    action = _selected_connection_action(args)
    if action is None:
        print(
            f"connection: provide exactly one of {_CONNECTION_ACTION_LIST}",
            file=sys.stderr,
        )
        return 2
    missing = _missing_connection_selector(action, args)
    if missing:
        print(f"connection: {missing}", file=sys.stderr)
        return 2
    if args.encrypt and action in _ACTIONS_WITHOUT_ENCRYPT:
        if action == "rekey":
            print(
                "connection: -encrypt is not used with -rekey, which re-encrypts by "
                "definition; give it -old-key and -new-key",
                file=sys.stderr,
            )
        else:
            print("connection: -encrypt is only valid for password actions", file=sys.stderr)
        return 2

    startup, path = _connection_edit_path(args, allow_missing=action == "create")
    if args.debug and startup is not None:
        _print_startup_debug(startup)

    editor = ConnectionEditor()
    try:
        plan = editor.run(
            _connection_request(action, args, path, password=None, apply=False)
        )
    except ConnectionEditError as error:
        print(f"connection: {error}", file=sys.stderr)
        return 2

    print()
    print(f"  Connection file   {path}")
    print(f"  Action            {plan.summary}")

    if not args.go:
        print("  Mode              preview (re-run with -go to apply)")
        if plan.preview:
            print()
            print(plan.preview)
        return 0

    if args.encrypt:
        try:
            crypto.resolve_key(args.key)
        except crypto.CryptoError as error:
            print(f"connection: {error}", file=sys.stderr)
            return 2

    password = None
    wallet_password = None
    if action == "create":
        password, wallet_password = _collect_create_passwords(args)
    elif action in {"add-schema", "set-pwd", "set-wallet-pwd"}:
        password, error_message = _collect_connection_password(action, args)
        if error_message:
            print(f"connection: {error_message}", file=sys.stderr)
            return 2

    try:
        result = editor.run(
            _connection_request(
                action, args, path,
                password=password, wallet_password=wallet_password, apply=True,
            )
        )
    except ConnectionEditError as error:
        # `-rekey` does its own validation on the apply pass, where the keys are
        # actually used, so this is the first point a wrong -old-key can surface.
        print(f"connection: {error}", file=sys.stderr)
        return 2
    print()
    print(f"  {result.summary}")
    if action == "rekey" and result.preview:
        # Which secrets were rewritten is the result of a rekey, not decoration:
        # it is the record that no corner of the file was missed.
        print()
        print(result.preview)
    print()
    print(f"WROTE: {path}")
    return 0

__all__ = [name for name in globals() if not name.startswith("__")]
