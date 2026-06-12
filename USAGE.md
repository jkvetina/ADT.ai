# ADT.ai Usage

This is the public usage index for ADT.ai `0.1.0`.

## Commands

| Command | Reference | Purpose |
| ------- | --------- | ------- |
| `export_db` | [USAGE/export_db.md](USAGE/export_db.md) | Export database objects to files. |
| `doctor` | [USAGE/doctor.md](USAGE/doctor.md) | Check local setup and bootstrap project config. |

## Install

```bash
python3 -m pip install -e .
```

## Help

Show the command overview:

```bash
adtai --help
```

Show all arguments for a command:

```bash
adtai export_db --help
adtai doctor --help
```

## Config, Connections, and Wallets

ADT.ai loads defaults from this checkout's `config/`, then overlays project config from `-config-dir`, `<root>/config/`, and `<root>/`.

Connection files are resolved by first match. By default ADT.ai checks `<root>/connections.yaml`, `<root>/connections/<FOLDER>.yaml`, then this checkout's ignored `connections/<FOLDER>.yaml`. Use `connections.path`, `connections.file`, and `connections.wallet_path` in config to keep sensitive files outside project repos.
