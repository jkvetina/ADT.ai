# Environment Check and Updates (adtai doctor)

`doctor` verifies the local toolchain ADT.ai depends on, Python, Git, Java, SQLcl, `oracledb`, Instant Client, reports versions and available updates, and applies explicitly requested updates or project scaffolding. Run it after installing ADT.ai, after a toolchain upgrade, or whenever a command fails in a way that smells environmental.

Run the setup check from any folder:

```bash
adtai doctor
```

Plain `doctor` is read-only. It checks Python, Git, Java, SQLcl, the Python `oracledb` module, Instant Client, `JAVA_TOOL_OPTIONS`, and ADT-compatible environment variables. By default it also checks online update availability for ADT.ai, Java, SQLcl, `oracledb`, and Instant Client. For ADT.ai, editable/git installs are compared against the configured `origin`, and normal installs are compared against the latest public GitHub Release in [`jkvetina/ADT.ai`](https://github.com/jkvetina/ADT.ai) before falling back to PyPI metadata. It prints current versions, runtime environment, row-level update/warning statuses, and, only when a check found something out of date, the explicit update actions available. Install and environment setup guidance is in [SETUP.md](../SETUP.md); this file owns the detailed Doctor command behavior.

Old ADT had a self-update path that could run `git pull` from inside the tool and reinstall Python requirements. ADT.ai keeps update behavior explicit and always prints current versions before any action.

The status output always starts with current component versions (`ADT.ai`, Python, Git, Java, `oracledb`, Instant Client, SQLcl), then an `ENVIRONMENT:` section with relevant runtime variables such as `ARCH`, `JAVA_TOOL_OPTIONS`, `LANG`, `NLS_LANG`, `ORACLE_HOME`, resolved `SQLCL` launcher, `ADT_ENV`, and `ADT_KEY`. `ADT_KEY` is never printed directly: non-empty values render as `<redacted>`, and missing values render as `<empty>`. Update subprocesses force English/UTF-8-safe settings so SQLcl, Oracle messages, and Python/pip output are not affected by local language overrides.

When ADT.ai had to fill in its own environment, the usual case under an AI tool, whose shell never sourced your startup file, the `ENVIRONMENT:` section ends with a `HYDRATED` row naming what was filled in and where it came from, for example `ADT_ENV, ADT_KEY, ORACLE_HOME, PATH from ~/.zshrc`. Only variable names appear; `ADT_KEY` still renders as `<redacted>` on its own row. In a normal terminal nothing needs hydrating and the row is absent. See [docs/README.md](README.md) §Environment Variables for the full behavior.

Use local checks only when you do not want Doctor to call remote metadata pages:

```bash
adtai doctor -offline
```

Selected rows append a dot leader plus status, capped at 78 characters. Successful local checks do not print `OK`; a plain version row means Doctor detected that local value and found no newer version online, or online checks were skipped with `-offline`.

- `UPDATE` means a newer available version was detected online.
- `WARN` means the machine can still run read-only Doctor, but optional setup is missing or uncertain, such as Java, SQLcl, Instant Client, `ADT_ENV`, `ADT_KEY`, or `JAVA_TOOL_OPTIONS`.
- `FAIL` means a required local prerequisite is missing or broken, such as Git or the Python `oracledb` module; Doctor exits non-zero.

## Version reporting

The `ADT.ai` row reports the package's own `__version__`, which is the last published release. In a **git checkout**, an editable install, or the repo itself, the row appends a marker:

```text
  ADT.ai               | 0.8.5 + WIP
```

The checkout is that release plus whatever has landed since, so it does not claim to *be* the release. An installed copy has nothing beyond the release and shows the bare `0.8.5`. The marker is display-only: the online update comparison and the upgrade paths use the bare version, so a checkout level with its release is not reported as out of date. The number itself lives in the DEV repo (`src/adt_ai/__init__.py` and `pyproject.toml`, which must agree); the public build refuses a release version DEV does not already declare, which is what keeps the two from drifting apart.

## Actions

`ACTIONS:` closes the run, and lists only upgrades an online check actually found:

- `-update` appears when ADT.ai, `oracledb`, or SQLcl is behind. `oracledb` counts because the full update reinstalls `requirements.txt`.
- `-sqlcl` appears only when SQLcl itself is behind.

When neither applies the whole section is omitted, header included, an up-to-date machine is offered nothing. `-offline` checks nothing online, so no status is backed by a real check and the section is likewise absent. Under `-update` and `-sqlcl` the section always prints, because it reports the actions that ran.

Run the full ADT.ai, `requirements.txt`, and SQLcl upgrade:

```bash
adtai doctor -update
```

Upgrade SQLcl only. This runs immediately without `-update`, checks Oracle's SQLcl download page for the current release, downloads newer SQLcl ZIPs to a temporary folder, and replaces the resolved SQLcl install folder:

```bash
adtai doctor -sqlcl
```

Bootstrap a project skeleton:

```bash
adtai doctor -init
```

`doctor -init` writes the project override config, copies the current ADT.ai root `.gitignore` and the `config/patch_template/` scaffold verbatim, and writes the safe `connections/.gitkeep` / `connections/wallets/.gitkeep` placeholders. It does not create generated-cache folders, APEX credentials folders, connection YAML files, or wallet contents. Existing generated files are skipped; use `-force` to overwrite them:

```bash
adtai doctor -init -force
```

To initialize a different folder:

```bash
adtai doctor -init -root /path/to/project
```

The patch templates are scaffolded because `patch -create` reads them from the **project** root, so a folder that only ships with ADT.ai is a folder nobody has. All eight files land verbatim, see [patch → Templates](patch.md#templates) for the slots and what each file does. **Read `db_end/` before your first deploy:** those three refresh every materialized view, gather schema stats and run every enabled daily job with a 60-second wait, and the APEX pair carries `<APEX_WORKSPACE>` / `<APEX_APP_ID>` / `<APEX_VERSION>` placeholders you fill in once. Delete what your deploy should not do.

Patch *scripts* are still not scaffolded, `patch_scripts/` is per-patch-code and generated per patch, so there is nothing fixed to seed.

`adtai update` and `adtai upgrade` are not public commands. They print the generic ADT.ai error banner and guide users to `adtai doctor -update` or `adtai doctor -sqlcl`.

`adtai init` is not a public command. It prints the generic ADT.ai error banner and guides users to `adtai doctor -init`.

## Arguments

| Argument | Repeatable | Default | Notes |
| -------- | ---------- | ------- | ----- |
| `-offline` | No | off | Skip online update metadata checks and show local versions only. |
| `-update` | No | off | Run the full ADT.ai, Python requirements, and SQLcl update workflow. Cannot be combined with `-sqlcl`. |
| `-sqlcl` | No | off | Upgrade SQLcl only. Runs immediately without `-update`; cannot be combined with `-update`. |
| `-init` | No | off | Scaffold project config, copy ADT.ai's current root `.gitignore` and `config/patch_template/`, and add safe local connection/wallet placeholders. |
| `-root`, `--root` | No | `.` | Project root folder for `-init`. |
| `-force`, `--force` | No | off | With `-init`, overwrite generated template files that already exist. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [docs/README.md](README.md) index
