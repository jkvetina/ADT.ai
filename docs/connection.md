# Edit Connection File (adtai connection)

Manage a project's connection file from the command line instead of hand-editing YAML. The command resolves the same connection file ADT.ai uses at runtime (via `-root` / `-config-dir`, like every other command) and applies one structural change to it. `-create` can create the resolved file when it is missing; the other actions edit an existing file.

```bash
adtai connection -add-schema -env DEV -schema REPORTS
```

Exactly one action is required per run: `-create`, `-add-env`, `-add-schema`, `-set-pwd`, `-set-wallet-pwd`, or `-rekey`.

The edit is rewritten with a round-trip YAML parser, so comments and key order in the connection file are preserved. Only the targeted block is added or changed. `-rekey` is the one action that is not targeted at a single block: it rewrites every encrypted secret in the file at once.

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

Passwords are never passed on the command line. When a password is needed it is collected interactively (hidden input) at apply time only, preview never prompts and never renders a secret.

- `-create -go` prompts once for the schema password and, when `-wallet` is provided, once for the wallet password; leave either blank to skip writing that password.
- `-add-schema -go` prompts once; leave it blank to add the schema without a password.
- `-set-pwd` and `-set-wallet-pwd` prompt twice and require a match.

Passwords are written as cleartext by default. Cleartext writes remove the matching encryption marker (`pwd!` / `wallet_pwd!`) so runtime loading will not try to decrypt plaintext.

Use `-encrypt` to write an encrypted value. The key comes from `-key VALUE`, `-key /path/to/keyfile`, `ADT_KEY=VALUE`, or `ADT_KEY=/path/to/keyfile`. Encrypted schema passwords are written as `pwd:` with `pwd!: Y`; encrypted wallet passwords are written as `wallet_pwd:` with `wallet_pwd!: Y`. Runtime connection loading decrypts those marked values before opening database or wallet connections.

### The stored format

An encrypted value looks like this, and the three parts are a format version, a salt, and the ciphertext:

```yaml
pwd: adt2$Zx8kQ1rTfN4bVpLsWm2dEg==$gAAAAABo...
pwd!: Y
pwd_key: "4f2a91c7de08"
```

The quotes around `pwd_key` are load-bearing, so keep them if you ever edit the line by hand. A 12 character hex digest reads as a number to a YAML parser about once in every 440: `123456789012` is an integer, and `1e2345678901` is a floating point number large enough to overflow. Unquoted, such a value can be rewritten as `inf` the next time anything edits the file, which turns a correct key into a permanent wrong-key error. A recorded value that is not a readable digest is ignored rather than trusted, so a file already damaged that way still opens, it just loses the better error message.

Each secret carries its own 16 byte random salt, so encrypting the same password twice gives two different values, and no dictionary table built against one project helps against another. The salt lives inside the value rather than beside it, which keeps the round trip stateless: the connection file is still the only thing that has to travel, and a colleague or a CI runner holding nothing but `ADT_KEY` can read it. `adt2` derives at 600000 PBKDF2-HMAC-SHA256 iterations, current OWASP guidance, which costs roughly 160 ms per secret on a modern laptop.

**Older files keep working.** A value with no `adt2$` prefix is read as the pre-`#399` format (no salt, 100000 iterations) and nothing rewrites it underneath you. Writing that secret again with `-encrypt`, or running `connection -rekey`, moves it to the current format. The version prefix is also what makes the next change painless: a value written by a newer ADT.ai than the one you are running says so by name instead of failing as a corrupt password.

**`pwd_key:` records which key the value is under.** It is a 12 character digest, not the key and not the password, and it exists because Fernet reports a wrong key and a corrupted value with the same error, so the commonest mistake of all used to read as a damaged connection file:

```text
Wrong encryption key for DEV.APP pwd: the stored value carries key fingerprint
4f2a91c7de08, the key in use fingerprints as 9b31e77c05aa. Pass -key or set
ADT_KEY to the key this value was encrypted with.
```

The digest is derived from that value's own salted key material rather than from `ADT_KEY` by itself. That is what makes it safe to commit: testing a guessed key against the fingerprint costs the same 600000 iterations as testing it against the ciphertext sitting next to it, so the recorded value hands an attacker nothing the file did not already give them. It also means two secrets encrypted with one key have different fingerprints, which is the property that lets a rekey be verified secret by secret rather than trusted in bulk.

