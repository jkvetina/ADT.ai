# Project Configuration (adtai)

ADT.ai is configured by files in your project rather than by flags you retype. This page covers where those files are looked for, the identity and timeout keys, how line endings are written, the session startup script, and the environment variables ADT.ai fills in for itself when a tool starts it outside your shell.

The shipped `config/config.yaml` comments every key it carries, so that file is the reference. What follows is the part a comment cannot explain.

<br>

## Config, connections and wallets

Config defaults load from the shipped ADT.ai `config/` folder first, then project config overlays them from `-config-dir`, `<root>/config/` and `<root>/`, so a project always wins over a global default.

Connection files resolve by **first match wins**. ADT.ai loads the first candidate that exists and merges no layers. The order, where the folder is the `<root>` directory's own name:

1. Paths configured through `connections.path`, `connections_path` or `connections_dir`, then explicit `-config-dir` folders. Searched first when set.
2. `<root>/connections.yaml`, the generic name, project root only.
3. `<root>/connections/<FOLDER>.yaml`, folder-named, in the project's `connections/` folder.
4. `<ADT.ai checkout>/connections/<FOLDER>.yaml`, folder-named, in the ADT.ai `connections/` folder.

`connections.file` replaces the derived filename everywhere, so a named file is looked for in each of those locations instead of the generic and folder-named ones. Combined with `connections.path`, that keeps sensitive connection files outside the repository while the project keeps its config:

```yaml
connections:
  path: /secure/path/connections
  file: PROJECT.yaml
  wallet_path: /secure/path/connections/wallets
```

Wallets are searched in the ADT.ai `connections/wallets/` folder first, then in configured wallet paths and project-local wallet folders. Where a `Wallet_NAME.zip` is present but `Wallet_NAME/tnsnames.ora` is missing, the zip is extracted before connecting.

A command naming an environment or schema that is not configured shows the loaded connection file to edit, then the environments or schemas that do exist, as a sorted indented list.

For how a stored password is protected and what to configure when storing one is unacceptable to a security review, see [connection_security.md](connection_security.md), which is written to be handed to a reviewer rather than to a developer.

<br>

## Developer identity

`config/IDENTITY.yaml` answers "who am I", for the database and for git alike. It is optional, gitignored, and never committed. The first copy found on the search path wins, so a project's own file overrides one sitting in the ADT.ai install root.

`doctor -init` scaffolds it, prefilling `apex_account` and `email` from `git config user.name`/`user.email` in the project folder when that folder has a git identity to read. `db_schema` has no git equivalent and always ships as a commented placeholder. An existing file survives untouched unless `-init -force` is given.

```yaml
db_schema               : YOUR_SCHEMA       # your Oracle working schema
apex_account            : FIRST.LAST        # your APEX workspace developer login
email                   : you@example.com   # your commit email, read by every -my
```

- **`db_schema` is the database identity.** Every new connection runs `DBMS_SESSION.SET_IDENTIFIER` with it **before** `STARTUP.sql`, so sessions are attributable through `V$SESSION.CLIENT_IDENTIFIER` and audit trails with no hand-written startup block. `export_db -my` narrows the export to the objects it has changed, which works because a project DDL trigger reading that identifier is what writes the `changed_by` the filter matches on.
- **`email` and `apex_account` are the commit identity.** Every `-my` and `-by` filtering git history reads `email`: [`search_repo`](search_repo.md), [`rebuild`](rebuild.md) and [`calendar`](calendar.md). [`export_apex -my`](export_apex.md) reads both, matching APEX workspace developers on the login as well as on the address.

**Git is the fallback, never a second source of truth.** State `email` here and every command uses it. State nothing and each half falls back independently to `git config user.email` and `user.name`, so a checkout with no identity file behaves as it always has, and a file naming only an account keeps the git address rather than losing it.

That fallback is the reason to set `email` at all. It is worth stating whenever your git author differs from the identity your work should be attributed to, which is the ordinary case on a machine account, a shared runner, or a laptop carrying a corporate address.

<br>

## Connect and query timeouts

Two independent knobs live in project `config.yaml`, both plain numbers of seconds:

```yaml
connect_timeout_seconds : 15
query_timeout_seconds   : 1200
rest_timeout_seconds    : 60
```

`connect_timeout_seconds` bounds only the connection attempt, reconnecting to a different schema included. `query_timeout_seconds` bounds each query round trip once connected, so a slow query keeps its own budget and is never aborted early because the connect timeout is short.

