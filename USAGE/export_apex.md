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

Choose export sections explicitly:

```bash
adtai export_apex -full -split -readable -rest -files
```

ADT.ai exports only the sections named on the command line. Use `-all` to export every supported format explicitly. ADT.ai does not read configured format defaults and does not support old ADT's `-only` or `-no...` format suppressor flags.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project or output root folder. This can be any ordinary folder and does not need to be a Git repository. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes | all configured schemas in `-reveal`; environment default APEX schema for exports | APEX owner schema. In `-reveal`, omitting it scans every schema configured for the environment. |
| `-ws`, `--ws` | No | connection `apex.workspace` | APEX workspace scope. |
| `-group`, `--group` | No | connection `apex.group` | APEX application group scope. |
| `-app`, `--app` | Yes | connection `apex.app` | Application id or ids to reveal. |
| `-max_app_id`, `--max_app_id`, `--max-app-id` | No | none | In reveal mode, list only applications with `application_id` below the value; also scopes workspace owner/application counts and per-owner application counts. |
| `-recent`, `--recent` | No | off | On exports, print components changed in the last DAYS days before the selected formats; with `-reveal`, filter the application list to apps changed in that window without printing component details. |
| `-by`, `--by` | No | none | Filter the recent component report by exact APEX developer username. |
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
| `-beep`, `--beep` | No | off | Force the completion chime on for this run, even from a worktree checkout. |

---

← [USAGE.md](../USAGE.md) index
