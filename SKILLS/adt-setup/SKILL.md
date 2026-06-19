---
created: 2026-06-10
updated: 2026-06-11
name: adt-setup
version: 1.0.0
tags: [oracle, apex, deployment, setup, doctor]
description: "Install and verify ADT.ai: pip install, PATH and environment variables, Oracle Instant Client and SQLcl, connections and wallets, and the adtai doctor health check and update flow. Use when setting up or troubleshooting ADT.ai on a machine."
---
# ADT.ai Setup

Install ADT.ai, wire up the runtime environment, and verify the machine with `adtai doctor`. The repo's `SETUP.md` is the concise human install/environment reference; this skill is the operating checklist. Day-to-day command usage is the separate `adt` skill.

The command is `adtai` (aliases: `adt`, `python -m adt_ai`).

## Install

From the ADT.ai repo folder:

```bash
python3 -m pip install -e .
```

This puts `adtai` and `adt` on `PATH`. Re-run after pulling changes only if dependencies changed — or use `adtai doctor -update` (below).

Windows uses `python` instead of `python3`:

```powershell
python -m pip install -e .
```

## Prerequisites on PATH

Confirm the core tools are available:

```bash
python3 --version
git --version
java --version
sql -V
```

`python3` and `git` are required. `java` and `sql` (SQLcl) are needed for APEX export and SQLcl-backed flows.

## Environment variables

Force English output so SQLcl and Oracle messages stay stable and parseable:

```bash
export JAVA_TOOL_OPTIONS="-Duser.language=en"
```

For thick Oracle connections, make Instant Client and SQLcl reachable:

```bash
export ORACLE_HOME="$HOME/instantclient_19_16"
export PATH="$PATH:$ORACLE_HOME:$HOME/sqlcl/bin"
```

Optional ADT-compatible defaults:

```bash
export ADT_ENV="DEV"
export ADT_SCHEMA="CORE"
```

`ADT_KEY` is deliberately omitted: it is **not** an ADT.ai setting and protects nothing. The name is reported for ADT compatibility and future encrypted-password parity — current ADT.ai does not decrypt connection or wallet passwords from it, so do not set it expecting password protection. `doctor` never prints the value — it renders as `<redacted>` when already set and `<empty>` when missing.

On Windows use `setx NAME "value"` for each of these (takes effect in new shells).

## Connections and wallets

ADT.ai keeps sensitive runtime material separate from project output. Connections resolve by **first match wins** (no merging):

1. configured `connections.path` / `--config-dir` override
2. `<root>/connections.yaml`
3. `<root>/connections/<FOLDER>.yaml`
4. the ADT.ai repo's `connections/<FOLDER>.yaml`

`connections.file` overrides the looked-up filename. Wallets live under `connections/wallets/`; if only `Wallet_NAME.zip` is present, ADT.ai extracts it before connecting. The `connections/` folder is gitignored except `.gitkeep` — it is local runtime state.

To keep secrets outside the project repo, point the project config at an external folder:

```yaml
connections:
  path: /secure/path/connections
  file: CORE23.yaml
  wallet_path: /secure/path/connections/wallets
```

When a requested environment or schema is missing, ADT.ai prints the connection file to edit and the configured choices as a sorted, two-space-indented list.

## doctor — verify and update

`adtai doctor` is the health check. **Plain `doctor` is read-only** — it changes nothing, never updates ADT.ai, never reinstalls requirements, never downloads SQLcl.

```bash
adtai doctor
```

It reports current versions (ADT.ai, Python, Git, Java, `oracledb`, Instant Client, SQLcl), an `ENVIRONMENT:` section (`ARCH`, `JAVA_TOOL_OPTIONS`, `LANG`, `NLS_LANG`, `ORACLE_HOME`, resolved `SQLCL` launcher, `ADT_ENV`, `ADT_KEY`), and online update availability for ADT.ai, Java, SQLcl, `oracledb`, and Instant Client.

Row statuses:
- `UPDATE` — a newer version is available online.
- `WARN` — read-only doctor still runs, but optional setup is missing/uncertain (Java, SQLcl, Instant Client, `ADT_ENV`, `ADT_KEY`, `JAVA_TOOL_OPTIONS`).
- `FAIL` — a required prerequisite is missing or broken (Git, `oracledb`); doctor exits non-zero.
- A plain version row with no tag means the value was detected and no newer version was found online (or online checks were skipped).

Run only local checks, with no remote metadata calls:

```bash
adtai doctor -offline
```

Run the full ADT.ai + `requirements.txt` + SQLcl upgrade (only when explicitly requested):

```bash
adtai doctor -update
```

Upgrade SQLcl only — runs immediately, checks Oracle's download page, fetches newer ZIPs, and replaces the resolved SQLcl install folder:

```bash
adtai doctor -sqlcl
```

Standalone `update` and `upgrade` command names are not public commands. They use the generic ADT.ai error screen and point to `doctor -update` or `doctor -sqlcl`.

## doctor -init — bootstrap project config

After install, scaffold a project config template:

```bash
adtai doctor -init
```

It writes the project config template, repo ignore rules for generated artifacts, and safe `connections/.gitkeep` / `connections/wallets/.gitkeep` placeholders. It never writes connection YAML secrets, wallet contents, generated-cache folders, or APEX credentials folders. Existing generated files are skipped; `-force` overwrites generated templates.

## Troubleshooting

- `adtai: command not found` or `adt: command not found` → re-run `python3 -m pip install -e .`, then open a new shell; confirm the Python scripts dir is on `PATH`.
- Garbled or non-English SQLcl/Oracle messages → set `JAVA_TOOL_OPTIONS="-Duser.language=en"`.
- `DPI-1047` or `libclntsh.dylib` missing → thick Oracle mode cannot locate Instant Client. Check `ORACLE_HOME` points at Instant Client, add it to `PATH`, open a new shell, and confirm with `adtai doctor` or `adtai doctor -offline`.
- "Schema/environment not configured" → open the connection file named in the error and add the listed choice.
- Always start troubleshooting with `adtai doctor` (or `adtai doctor -offline` when offline) — it pinpoints which prerequisite is the `FAIL`/`WARN`.

## Examples

Verify a fresh install end to end:

```bash
python3 -m pip install -e .
adtai doctor
```

Diagnose a broken setup without hitting the network:

```bash
adtai doctor -offline
```

Bring the local toolchain fully up to date:

```bash
adtai doctor -update
```