## The secret can come from your own vault instead

A connection file does not have to hold the password at all, encrypted or otherwise. Point `pwd_cmd:` at a command, and ADT.ai runs it and takes its output as the password:

```yaml
DEV:
  defaults:
    schema_db: APP
  schemas:
    APP:
      db:
        user: APP
        pwd_cmd: op read op://Engineering/DEV_APP/password
```

The wallet password has the same key on the wallet block, and the encryption key has an environment variable one level up:

```yaml
DEV:
  wallet:
    wallet_path: adb_wallet
    wallet_pwd_cmd: op read op://Engineering/DEV_WALLET/password
```

```bash
export ADT_KEY_CMD='op read op://Engineering/ADT/adt-key'
```

Whatever your secret manager is, if it can print a value it works:

```yaml
pwd_cmd: op read op://Engineering/DEV_APP/password
pwd_cmd: vault kv get -field=password secret/oracle/dev/app
pwd_cmd: pass show oracle/dev/app
pwd_cmd: az keyvault secret show --vault-name adt-dev --name app-password --query value -o tsv
```

### What this buys, and what it does not

What you get is custody. Your vault holds the secret, rotates it, revokes it, and records who asked for it, and revoking it there revokes it everywhere. A password copied into a file, even an encrypted one, is a copy you now have to track.

What you do not get is secrecy from the tool. The value still reaches the Oracle driver as plaintext, and anyone allowed to run `adtai` can run `op read` themselves, so this hides nothing from a person or an agent already on that machine. Anybody telling you otherwise is selling something. The interesting property is the first one, not the second.

### The rules, all four of them

**No shell.** The command runs as an argument list. A string is split the way a shell would split it, quotes respected, but nothing is ever handed to `sh`, so a `&` or a `;` inside a vault path is passed through as text rather than run. Quote a path that has spaces in it, or write the command as a YAML list when quoting gets awkward:

```yaml
pwd_cmd: ["op", "read", "op://My Vault/DEV APP/password"]
```

**One source per secret.** A block carrying both `pwd_cmd:` and `pwd:` is refused, by name, instead of one of them quietly winning:

```text
DEV.APP pwd: pwd_cmd is configured beside pwd, so the secret has two sources. A
block reads its secret from exactly one place: remove pwd to fetch it with the
command, or remove pwd_cmd to keep the stored value.
```

That covers `pwd!` and `pwd_key` too, and it counts what the schema actually resolves, so an environment-level `pwd:` inherited from above collides with a schema-level `pwd_cmd:` just as a neighbouring one does. `ADT_KEY` beside `ADT_KEY_CMD` is refused for the same reason. `-key` on the command line still wins over both, because that is an override for one run rather than a second piece of configuration.

**The command must be able to finish on its own.** Its input is closed, so a command that stops to ask something fails immediately rather than hanging behind a pipe nobody can see, and it gets 60 seconds before ADT.ai gives up on it. Biometric and GUI unlock prompts are fine (`op` and `pass` both work that way on a Mac); a command that wants an answer typed into the terminal is not.

**A failure names the command, never its output.** stdout is where the secret arrives, so it is never quoted back at you. stderr is, because that is where a vault CLI writes the actual cause:

```text
DEV.APP pwd: command failed with exit status 1: op read op://Engineering/DEV_APP/password
  [ERROR] 2026/08/19 12:00:00 you are not currently signed in
```

One trailing newline is dropped from the output, since every CLI above adds one. Nothing else is trimmed, so a password ending in a space survives.

## Or no password in the process at all

The options above move the secret around: encrypted in the file, or fetched from your own vault. `sqlcl_only` removes it from ADT.ai entirely. Put this in the project config:

```yaml
sqlcl_only : True
```

Every database call then goes through SQLcl instead of the Python driver, and SQLcl connects by the name it holds in its own secure store, so the credential never becomes a value ADT.ai has. A connection file under this switch needs no password at all:

```yaml
DEV:
  db:
    hostname: oracle.example.test
    service: FREEPDB1
  defaults:
    schema_db: APP
  schemas:
    APP:
      db:
        user: APP
        sqlcl: ADT_MYPROJECT
```

The name has to be in SQLcl's store first, and ADT.ai puts it there on any ordinary run that has the password, so the usual path is to connect once the normal way and then turn the switch on. A run that finds no stored connection stops with `SQLcl did not connect` and says so, instead of carrying on and reporting something strange three screens later.

