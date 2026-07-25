# Export APEX Applications (adtai export_apex)

`export_apex` discovers and exports Oracle APEX workspaces and applications into your project folder — the APEX counterpart to `export_db`. `-reveal` answers "what workspaces, groups, and apps live in this environment?"; the format flags (`-full`, `-split`, `-readable`, `-rest`, `-files`, `-files_ws`, `-embedded`, `-checksum`) then export an application as a single SQL file, split per-component files, readable YAML, REST module definitions, static application/workspace files, or the application checksum, so app changes are diffable in Git like any other code.

Reveal APEX workspaces and applications from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_apex -reveal
```

The initial `export_apex` slice discovers applications only. In `-reveal` mode, it lists the workspace inventory once at the top, then lists applications for every schema configured in the selected connection environment unless `-schema` narrows the scan. Reveal keeps one APEX connection open and switches APEX workspace/security context for each schema's configured workspace before querying that schema's APEX applications. For normal exports, it uses the APEX schema default from the connection file. Workspace, application group, and application id scope still come from each connection schema's `apex:` section unless overridden on the command line.

When you export by application id (`-app <id>`, without `-schema`/`-reveal`), `export_apex` first reads the cached `config/apex_apps.yaml` (written by earlier exports). If an app's recorded `owner` schema differs from the default APEX schema, it connects straight to that owner schema and skips both the wasted default-schema connection and the live owner-discovery round-trip. Multiple `-app` ids that map to different owners each connect to their own owner schema. Apps not yet recorded in the file, a missing file, or `-schema`/`-reveal` fall back to connecting to the default schema and discovering the owner live.

A multi-schema export (`-schema DA GSN -full`, outside `-reveal`) executes schema by schema: connect to DA, discover and export its applications, print its own `TIMER`, then connect to GSN and repeat — exactly as if you had run the command once per schema, with the banner printed only once. If `-app <id>` names an app whose owner is not among the requested schemas, the owner lookup runs once, inside the last requested schema's own segment, and the owner schema it finds becomes its own appended segment (connection block, `APEX APPLICATIONS:` table, export, timer) rather than being folded into an already-printed segment. `-reveal` is unaffected — it stays a single connection with one shared inventory screen. See `USAGE.md` §Console Output Contract for the full multi-schema shape.

Limit the reveal to one workspace, group, and application list:

```bash
adtai export_apex -ws HUB -group CORE -app 100 200 -reveal
```

Show owner/application counts for all owners instead of only configured schemas, while hiding high-id temporary or backup apps:

```bash
adtai export_apex -reveal -owners -max_app_id 10000
```

Export every application in an id range, or from a minimum id upward:

```bash
adtai export_apex -app 0-9999 -all
adtai export_apex -app 0+ -all
adtai export_apex -app 0-99 100-999 -all
```

Choose export sections explicitly:

```bash
adtai export_apex -full -split -readable -rest -files
```

ADT.ai exports only the sections named on the command line. Use `-all` to export every supported format explicitly. ADT.ai does not read configured format defaults and does not support old ADT's `-only` or `-no...` format suppressor flags.

Export only selected pages and shared components from component-based formats:

```bash
adtai export_apex -app 100 -split -readable -page 1-50 55,56 60-62
adtai export_apex -app 100 -split -page 7
adtai export_apex -app 100 -split -component LOV:NAME% LIST:MENU%
adtai export_apex -app 100 -readable -component LOV:%
```


Export the application checksum — an ID-independent SHA-256 fingerprint of the whole app:

```bash
adtai export_apex -app 100 -checksum
```

`-checksum` writes one line to `checksum.txt` in the application folder, beside `f100.sql`. The fingerprint ignores internal component ids, so it changes when the application definition changes and stays stable across imports and environments. That makes it a cheap deploy gate: export the checksum and let Git answer "did anything actually change?" without diffing a full export.

```bash
adtai export_apex -app 100 -checksum
git diff --exit-code apex/100_CORE23/checksum.txt || echo "application changed"
```

Because a fingerprint covers the whole application, `-page`, `-component`, and `-recent` never filter it out, and a `-checksum` run never advances a `-recent` watermark — the fingerprint reports *that* the app changed, never which components were exported.

Export recent changes by one developer, or by the current git user:

```bash
adtai export_apex -recent 3
adtai export_apex -recent 3 -by JANE.DEV
adtai export_apex -recent 3 -my
```

`-my` compares `git config user.name` and `git config user.email` with the workspace developers in `config/apex_developers.yaml` and the developers discovered from APEX. This covers short APEX account names (initials-style logins) as well as email-form authors.
When no explicit export format is selected, non-reveal `-recent` requests print only the recent-change report and do not export files. When `-by` or `-my` covers multiple apps, the `APEX APPLICATIONS` list remains complete, but apps with no matching developer changes are skipped in the detailed `CHANGES SINCE` sections below it.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project or output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | all configured schemas in `-reveal`; environment default APEX schema for exports | APEX owner schema(s). Pass multiple times, space-separate (`-schema DA GSN`), use comma lists, or use `%` patterns. In `-reveal`, omitting it scans every schema configured for the environment. |
| `-ws`, `--ws` | No | connection `apex.workspace` | APEX workspace scope. |
| `-group`, `--group` | No | connection `apex.group` | APEX application group scope. |
| `-app`, `--app` | Yes | connection `apex.app` | Application ids to reveal or export. Each value may be a plain id, a closed range `MIN-MAX`, or an open range `MIN+` (no upper bound); combine freely, e.g. `-app 0-99 100-999 5000 9000+`. When any range is given, ADT.ai scans without an id filter and selects matching apps in Python. |
| `-page`, `--page` | Yes | none | Page ids to include in split/readable/embedded component exports. Each value may be a plain id, a closed range `MIN-MAX`, or an open range `MIN+`; comma-separated values are accepted. Requires an explicit component-based export format. Page-filtered exports print affected components and do not update `apex_timers.yaml`. |
| `-component`, `--component` | Yes | none | Shared component filters to include in split/readable/embedded component exports, written as `TYPE:NAME_PATTERN`. `%` and `*` are wildcards, for example `LOV:NAME%`, `LOV:%`, or `LIST:MENU%`. Requires an explicit component-based export format. Component-filtered exports print affected components and do not update `apex_timers.yaml`. |
| `-max_app_id`, `--max_app_id`, `--max-app-id` | No | none | In reveal mode, list only applications with `application_id` below the value; also scopes workspace owner/application counts and per-owner application counts. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | off | Print components changed in the last DAYS days. Bare `-recent` uses the app's stored watermark instead of a day window — "changed since the last export of this app in this format", keyed per environment/app/format in `config/recent.yaml`; an app+format with no watermark yet falls back to a full pull that seeds it. With selected split/readable/embedded formats, also limits output to those components, and each exported format advances its own watermark key. Without an explicit export format, non-reveal `-recent` is report-only and never advances a watermark; with `-reveal`, it filters the application list to apps changed in that window without printing component details. |
| `-by`, `--by` | No | none | Filter the recent component report and recent export set by exact APEX developer username. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-my`, `--my` | No | off | Filter the recent component report and recent export set to the current git user, resolving APEX author aliases from `config/apex_developers.yaml` and discovered workspace developers. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-release`, `--release` | No | none | Override `p_release` values in exported SQL files, matching old ADT upgrade-recovery behavior. |
| `-reveal`, `--reveal` | No | off | Show matching APEX workspaces and applications. |
| `-owners`, `--owners` | No | off | In reveal mode, list application counts for all APEX owners instead of only configured/scanned schemas. |
| `-all`, `--all` | No | off | Export all APEX formats. |
| `-full`, `--full` | No | off | Export full application SQL. |
| `-split`, `--split` | No | off | Export split application source. |
| `-readable`, `--readable` | No | off | Export readable YAML source. |
| `-embedded`, `--embedded` | No | off | Export embedded code report. |
| `-checksum`, `--checksum` | No | off | Export the ID-independent SHA-256 application checksum to `checksum.txt` in the app folder. Whole-app format: `-page`, `-component`, and `-recent` never filter it out, and it never advances a `-recent` watermark. |
| `-rest`, `--rest` | No | off | Export REST services. Runs through SQLcl using a named `ADT_…` connection (auto-registered, wallet included) — see [connection.md](connection.md#named-sqlcl-connections). |
| `-files`, `--files` | No | off | Export application files. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | Export workspace files. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
