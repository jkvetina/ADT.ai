# Where the Password Lives (adtai)

![Plain text, encrypted, your vault, or no password at all.](images/connection_passwords.png)

A connection file can hold cleartext, encrypted text, a vault command, or no password. This page covers those formats, masking and rekeying. It remains a local secret file in every mode: keep connection files, key files and wallets out of Git.

[connection.md](connection.md) covers the command that writes these values, and [connection_security.md](connection_security.md) states the same ground for a security reviewer rather than a developer.

## The stored format

An encrypted value looks like this, and the three parts are a format version, a salt, and the ciphertext:

```yaml
pwd: adt2$UZEXtYJdGemmYw8AQk8KAQ==$gAAAAABqi0W1o2TXIgb0BYlUZ6_Htz...
pwd!: Y
pwd_key: "d5506a8a8ca5"
```

The quotes around `pwd_key` are load-bearing, so keep them if you ever edit the line by hand. A 12-character hex digest reads as a number to a YAML parser about once in every 440: one such value is an integer, another a floating point number large enough to overflow.

Unquoted, it can be rewritten as `inf` the next time anything edits the file, which turns a correct key into a permanent wrong-key error. A recorded value that is not a readable digest is ignored rather than trusted, so a file already damaged that way still opens.

Each secret carries its own 16-byte random salt, so encrypting one password twice gives two different values and no table built against one project helps against another.

The salt lives inside the value rather than beside it, which keeps the encrypted value self-contained. A colleague or a CI runner receiving the file through an approved secret channel can read it with the separately supplied key. Key derivation runs at 600000 PBKDF2-HMAC-SHA256 iterations, current OWASP guidance, costing roughly 160 ms per secret.

**Older files keep working.** A value with no version prefix is read as the earlier format, and nothing rewrites it underneath you. Writing that secret again with `-encrypt`, or running a rekey, moves it to the current format.

The version prefix is also what makes the next change painless: a value written by a newer ADT.ai than the one you are running says so by name instead of failing as a corrupt password.

### The key fingerprint

`pwd_key:` records which key the value is under. It is a 12-character digest, not the key and not the password, and it exists because a wrong key and a corrupted value otherwise report the same error, so the commonest mistake of all used to read as a damaged connection file:

```text
Wrong encryption key for DEV.APP pwd: the stored value carries key fingerprint
4f2a91c7de08, the key in use fingerprints as 9b31e77c05aa. Pass -key or set
ADT_KEY to the key this value was encrypted with.
```

The digest comes from that value's salted key material, not the key alone. Testing a guess against it therefore costs the same 600000 iterations as testing the ciphertext. The fingerprint does not weaken the encrypted value, but the whole connection file still stays out of Git.

It also means two secrets encrypted with one key have different fingerprints, which is the property that lets a rekey be verified secret by secret rather than trusted in bulk.

## The secret can come from your own vault

A connection file does not have to hold the password at all. Point `pwd_cmd:` at a command and ADT.ai runs it, taking its output as the password:

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

The wallet password has the same key on the wallet block as `wallet_pwd_cmd:`, and the encryption key has an environment variable of its own, `ADT_KEY_CMD`. Whatever your secret manager is, if it can print a value it works:

```yaml
pwd_cmd: op read op://Engineering/DEV_APP/password
pwd_cmd: vault kv get -field=password secret/oracle/dev/app
pwd_cmd: pass show oracle/dev/app
```

### What this buys, and what it does not

What you get is custody. Your vault holds the secret, rotates it, revokes it, and records who asked for it, and revoking it there revokes it everywhere. A password copied into a file, even an encrypted one, is a copy you now have to track.

What you do not get is secrecy from the tool. The value still reaches the Oracle driver as plaintext, and anyone allowed to run `adtai` can run the vault command themselves, so this hides nothing from a person or an agent already on that machine. Anybody telling you otherwise is selling something. The interesting property is the first one.

### The rules, all four of them

**No shell.** The command runs as an argument list. A string is split the way a shell would split it, quotes respected, but nothing is ever handed to `sh`, so an `&` or a `;` inside a vault path is passed through as text rather than run. Quote a path that has spaces in it, or write the command as a YAML list when quoting gets awkward:

```yaml
pwd_cmd: ["op", "read", "op://My Vault/DEV APP/password"]
```