What it costs is time. The session opens once per run, measured at about 3 seconds, and each query after that costs tens of milliseconds where the driver costs single digits. On a large export that adds up, which is why the default is `False` and why this is a tradeoff rather than advice. What it does not cost is behaviour: the same commands print the same output and write the same files, and there is no command the switch cannot serve.

## Or no password passed at all

`sqlcl_only` keeps the password out of ADT.ai's process by moving it to SQLcl. External authentication goes one step further: nothing passes a password anywhere, because the Oracle client library reads it out of a Secure External Password Store itself.

```yaml
DEV:
  db:
    auth: external
    tns: ADT_PROD_APP
    wallet_path: seps_prod
    client_lib_dir: /opt/oracle/instantclient_23_3
  defaults:
    schema_db: APP
  schemas:
    APP:
      db:
        user: APP
```

`auth: external` skips password resolution completely. Not "resolves to nothing", skips: no `pwd`, no `pwd!`, no `pwd_cmd`, no `ADT_KEY`, and the decrypt path is never reached, so a file in this mode cannot fail on a missing key because nothing asks for one. `tns` is the alias your wallet files the credential under, and `wallet_path` is the folder holding `cwallet.sso` beside its `tnsnames.ora` and `sqlnet.ora`.

**This wallet is not the ADB wallet.** ADT.ai already handles the mTLS wallet an Autonomous Database hands you, through `wallet_pwd`. That one proves who the client is and still needs a password to log in. A SEPS wallet holds the login itself and needs no `wallet_pwd`. Different file, different job, and a project can use one, the other, or both.

Building one, once, with the Oracle client's own tool:

```bash
mkstore -wrl /secure/seps_prod -create
mkstore -wrl /secure/seps_prod -createCredential ADT_PROD_APP APP
```

Two requirements come with the mode, and both are the driver's rather than ours. It is **thick mode only**, so ADT.ai turns `thick` on for you when it sees `auth: external` rather than making you set two things that only work together. And it needs the **Oracle client libraries** on the machine, which `SETUP.md` covers; point `client_lib_dir` at them.

Being exact about what it is: SEPS is still password authentication, and `SYS_CONTEXT('USERENV','AUTHENTICATION_METHOD')` reports `PASSWORD`, not `EXTERNAL`. What changes is who holds the password. It lives in the wallet, the client library reads it, and it never exists as a value inside ADT.ai, which is the one claim on this whole page that survives an agent reading the tool's memory.

## A loaded credential is masked

Once a connection file is loaded, the schema password and the wallet password are both held in a `Secret` wrapper whose `repr` and `str` render as `***`, so neither can reach stdout, a log line, a generated script, or an error message by accident. That covers the cases nothing in the code deliberately prints: an interpolated connection inside an exception message, a debug dump, a test runner showing local variables on failure.

Exactly three places unwrap the value, and each hands it straight to something outside Python that cannot accept a mask: the python-oracledb connect call, the `connect user/"pwd"@dsn` line of a generated SQLcl script (which named connections avoid entirely, see below), and the credential fingerprint, which emits nothing but a 12 character digest. A contract test pins that list, so a fourth unwrap fails the test suite rather than passing review.

Two limits, stated rather than implied. This does not hide a credential from an agent that is allowed to run `adtai` at all, because such an agent can open its own database session without ever reading a password; the guarantee is the narrower and more useful one, that ADT.ai itself never puts a plaintext credential where something else reads it back. And it is not encryption at rest: what protects the stored value is `-encrypt` above, plus keeping the key outside the project folder.

## Create a connection file or entry

`-create` is the OLD ADT-style bootstrap path. It creates the resolved connection YAML file when no candidate exists yet, or fills missing fields in the existing resolved file. Existing values are preserved; the command only adds missing environment/schema fields.

For the `export:` filters (`-prefix`, `-ignore`) a **blank** entry counts as missing, not as a value: a connection file conventionally seeds the block as `ignore: ''` / `prefix: ''`, so treating a pre-seeded placeholder as "already set" would make both flags a permanent no-op on exactly the files that need them. A filter that actually holds a value is still never overwritten, change one by editing the YAML.

