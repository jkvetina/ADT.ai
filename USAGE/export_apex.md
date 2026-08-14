# Export APEX Applications (adtai export_apex)

`export_apex` discovers and exports Oracle APEX workspaces and applications into your project folder, the APEX counterpart to `export_db`. `-reveal` answers "what workspaces, groups, and apps live in this environment?"; the format flags (`-full`, `-split`, `-readable`, `-rest`, `-files`, `-files_ws`, `-embedded`, `-apexlang`, `-checksum`) then export an application as a single SQL file, split per-component files, readable YAML, APEXlang `.apx` source, REST module definitions, static application/workspace files, or the application checksum, so app changes are diffable in Git like any other code.

Reveal APEX workspaces and applications from the current folder:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai export_apex -reveal
```

The initial `export_apex` slice discovers applications only. In `-reveal` mode, it lists the workspace inventory once at the top, then lists applications for every schema configured in the selected connection environment unless `-schema` narrows the scan. Reveal keeps one APEX connection open and switches APEX workspace/security context for each schema's configured workspace before querying that schema's APEX applications. For normal exports, it uses the APEX schema default from the connection file. Workspace, application group, and application id scope still come from each connection schema's `apex:` section unless overridden on the command line.

When you export by application id (`-app <id>`, without `-schema`/`-reveal`), `export_apex` first reads the cached `config/internal/apex_apps.yaml` (written by earlier exports). If an app's recorded `owner` schema differs from the default APEX schema, it connects straight to that owner schema and skips both the wasted default-schema connection and the live owner-discovery round-trip. Multiple `-app` ids that map to different owners each connect to their own owner schema. Apps not yet recorded in the file, a missing file, or `-schema`/`-reveal` fall back to connecting to the default schema and discovering the owner live.

A multi-schema export (`-schema DA GSN -full`, outside `-reveal`) executes schema by schema: connect to DA, discover and export its applications, print its own `TIMER`, then connect to GSN and repeat, exactly as if you had run the command once per schema, with the banner printed only once. If `-app <id>` names an app whose owner is not among the requested schemas, the owner lookup runs once, inside the last requested schema's own segment, and the owner schema it finds becomes its own appended segment (connection block, `APEX APPLICATIONS:` table, export, timer) rather than being folded into an already-printed segment. `-reveal` is unaffected, it stays a single connection with one shared inventory screen. See `USAGE.md` §Console Output Contract for the full multi-schema shape.

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
adtai export_apex -app 100 -split -page 7 -deep
adtai export_apex -app 100 -split -component LOV:NAME% LIST:MENU%
adtai export_apex -app 100 -readable -component LOV:%
```

`-page` and `-component` filter split/readable/embedded collection output and matching page comment YAML. They do not select an export format on their own; pass `-split`, `-readable`, or `-embedded` explicitly. Add `-deep` to `-page` to also export components recorded for the selected pages in `config/internal/dependencies.db`, such as LOVs, lists, and authorization schemes, and to print a `DB OBJECTS` section with database objects used by the selected pages. Filtered component exports print the affected pages/components line by line instead of the dotted progress bar, and filtered runs do not update `config/internal/apex_timers.yaml`. Full app SQL, REST services, application files, workspace files, and the application checksum are not component-filtered.

Export APEXlang source, the `.apx` format APEX 26.1 introduced:

```bash
adtai export_apex -app 100 -apexlang
adtai export_apex -app 100 -apx
```

`-apexlang` (short alias `-apx`) writes the APEXlang folder tree into `apexlang/` in the application folder, beside `readable/` and `embedded_code/`: `application.apx`, `pages/pNNNNN-<alias>.apx`, `shared-components/…`, `workspace-components/…`, `deployments/default.json`, and `.apex/apexlang.json`. Members land verbatim, `.apx` is compiler input, so ADT.ai applies none of the SQL-export postprocessing (no component-ID enrichment, no `p_default_id_offset` rewrite, no `-release` override).

The folder is recreated on every export, so a component deleted in APEX leaves no stale `.apx` behind. APEXlang is a whole-app format in this version: `-page`, `-component`, and `-recent` never filter it, and an APEXlang run never advances a `-recent` watermark.

