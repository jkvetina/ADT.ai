# Export APEX Applications (adtai export_apex)

Reveal APEX workspaces and applications from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_apex -reveal
```

The initial `export_apex` slice discovers applications only. In `-reveal` mode, it lists the workspace inventory once at the top, then lists applications for every schema configured in the selected connection environment unless `-schema` narrows the scan. Reveal keeps one APEX connection open and switches APEX workspace/security context for each schema's configured workspace before querying that schema's APEX applications. For normal exports, it uses the APEX schema default from the connection file. Workspace, application group, and application id scope still come from each connection schema's `apex:` section unless overridden on the command line.

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
adtai export_apex -app 100 -page 7
adtai export_apex -app 100 -split -component LOV:NAME% LIST:MENU%
adtai export_apex -app 100 -component LOV:%
```

`-page` and `-component` filter split/readable/embedded collection output and matching page comment YAML. When either filter is passed without an explicit export format, ADT.ai assumes `-split`. Filtered component exports print the affected pages/components line by line instead of the dotted progress bar, and filtered runs do not update `config/apex_timers.yaml`. Full app SQL, REST services, application files, and workspace files are not component-filtered.

Show recent changes by one developer, or by the current git user, without exporting:

```bash
adtai export_apex -recent 3 -by JANE.DEV
adtai export_apex -recent 3 -my
```

`-my` compares `git config user.name` and `git config user.email` with the workspace developers in `config/apex_developers.yaml` and the developers discovered from APEX. This covers APEX author names such as `JANK` as well as email-form authors.
When `-by` or `-my` covers multiple apps, the `APEX APPLICATIONS` list remains complete, but apps with no matching developer changes are skipped in the detailed `CHANGES SINCE` sections below it.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project or output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | all configured schemas in `-reveal`; environment default APEX schema for exports | APEX owner schema. In `-reveal`, omitting it scans every schema configured for the environment. |
| `-ws`, `--ws` | No | connection `apex.workspace` | APEX workspace scope. |
| `-group`, `--group` | No | connection `apex.group` | APEX application group scope. |
| `-app`, `--app` | Yes | connection `apex.app` | Application ids to reveal or export. Each value may be a plain id, a closed range `MIN-MAX`, or an open range `MIN+` (no upper bound); combine freely, e.g. `-app 0-99 100-999 5000 9000+`. When any range is given, ADT.ai scans without an id filter and selects matching apps in Python. |
| `-page`, `--page` | Yes | none | Page ids to include in split/readable/embedded component exports. Each value may be a plain id, a closed range `MIN-MAX`, or an open range `MIN+`; comma-separated values are accepted. If no export format is selected, `-page` defaults the export to `-split`. Page-filtered exports print affected components and do not update `apex_timers.yaml`. |
| `-component`, `--component` | Yes | none | Shared component filters to include in split/readable/embedded component exports, written as `TYPE:NAME_PATTERN`. `%` and `*` are wildcards, for example `LOV:NAME%`, `LOV:%`, or `LIST:MENU%`. If no export format is selected, `-component` defaults the export to `-split`. Component-filtered exports print affected components and do not update `apex_timers.yaml`. |
| `-max_app_id`, `--max_app_id`, `--max-app-id` | No | none | In reveal mode, list only applications with `application_id` below the value; also scopes workspace owner/application counts and per-owner application counts. |
| `-recent`, `--recent` | No | off | On exports, print components changed in the last DAYS days before the selected formats and limit split/readable/embedded output to those components; with `-by` or `-my`, can run report-only without an export format; with `-reveal`, filter the application list to apps changed in that window without printing component details. |
| `-by`, `--by` | No | none | Filter the recent component report and recent export set by exact APEX developer username. With `-recent`, does not require an export format. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-my`, `--my` | No | off | Filter the recent component report and recent export set to the current git user, resolving APEX author aliases from `config/apex_developers.yaml` and discovered workspace developers. With `-recent`, does not require an export format. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-release`, `--release` | No | none | Override `p_release` values in exported SQL files, matching old ADT upgrade-recovery behavior. |
| `-reveal`, `--reveal` | No | off | Show matching APEX workspaces and applications. |
| `-owners`, `--owners` | No | off | In reveal mode, list application counts for all APEX owners instead of only configured/scanned schemas. |
| `-all`, `--all` | No | off | Export all APEX formats. |
| `-full`, `--full` | No | off | Export full application SQL. |
| `-split`, `--split` | No | off | Export split application source. |
| `-readable`, `--readable` | No | off | Export readable YAML source. |
| `-embedded`, `--embedded` | No | off | Export embedded code report. |
| `-rest`, `--rest` | No | off | Export REST services. |
| `-files`, `--files` | No | off | Export application files. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | Export workspace files. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