There is no shipped connection template. `connections/*` is gitignored, a connection file holds credentials, and `doctor -init` writes only the `connections/.gitkeep` and `connections/wallets/.gitkeep` placeholders, never a YAML. A `connections/SAMPLE.yaml` in an ADT.ai checkout is a local, hand-maintained reference copy, not something you receive: bootstrap your first connection file with `connection -create`, which writes the whole structure for you.

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

## Change the encryption key

`-rekey` re-encrypts every secret in the resolved file under a new key, in one pass across every environment and every schema. It needs both keys, and either may be a value or a path to a key file:

```bash
adtai connection -rekey -old-key /secure/adt.key -new-key /secure/adt-2027.key -go
```

It takes no `-env` and no `-schema`: a rekey that covered part of a file would leave one no single key can open, which is the failure the whole action exists to avoid.

**Re-encrypting is not rotating the database password.** They are two separate rotations, and after a suspected leak only the second one matters. Changing the key protects a connection file that someone might obtain in future; it does nothing about a password an attacker already has, which is changed in the database and then written here with `-set-pwd`. Do both, in that order, and do not let the first stand in for the second.

Without `-go` the command previews. The preview names each secret it would rewrite and renders none of them, neither the plaintext nor the ciphertext:

```text
  Connection file   /path/to/connections/CORE23.yaml
  Action            re-encrypt 5 secrets under the new key
  Mode              preview (re-run with -go to apply)

  DEV.db.pwd
  DEV.wallet.wallet_pwd
  DEV.APP.pwd
  DEV.REPORTS.pwd
  UAT.APP.pwd
```

Cleartext passwords are left exactly as they are. `-rekey` rewrites values carrying `pwd!` / `wallet_pwd!`, so a file with no encrypted secret is an error rather than a silent success, and a cleartext password becomes encrypted through `-set-pwd -encrypt`, never through a rekey.

**Nothing is written unless everything can be.** Every secret is decrypted before any is rewritten, so a wrong `-old-key` leaves the file untouched instead of half converted. The recorded `pwd_key:` fingerprints are what let the command tell you which of two things went wrong:

- Every recorded fingerprint disagrees with `-old-key`, so the key is simply wrong: `wrong -old-key: it matches none of the 5 recorded key fingerprints in CORE23.yaml. Nothing was written`.
- Some agree and some do not, so the file is already under more than one key. The message names the odd ones out and tells you to settle them with `-set-pwd -encrypt` before rekeying. That is the case a fingerprint-free file could never report, and it is why `#399` records one fingerprint per secret rather than one per key.

A rekey is also the wholesale way off the pre-`#399` stored format: values written by an older ADT.ai come out as `adt2$...` with a fresh salt and a recorded fingerprint each, whether or not the key actually changed. Passing the same value for both keys is a legitimate way to migrate a file in place.

## Add an environment

The environment must not already exist. From scratch it builds a `db` block (`-host`, `-port` defaulting to 1521, `-service`), plus empty `defaults` and `schemas` blocks ready for `-add-schema`.

```bash
adtai connection -add-env -env QA -host db.example.com -service QADB -go
```

With `-like`, the new environment clones the source environment's `db` and `wallet` blocks (with any passwords stripped), then applies any `-host` / `-port` / `-service` overrides. The cloned environment starts with no schemas, add them with `-add-schema`.

```bash
adtai connection -add-env -env QA -like DEV -go
```

## Arguments

Exactly one action flag, `-create`, `-add-env`, `-add-schema`, `-set-pwd`, `-set-wallet-pwd`, or `-rekey`, is required; each names the further arguments it needs.