**Static files are deliberately not included.** An APEXlang export carries the app's static files as binary payloads under `shared-components/static-files/`; ADT.ai skips those members so the repo never holds two copies of the same file, `-files` remains the single static-file channel. The `shared-components/static-files.apx` metadata that references them is still exported. That makes `apexlang/` a source and review surface rather than a directly importable artifact; deployment stays on full-app SQL exports.

That last sentence has a measured mechanism behind it (`#163`). Validating the same real application twice, once complete, once with only the payload folder removed, the complete tree passes and the stripped one fails with one `REFERENCE_NOT_FOUND` per referenced file, because `static-files.apx` names each payload in a `fileName` property. Since `apex import` validates first and proceeds only on a clean run, the committed `apexlang/` folder cannot be imported on its own.

**It does not have to be.** `validate` assembles the complete application on demand instead (`#165`): `files/<X>` maps 1:1 onto `shared-components/static-files/<X>`, so a staging tree under the gitignored `config/temp/apexlang/` **hardlinks** the metadata and the payloads together, one inode per file, no bytes copied, nothing added to git. The export stays single-copy and the compiler still sees a whole application. That is also why exporting the payloads a second time was rejected rather than deferred: the same staged tree is what a future import command should be handed.

**Check it with [`adtai validate`](validate.md).** The point of APEXlang is that a compiler can tell you whether the tree is valid, and `validate` is that check, connectionless, so `adtai export_apex -app 100 -apexlang && adtai validate -app 100` is a complete export-and-verify gate. Run `-files` alongside `-apexlang` (or use `-all`) so the payloads exist to stage; without them the gate reports one `REFERENCE_NOT_FOUND` per referenced file plus a `NOTES:` row naming the command that fixes it.

Version handling is one-way in both directions. `-apexlang` needs APEX 26.1+; on an older instance the slice is skipped and the run continues, so `-all` never fails on a pre-26.1 environment. The skip is *announced* only when you named the format yourself, `-apexlang` and `-apx` print `APEXLANG EXPORT SKIPPED, NEEDS APEX 26.1`, and under `-all` it is silent, because the note answers a question `-all` never asked. Conversely, APEX 26.1 folded `READABLE_YAML` into APEXlang, so on 26.1+ a requested `-readable` slice is skipped silently and writes nothing, a "readable" export there would just be APEXlang content in the wrong folder. Pre-26.1 `-readable` is unchanged.

Export the application checksum, an ID-independent SHA-256 fingerprint of the whole app:

```bash
adtai export_apex -app 100 -checksum
```

`-checksum` writes one line to `checksum.txt` in the application folder, beside `f100.sql`. The fingerprint ignores internal component ids, so it changes when the application definition changes and stays stable across imports and environments. That makes it a cheap deploy gate: export the checksum and let Git answer "did anything actually change?" without diffing a full export.

```bash
adtai export_apex -app 100 -checksum
git diff --exit-code apex/100_CORE23/checksum.txt || echo "application changed"
```

Because a fingerprint covers the whole application, `-page`, `-component`, and `-recent` never filter it out, and a `-checksum` run never advances a `-recent` watermark, the fingerprint reports *that* the app changed, never which components were exported.

Export recent changes by one developer, or by the current git user:

```bash
adtai export_apex -recent 3
adtai export_apex -recent 3 -by JANE.DEV
adtai export_apex -recent 3 -my
```