Those two bound the Python driver. SQLcl is a separate child process and is deliberately left unbounded, so a command that drives a long script through it runs for as long as the work takes.

The one exception is `rest_timeout_seconds`, which bounds `export_apex -rest`, whose SQLcl call could otherwise sit for many minutes showing nothing but a crawling bar. Past the budget SQLcl is killed and the run reports the timeout with whatever it printed first.

A missing, non-numeric or non-positive value falls back to the default rather than removing the bound.

<br>

## Line endings

Every generated text file, exported DDL, CSVs, merge scripts, patch files, logs and caches, is written with **LF line endings on every platform** by default, so one export produces identical bytes on macOS, Linux and Windows and never shows a whole-file line-ending change. Set `file_crlf` to write CRLF everywhere instead, typically to match a CRLF working tree:

```yaml
file_crlf               : False
```

The setting is a **normalization rather than an addition**. Whatever the database returns is collapsed to plain line breaks first, then written with the configured ending, so the configured ending is the only one in the file.

That matters because Oracle returns stored source verbatim. An object ever compiled from a Windows client carries CRLF inside the dictionary, and such objects would otherwise keep it under `false` and grow a second carriage return under `true`.

Two deliberate details. Raw LOB sidecar files are never translated, mirroring the stored value byte for byte whatever the key says. And flipping the key rewrites nothing that has not changed, because the exporter compares content ignoring line endings, so existing files adopt the new ending on their next real change.

<br>

## How a list of files reads

Every section that lists files, on `export_db` and `search_repo` alike, opens on the folder its rows share and gives every directory below it a row of its own, each two spaces further in:

```text
PROCESSED FILES: SANDBOX
----------------
  - sandbox/database/tables/
    - BILLING/
      - monthly_total.sql
    - order_line.sql
  - sandbox/database/synonyms/
    - BILLING/
      - monthly_total.sql
```

The first row is the anchor, the folder `path_objects` names, so one object type reads as one folder however many segments its own name carries. Everything under it splits a row per directory, which is where a sub-folder made by `export_db -groups` lands.

A path outside that layout, an install script or a per-patch script, anchors on the directory above the one it sits in, so `patch_scripts/MY_TASK/tables_after/t_orders.sql` reads as the patch code, then the slot, then the file. The trailing slash tells a folder line from a file row.

`PATCH FILES:` is the one section that does not group: it prints one whole path per schema, that path being the entire answer it gives.

Anything hanging off a row sits two spaces further again, which is how the commits under `WARNING - OUTDATED FILES:` line up with the file they are about. Those commit rows carry the indent and no dash, because they say why a file is listed rather than being another entry in the list.

Set the key to `False` for the flat one-path-per-row list:

```yaml
nested_files            : True
```

<br>

## Session startup script

An optional `config/STARTUP.sql` runs once on every new database connection, on the Python driver and in SQLcl alike. The repository ships only the committed `config/STARTUP.sample.sql`; copy it to `config/STARTUP.sql`, which is gitignored, so personal session setup never dirties the repository.

The order is `DDL_LOCK_TIMEOUT`, then the automatic session identifier from `config/IDENTITY.yaml`, then your script, so anything you set here wins. Put another `ALTER SESSION SET DDL_LOCK_TIMEOUT` in it when a project needs a different object-lock wait.

It is authored as an ordinary SQLcl script and may mix three statement kinds:

- **SQL\*Plus directives** (`SET SERVEROUTPUT ON`, `SET DEFINE OFF`) are client-side and the database never sees them. On the Python path they are filtered out, with `SET SERVEROUTPUT` emulated server-side. SQLcl deploys read the file natively.
- **`ALTER SESSION` and plain SQL** end with `;`. `SET TRANSACTION` is real SQL rather than a directive, and is sent to the database.
- **PL/SQL blocks** end with a lone `/` on its own line.

```sql
ALTER SESSION SET NLS_NUMERIC_CHARACTERS = '. ';
ALTER SESSION SET NLS_DATE_FORMAT        = 'YYYY-MM-DD HH24:MI';

BEGIN
    DBMS_APPLICATION_INFO.SET_CLIENT_INFO('ADT');
END;
/
```

Statements run fail-fast: any error aborts the connection and reports the offending line. Resolution is nearest-wins, so a project copy overrides the repository-level one, the file may be absent entirely, and a comment-only file counts as empty.

<br>

## Naming the parts of a patch

