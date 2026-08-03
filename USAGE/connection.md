# Edit Connection File (adtai connection)

Manage a project's connection file from the command line instead of hand-editing YAML. The command resolves the same connection file ADT.ai uses at runtime (via `-root` / `-config-dir`, like every other command) and applies one structural change to it. `-create` can create the resolved file when it is missing; the other actions edit an existing file.

```bash
adtai connection -add-schema -env DEV -schema REPORTS
```

Exactly one action is required per run: `-create`, `-add-env`, `-add-schema`, `-set-pwd`, or `-set-wallet-pwd`.

The edit is rewritten with a round-trip YAML parser, so comments and key order in the connection file are preserved. Only the targeted block is added or changed.

## Preview by default

Without `-go`, the command previews: it prints the resolved connection file path, a one-line summary of the action, and the YAML block that would be inserted. Nothing is written.

```bash
adtai connection -add-schema -env DEV -schema REPORTS
```

Re-run with `-go` to apply the change:

```bash
adtai connection -add-schema -env DEV -schema REPORTS -go
```

## Passwords

Passwords are never passed on the command line. When a password is needed it is collected interactively (hidden input) at apply time only — preview never prompts and never renders a secret.

- `-create -go` prompts once for the schema password and, when `-wallet` is provided, once for the wallet password; leave either blank to skip writing that password.
- `-add-schema -go` prompts once; leave it blank to add the schema without a password.
- `-set-pwd` and `-set-wallet-pwd` prompt twice and require a match.

Passwords are written as cleartext by default. Cleartext writes remove the matching encryption marker (`pwd!` / `wallet_pwd!`) so runtime loading will not try to decrypt plaintext.

Use `-encrypt` to write OLD ADT-compatible encrypted values. The key comes from `-key VALUE`, `-key /path/to/keyfile`, `ADT_KEY=VALUE`, or `ADT_KEY=/path/to/keyfile`. Encrypted schema passwords are written as `pwd:` with `pwd!: Y`; encrypted wallet passwords are written as `wallet_pwd:` with `wallet_pwd!: Y`. Runtime connection loading decrypts those marked values before opening database or wallet connections.

## Create a connection file or entry

`-create` is the OLD ADT-style bootstrap path. It creates the resolved connection YAML file when no candidate exists yet, or fills missing fields in the existing resolved file. Existing values are preserved; the command only adds missing environment/schema fields.

For the `export:` filters (`-prefix`, `-ignore`) a **blank** entry counts as missing, not as a value: `SAMPLE.yaml` seeds the block as `ignore: ''` / `prefix: ''`, so treating a pre-seeded placeholder as "already set" would make both flags a permanent no-op on every project bootstrapped from the template. A filter that actually holds a value is still never overwritten — change one by editing the YAML.

```bash
adtai connection -create -env DEV -schema APP -user APP \
  -host db.example.com -service DEVDB -default -go
```

For wallet/APEX projects:

```bash
adtai connection -create -env DEV -schema APP -user APP \
  -host db.example.com -service DEVDB \
  -wallet Wallet_DEV -workspace DEV_WS -app 100 \
  -prefix APP_% -ignore TMP_% \
  -default -encrypt -key /secure/adt.key -go
```

When `-default` is passed, ADT.ai writes `defaults.schema_apex` if an APEX workspace is configured, otherwise `defaults.schema_db`.

## Add a schema

The environment must already exist; the schema must not. The new schema is added as `db: {user: ...}` (the user defaults to the schema name, or use `-user`). Host, port, and service are inherited from the environment-level `db` block at resolve time, so they are not restated per schema.

```bash
adtai connection -add-schema -env DEV -schema REPORTS -user REPORT_OWNER -go
```

## Set a schema password

The environment and schema must both exist. Sets `db.pwd` for that schema; the prompt is interactive.

```bash
adtai connection -set-pwd -env DEV -schema REPORTS -go
```

To write the schema password encrypted:

```bash
adtai connection -set-pwd -env DEV -schema REPORTS -encrypt -key /secure/adt.key -go
```

## Set a wallet password

The environment must exist. Sets `wallet.wallet_pwd` for that environment; the prompt is interactive.

```bash
adtai connection -set-wallet-pwd -env DEV -encrypt -key /secure/adt.key -go
```

## Add an environment

The environment must not already exist. From scratch it builds a `db` block (`-host`, `-port` defaulting to 1521, `-service`), plus empty `defaults` and `schemas` blocks ready for `-add-schema`.

```bash
adtai connection -add-env -env QA -host db.example.com -service QADB -go
```

With `-like`, the new environment clones the source environment's `db` and `wallet` blocks (with any passwords stripped), then applies any `-host` / `-port` / `-service` overrides. The cloned environment starts with no schemas — add them with `-add-schema`.