`-my` compares `git config user.name` and `git config user.email` with the workspace developers in `config/internal/apex_developers.yaml` and the developers discovered from APEX. This covers short APEX account names (initials-style logins) as well as email-form authors.
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
| `-deep`, `--deep` | No | off | Modifier valid only with `-page`. Reads `config/internal/dependencies.db`, adds exportable components recorded for the selected pages to the page-scoped split/readable/embedded export, and prints database objects used by those pages. |
| `-component`, `--component` | Yes | none | Shared component filters to include in split/readable/embedded component exports, written as `TYPE:NAME_PATTERN`. `%` and `*` are wildcards, for example `LOV:NAME%`, `LOV:%`, or `LIST:MENU%`. Requires an explicit component-based export format. Component-filtered exports print affected components and do not update `apex_timers.yaml`. |
| `-max_app_id`, `--max_app_id`, `--max-app-id` | No | none | In reveal mode, list only applications with `application_id` below the value; also scopes workspace owner/application counts and per-owner application counts. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | off | Print components changed in the last DAYS days. Bare `-recent` uses the app's stored watermark instead of a day window, "changed since the last export of this app in this format", keyed per environment/app/format in `config/internal/recent.yaml`; an app+format with no watermark yet falls back to a full pull that seeds it. With selected split/readable/embedded formats, also limits output to those components, and each exported format advances its own watermark key. Without an explicit export format, non-reveal `-recent` is report-only and never advances a watermark; with `-reveal`, it filters the application list to apps changed in that window without printing component details. |
| `-by`, `--by` | No | none | Filter the recent component report and recent export set by exact APEX developer username. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-my`, `--my` | No | off | Filter the recent component report and recent export set to the current git user, resolving APEX author aliases from `config/internal/apex_developers.yaml` and discovered workspace developers. Developer-filtered exports do not update `apex_timers.yaml`. |
| `-release`, `--release` | No | none | Override `p_release` values in exported SQL files, matching old ADT upgrade-recovery behavior. |
| `-reveal`, `--reveal` | No | off | Show matching APEX workspaces and applications. |
| `-owners`, `--owners` | No | off | In reveal mode, list application counts for all APEX owners instead of only configured/scanned schemas. |
| `-all`, `--all` | No | off | Export all APEX formats. |
| `-full`, `--full` | No | off | Export full application SQL. |
| `-split`, `--split` | No | off | Export split application source. |
| `-readable`, `--readable` | No | off | Export readable YAML source. On APEX 26.1+ this format no longer exists, APEX folded `READABLE_YAML` into APEXlang, so the slice is skipped silently and writes nothing; use `-apexlang` there. |
| `-embedded`, `--embedded` | No | off | Export embedded code report. |
| `-apexlang`, `--apexlang`, `-apx`, `--apx` | No | off | Export APEXlang (`.apx`) source into `apexlang/` in the app folder. Requires APEX 26.1+; on an older instance the slice prints `APEXLANG EXPORT SKIPPED, NEEDS APEX 26.1`, no dotted leader, since nothing ran, and the rest of the run continues (the release the instance is on is not repeated; the connection block above already prints it). That line appears **only when the format was named by flag**: under `-all` the skip is silent, because it answers a question only `-apexlang`/`-apx` asked. Whole-app format: `-page`, `-component`, and `-recent` never filter it, and it never advances a `-recent` watermark. Static-file payloads are skipped by design, `-files` stays the single static-file channel. |
| `-checksum`, `--checksum` | No | off | Export the ID-independent SHA-256 application checksum to `checksum.txt` in the app folder. Whole-app format: `-page`, `-component`, and `-recent` never filter it out, and it never advances a `-recent` watermark. |
| `-rest`, `--rest` | No | off | Export REST services. **Schema-level, not per-application**: it writes `apex/workspace/rest/` **once per schema**, on that schema's first application and listed among its other export rows, and runs even when the schema hosts no APEX application at all, that last case is the one that still prints a `SCHEMA <name>, EXPORTING:` header, there being no application block for the row to sit under. Runs through SQLcl using a named `ADT_…` connection (auto-registered, wallet included), see [connection.md](connection.md#named-sqlcl-connections). A schema that publishes no REST modules exports an empty folder and succeeds; a session that could not connect, or a `rest export` reporting an `ORA-`/`SP2-`/`PLS-` error, fails the run with the **full SQLcl output** attached, no `-debug` rerun needed to see the cause. Bounded by `rest_timeout_seconds` (default 60); past it SQLcl is killed and the run reports the timeout. |
| `-files`, `--files` | No | off | Export application files. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | Export workspace files. **Schema-level, not per-application**, exactly like `-rest`: `apex/workspace/files/` carries no app id, so it exports once per schema on that schema's first application, and once for a schema hosting no application at all. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