| Argument | Repeatable | Default | Notes |
| -------- | ---------- | ------- | ----- |
| `-create` | No | off | Action. Create or update a connection entry. Requires `-env` and `-schema`; can create the resolved file if missing. |
| `-add-env` | No | off | Action. Add a new environment. Requires `-env`; use `-like` to clone another environment. |
| `-add-schema` | No | off | Action. Add a new schema to an environment. Requires `-env` and `-schema`. |
| `-set-pwd` | No | off | Action. Set a schema password. Requires `-env` and `-schema`; prompts interactively with `-go`. |
| `-set-wallet-pwd` | No | off | Action. Set an environment wallet password. Requires `-env`; prompts interactively with `-go`. |
| `-rekey` | No | off | Action. Re-encrypt every encrypted secret in the file under a new key. Requires `-old-key` and `-new-key`; takes no `-env` or `-schema`, and rejects `-encrypt`. |
| `-old-key`, `--old-key` | No | none | With `-rekey`, the key the file's secrets are encrypted with today. A value or a path to a key file. |
| `-new-key`, `--new-key` | No | none | With `-rekey`, the key to re-encrypt them with. A value or a path to a key file. |
| `-env`, `--env` | No | none | Target environment name. Required by every action. |
| `-schema`, `--schema` | No | none | Target schema name (required for `-create`, `-add-schema`, and `-set-pwd`). |
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
| `-ignore`, `--ignore` | No | none | With `-create`, set schema export ignore filter, the SQL LIKE patterns `export_db` / `export_data` skip (`TMP_%,BIN$%`). Fills a blank or missing entry; never overwrites one that already holds a value. |
| `-default`, `--default` | No | off | With `-create`, mark the schema as the default database or APEX schema. |
| `-encrypt`, `--encrypt` | No | off | With password-writing actions, encrypt the stored value using `-key` or `ADT_KEY`. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file. |
| `-root`, `--root` | No | `.` | Project root folder used to resolve the connection file. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing config YAML. |
| `-go`, `--go` | No | off | Apply the change. Without it, the command previews without writing. |
| `-debug`, `--debug` | No | off | Show input parameters and the resolved startup context. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Named SQLcl connections

Every SQLcl script ADT generates (including REST export in `export_apex -rest`) connects through a **named SQLcl connection** instead of embedding the username, password, and wallet path in the script. The password lives in SQLcl's own connection store (OS secure storage via `connect -save … -savepwd`), so per-call scripts carry `connect -name ADT_…` and nothing else.

- **Naming.** `ADT_` + the connection-file basename, `ADT_CORE23` for `connections/CORE23.yaml`. When the file defines more than one environment the name gains `_<ENV>`, and when that environment defines more than one schema it gains `_<SCHEMA>` (e.g. `ADT_TEAMDB_DEV_DA` for `connections/TEAMDB.yaml` with several environments and schemas), so every environment/schema pair maps to its own name. A project using the generic `connections.yaml` filename is named after its project folder instead.
- **Transparency.** The first SQLcl call registers the connection and records the assigned name on the schema's `db:` block in the connection YAML (`sqlcl:`), together with a credential fingerprint (`sqlcl_sync:`). Edit `sqlcl:` by hand to pin a different name, a recorded name always wins over generation.
- **A failed connect is an error, not an empty result.** Every generated `connect` runs under `WHENEVER SQLERROR EXIT FAILURE`, so a connection that could not be opened fails the command with the SQLcl/Oracle message rather than letting the script run on to its `exit;` and return `0`. That guard is also what makes the two recorded keys trustworthy: `sqlcl:` and `sqlcl_sync:` are written **only after the run that carried the registration actually succeeded**. If you see the export fail and the two keys stay absent, that is the intended behaviour, deleting them by hand and re-running is not a fix for a credential or network problem, it just re-attempts the registration.
- **Credential changes.** On every SQLcl call ADT recomputes the fingerprint over the username, password, host/service, wallet path, and wallet password. Any change (e.g. after `-set-pwd`) re-registers the named connection automatically on the next call.
- **Machine moves.** The YAML travels with the project; SQLcl's store does not. When the store has no such name (new machine, cleared `~/.dbtools`), the call fails fast and ADT re-registers and retries automatically.
- **Wallets.** Registration passes `-cloudconfig` with an absolute wallet path, so OCI wallet projects need nothing extra.
- **Opt-out.** Set `sqlcl_named_connections: false` in project `config.yaml` to restore the old inline `connect user/"pwd"@service` scripts.
- **Driver.** SQLcl is always launched on the JDBC **thin** driver: ADT starts it with `ORACLE_HOME` withheld from its environment, because SQLcl's launcher otherwise reads that variable as "use the OCI thick driver" and builds a `jdbc:oracle:oci8:` URL the JVM cannot satisfy on macOS (`no ocijdbc23 in java.library.path`, whatever the Instant Client version). Nothing else changes, `PATH` still finds the launcher, `TNS_ADMIN` still resolves aliases, wallet connects are unaffected, and ADT's own python-oracledb connection keeps thick mode, since only the SQLcl child is started without the variable.

---

← [docs/README.md](README.md) index