**One source per secret.** A block carrying both a command and a stored value is refused, by name, instead of one of them quietly winning:

```text
DEV.APP pwd: pwd_cmd is configured beside pwd, so the secret has two sources. A
block reads its secret from exactly one place: remove pwd to fetch it with the
command, or remove pwd_cmd to keep the stored value.
```

That covers the marker and the fingerprint too, and it counts what the schema actually resolves, so an environment-level value inherited from above collides with a schema-level command just as a neighbouring one does. The same holds for the encryption key and its command form.

A `-key` on the command line still wins over both, because that is an override for one run rather than a second piece of configuration.

**Keep secrets out of command arguments.** A literal `-key VALUE`, a token inside `ADT_KEY_CMD`, or a token inside `pwd_cmd` can be visible in shell history and process listings before ADT.ai starts. Prefer `-key /secure/adt.key`, set `ADT_KEY` to that owner-only path, or use a provider command whose authentication is held by the provider rather than written in its arguments.

**The command must be able to finish on its own.** Its input is closed, so a command that stops to ask something fails immediately rather than hanging behind a pipe nobody can see, and it gets 60 seconds before ADT.ai gives up on it. Biometric and desktop unlock prompts are fine; a command that wants an answer typed into the terminal is not.

**A failure names only the executable, never its arguments or output.** Standard output is where the secret arrives, and provider arguments or standard error can also contain tokens. All three are suppressed. The diagnostic retains the context, executable and exit status:

```text
DEV.APP pwd: command failed with exit status 1: op
```

Run the provider command directly in a trusted terminal when its own detailed diagnostic is needed. ADT.ai also removes `ADT_KEY` and `ADT_KEY_CMD` from the environment of the provider process; provider-owned variables such as `VAULT_TOKEN` are left intact.

One trailing newline is dropped from the output, since every CLI above adds one. Nothing else is trimmed, so a password ending in a space survives.

## Or no password in the process at all

The options above move the secret around. `sqlcl_only` removes it from ADT.ai entirely. Put this in the project config:

```yaml
sqlcl_only : True
```

Every database call then goes through SQLcl rather than the Python driver, and SQLcl connects by the name it holds in its own secure store, so the credential never becomes a value ADT.ai has. A connection file under this switch needs no password at all:

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

The name has to be in SQLcl's store first, and ADT.ai puts it there on any ordinary run that has the password, so the usual path is to connect once the normal way and then turn the switch on. A run that finds no stored connection stops and says so.

What it costs is time. The session opens once per run, at about 3 seconds, and each query after that costs tens of milliseconds where the driver costs single digits. On a large export that adds up, which is why the default is off.

What it does not cost is behaviour: the same commands print the same output and write the same files, and there is no command the switch cannot serve.

**On Windows the switch needs one extra package.** SQLcl has to run on a terminal, because the JVM holds its output back when it is talking to a pipe, and Windows has no `pty` in its standard library the way POSIX does. So install `pywinpty` beside ADT.ai:

```bash
pip install pywinpty
```

It is declared as a Windows-only dependency, so an ordinary install on Windows already pulls it in and a macOS or Linux install never sees it. Where it is missing, `sqlcl_only` stops and names the package rather than failing somewhere further down.

**Windows runs the switch differently, and two commands pay for it.** SQLcl never draws its prompt inside a Windows console: it echoes each statement and runs none of them, while the same machine runs a SQLcl script normally. Windows therefore drives **one SQLcl script per statement**, which is the shape measured to work there.

What that costs is one database session per statement instead of one per run. Almost nothing notices, because every statement carries its own connect and its own session setup, so the output and the files are the same.

Two commands do notice, since they need two statements to land in one session, `export_apex` (fills an APEX collection with one statement, reads it with the next) and `ut` (starts a coverage profiler, runs the tests, then reads the profile). On Windows they stop with a message saying so rather than handing back an empty answer.

Run those two with the switch off, or from macOS or Linux.

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

`auth: external` skips password resolution completely. Not resolves to nothing, skips: no stored value, no marker, no command, no key, and the decrypt path is never reached, so a file in this mode cannot fail on a missing key.

`tns` is the alias your wallet files the credential under, and `wallet_path` is the folder holding the wallet beside its `tnsnames.ora` and `sqlnet.ora`.