A group of keys decides what a patch is called and how it is laid out. None of them changes what a patch contains. The full list is commented in the shipped config; two of them deserve a warning rather than a row.

**`patch_session_directives` carries a real risk if you narrow it.** `SET DEFINE OFF` is the load-bearing entry, because SQLcl reads `&` as a substitution prompt, so a package body holding a literal `&APP_ID.` stops a terminal-less deploy dead. An empty list is a legitimate answer only for a project whose own template sets the session up instead.

**`patch_folder` is read in both directions.** ADT writes a folder name from it and parses folder names back with it, so a project that changes it stops recognising the folders it already has.

Nothing is renamed or migrated, and the old folders are simply no longer listed or selectable. Change it before a project's first patch, or accept that the ones on disk become history.

One rule covers the rest of the file: **every default ships the value ADT.ai used before the key existed**, so a project that sets nothing gets exactly what it got before.

That cuts both ways while you are testing a change, since a default matching the old hardcode is precisely what hides a key nothing reads. Prove a key by setting it to something else and watching the output move.

<br>

## Environment variables

ADT.ai reads `ADT_KEY`, which decrypts connection passwords, then `ADT_ENV`, `ADT_REPO`, `ADT_CLIENT`, `ADT_PROJECT`, `ADT_BRANCH` and `ADT_SCHEMA`.

It also reads the Oracle variables an Instant Client setup exports: `ORACLE_HOME`, `TNS_ADMIN`, `NLS_LANG`, `DBVERSION`, `DYLD_LIBRARY_PATH`, `LD_LIBRARY_PATH`, `OCI_LIB_DIR`, `OCI_INC_DIR` and `JAVA_TOOL_OPTIONS`.

In a terminal those come from your shell startup file and everything works. They do not survive the trip through an AI tool, which spawns a non-login, non-interactive shell that never sources it.

ADT.ai would then start with none of them, so encrypted connections fail to open, thick mode is unavailable and SQLcl is not on `PATH`, all of which read like a config bug rather than a missing environment.

So ADT.ai fills them in itself. On every run, when `ADT_ENV` or `ORACLE_HOME` is unset, it reads your shell startup file and hydrates the variables above, then appends the client folder and its `sqlcl/bin` to `PATH`:

- **An explicit value always wins.** A variable already in the environment is never overwritten, so a value you set on the command line or in CI behaves as you expect. When both `ADT_ENV` and `ORACLE_HOME` are already set, nothing is read at all.
- **Your startup file is parsed, not executed.** `export VAR=value` lines are read as text, with `~` and `$VAR` expansion. Only when a sentinel is still unresolved afterwards does ADT.ai fall back to running your shell, which also sees variables set inside a function, a conditional or an `eval`.
- **Which file follows `$SHELL`.** zsh reads `~/.zshrc`, `~/.zprofile` and `~/.zshenv`; bash reads `~/.bash_profile`, `~/.bashrc` and `~/.profile`. An unknown shell falls back to all three of the common ones.
- **Nothing here can fail your command.** A missing file, an unreadable one, or a shell that will not run leaves the environment untouched and the command proceeds.
- **`ADT_KEY` is never printed.** Hydration carries variable names only, and [`doctor`](doctor.md) keeps showing the key as `<redacted>`.
- **macOS and Linux only.** On Windows hydration is a no-op, so set the variables yourself.

Hydration announces nothing of its own. What it did is visible where it matters: [`adtai doctor`](doctor.md#output) prints the values the process actually holds, so an `ENVIRONMENT:` section carrying your real `ADT_ENV` and `ORACLE_HOME` under an AI tool is hydration having worked.

Which variable does the real work, in case you are debugging a connection rather than reading for pleasure:

- **Thick-mode connections need `ORACLE_HOME`**, and nothing else. The Python driver reads it when initializing the Oracle Client and finds the client library under that folder.
- **SQLcl needs `PATH`**, and nothing else. It runs on the JDBC thin driver, so it wants no Oracle client libraries, only to be found.

`DYLD_LIBRARY_PATH` and `LD_LIBRARY_PATH` are carried across because they are right on Linux and harmless on macOS. On macOS they achieve nothing: the loader fixes its search path when a process starts and never rereads the variable, and System Integrity Protection strips `DYLD_*` when launching a protected binary.

Nothing depends on them, so nothing breaks, but do not expect them to fix a `DPI-1047`. That one is `ORACLE_HOME`.