```bash
adtai connection -add-env -env QA -like DEV -go
```

## Arguments

| Argument | Required | Default | Notes |
| -------- | -------- | ------- | ----- |
| `-create` | One action required | off | Create or update a connection entry. Requires `-env` and `-schema`; can create the resolved file if missing. |
| `-add-env` | One action required | off | Add a new environment. Requires `-env`; use `-like` to clone another environment. |
| `-add-schema` | One action required | off | Add a new schema to an environment. Requires `-env` and `-schema`. |
| `-set-pwd` | One action required | off | Set a schema password. Requires `-env` and `-schema`; prompts interactively with `-go`. |
| `-set-wallet-pwd` | One action required | off | Set an environment wallet password. Requires `-env`; prompts interactively with `-go`. |
| `-env`, `--env` | Per action | none | Target environment name. |
| `-schema`, `--schema` | Per action | none | Target schema name (required for `-create`, `-add-schema`, and `-set-pwd`). |
| `-user`, `--user` | No | schema name | Database user for `-create` and `-add-schema`. |
| `-like`, `--like` | No | none | With `-add-env`, clone this environment's `db` / `wallet` blocks (secrets stripped). |
| `-host`, `--host` | No | none | With `-create` or `-add-env`, set the `db` hostname. |
| `-port`, `--port` | No | `1521` | With `-create` or `-add-env`, set the `db` port. |
| `-service`, `--service` | No | none | With `-create` or `-add-env`, set the `db` service. |
| `-sid`, `--sid` | No | none | With `-create`, set the `db` SID when no service is used. |
| `-wallet`, `--wallet` | No | none | With `-create`, set the environment wallet name/path. |
| `-workspace`, `--workspace` | No | none | With `-create`, set schema APEX workspace. |
| `-app`, `--app` | No | none | With `-create`, set schema APEX app scope. |
| `-prefix`, `--prefix` | No | none | With `-create`, set schema export prefix filter. |
| `-ignore`, `--ignore` | No | none | With `-create`, set schema export ignore filter — the SQL LIKE patterns `export_db` / `export_data` skip (`TMP_%,BIN$%`). Fills a blank or missing entry; never overwrites one that already holds a value. |
| `-default`, `--default` | No | off | With `-create`, mark the schema as the default database or APEX schema. |
| `-encrypt`, `--encrypt` | No | off | With password-writing actions, encrypt the stored value using `-key` or `ADT_KEY`. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file. |
| `-root`, `--root` | No | `.` | Project root folder used to resolve the connection file. |
| `-config-dir`, `--config-dir` | No | none | Folder containing config YAML (repeatable). |
| `-go`, `--go` | No | off | Apply the change. Without it, the command previews without writing. |
| `-debug`, `--debug` | No | off | Show input parameters and the resolved startup context. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Named SQLcl connections

Every SQLcl script ADT generates (including REST export in `export_apex -rest`) connects through a **named SQLcl connection** instead of embedding the username, password, and wallet path in the script. The password lives in SQLcl's own connection store (OS secure storage via `connect -save … -savepwd`), so per-call scripts carry `connect -name ADT_…` and nothing else.

- **Naming.** `ADT_` + the connection-file basename — `ADT_CORE23` for `connections/CORE23.yaml`. When the file defines more than one environment the name gains `_<ENV>`, and when that environment defines more than one schema it gains `_<SCHEMA>` (e.g. `ADT_TEAMDB_DEV_DA` for `connections/TEAMDB.yaml` with several environments and schemas), so every environment/schema pair maps to its own name. A project using the generic `connections.yaml` filename is named after its project folder instead.
- **Transparency.** The first SQLcl call registers the connection and records the assigned name on the schema's `db:` block in the connection YAML (`sqlcl:`), together with a credential fingerprint (`sqlcl_sync:`). Edit `sqlcl:` by hand to pin a different name — a recorded name always wins over generation.
- **Credential changes.** On every SQLcl call ADT recomputes the fingerprint over the username, password, host/service, wallet path, and wallet password. Any change (e.g. after `-set-pwd`) re-registers the named connection automatically on the next call.
- **Machine moves.** The YAML travels with the project; SQLcl's store does not. When the store has no such name (new machine, cleared `~/.dbtools`), the call fails fast and ADT re-registers and retries automatically.
- **Wallets.** Registration passes `-cloudconfig` with an absolute wallet path, so OCI wallet projects need nothing extra.
- **Opt-out.** Set `sqlcl_named_connections: false` in project `config.yaml` to restore the old inline `connect user/"pwd"@service` scripts.

---

← [USAGE.md](../USAGE.md) index