**This wallet is not the ADB wallet.** ADT.ai already handles the mTLS wallet an Autonomous Database hands you, through a wallet password. That one proves who the client is and still needs a password to log in. A SEPS wallet holds the login itself and needs none. Different file, different job, and a project can use one, the other, or both.

Building one, once, with the Oracle client's own tool:

```bash
mkstore -wrl /secure/seps_prod -create
mkstore -wrl /secure/seps_prod -createCredential ADT_PROD_APP APP
```

Two requirements come with the mode, and both are the driver's rather than ours. It is **thick mode only**, so ADT.ai turns thick on for you when it sees `auth: external` rather than making you set two things that only work together. And it needs the **Oracle client libraries** on the machine, which [SETUP.md](../SETUP.md) covers; point `client_lib_dir` at them.

Being exact about what it is: SEPS is still password authentication, and the database reports the authentication method as `PASSWORD` rather than external. What changes is who holds the password.

It lives in the wallet, the client library reads it, and it never exists as a value inside ADT.ai, which is the one claim on this page that survives an agent reading the tool's memory.

## A loaded or pending credential is masked

Once loaded, schema and wallet passwords use a wrapper that renders as `***`, so neither can reach stdout, a log, a generated script or an error by accident. The same wrapper covers those passwords, the write key and both rekey keys while `adtai connection` prepares an edit.

That covers the cases nothing in the code deliberately prints: an interpolated connection inside an exception, a debug dump, a test runner showing local variables on failure.

Exactly four places unwrap a value, and each hands it straight to a consumer that cannot accept a mask: the driver's connect call, the connect line of a generated SQLcl script (which named connections avoid entirely), the credential fingerprint that emits nothing but a digest, and the connection editor's single boundary immediately before encryption, key resolution or owner-only file writing.

A contract test pins that list, so another unwrap fails the test suite rather than passing review.

Two limits, stated rather than implied. This does not hide a credential from an agent allowed to run `adtai` at all, because such an agent can open its own database session without ever reading a password.

ADT.ai masks credentials by default, contracts the deliberate plaintext exits, scrubs SQLcl output and removes its encryption keys from child environments. This is not isolation from software running as the same user, and masking is not encryption at rest; `-encrypt` provides the latter.

## Changing the encryption key

`-rekey` re-encrypts every secret in the resolved file under a new key, in one pass across every environment and every schema. It needs both keys, and either may be a value or a path to a key file:

```bash
adtai connection -rekey -old-key /secure/adt.key -new-key /secure/adt-2027.key -go
```

It takes no `-env` and no `-schema`. A rekey covering part of a file would leave one no single key can open, which is the failure the whole action exists to avoid. Without `-go` it previews, naming each secret it would rewrite and rendering none of them:

```text
  Action            re-encrypt 1 secret under the new key
  Mode              preview (re-run with -go to apply)

  DEV.APP.pwd
```

Cleartext passwords are left exactly as they are. A rekey rewrites the values carrying an encryption marker, so a file with no encrypted secret is an error rather than a silent success, and a cleartext password becomes encrypted through `-set-pwd -encrypt` rather than through a rekey.

**Re-encrypting is not rotating the database password.** They are two separate rotations, and after a suspected leak only the second one matters. Changing the key protects a connection file somebody might obtain in future; it does nothing about a password an attacker already has, which is changed in the database and then written here. Do both, in that order, and do not let the first stand in for the second.

**Nothing is written unless everything can be.** Every secret is decrypted before any is rewritten, so a wrong old key leaves the file untouched instead of half converted. The recorded fingerprints are what let the command tell you which of two things went wrong:

- Every recorded fingerprint disagrees with the old key, so the key is simply wrong: `wrong -old-key: it matches none of the 1 recorded key fingerprints in connections.yaml. Nothing was written`.
- Some agree and some do not, so the file is already under more than one key. The message names the odd ones out and tells you to settle them with `-set-pwd -encrypt` before rekeying. That is the case a fingerprint-free file could never report.

A rekey is also the wholesale way off the earlier stored format: values written by an older ADT.ai come out with a fresh salt and a recorded fingerprint each, whether or not the key actually changed. Passing the same value for both keys is a legitimate way to migrate a file in place.
