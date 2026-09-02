# Export APEX Applications (adtai export_apex)

![No format, no export.](images/export_apex.png)

`export_apex` brings APEX workspaces and applications out of the database and into your repository, so an application change is diffable in git like any other code. It is the APEX counterpart to [`export_db`](export_db.md).

`-reveal` answers "what workspaces and applications live in this environment?". The format flags decide what an export writes, and nothing is exported unless a format was named.

## Examples

See what is there before exporting anything:

```bash
adtai export_apex -reveal
adtai export_apex -reveal -owners -max_app_id 10000
adtai export_apex -ws HUB -group CORE -app 100 200 -reveal
```

Export one application, or a whole range, in the formats you name:

```bash
adtai export_apex -app 100 -full -split
adtai export_apex -app 0-9999 -all
adtai export_apex -app 100+ -apexlang -files
```

Export only some pages or shared components:

```bash
adtai export_apex -app 100 -split -page 1-50 55,56
adtai export_apex -app 100 -split -page 7 -deep
adtai export_apex -app 100 -readable -component LOV:NAME% LIST:MENU%
```

Report what changed recently, by anyone or by you:

```bash
adtai export_apex -recent 3
adtai export_apex -recent 3 -my
```

## Output

`-reveal` prints the connection block, then the workspace inventory once, then the applications of every schema in scope:

```text
APEX DEPLOYMENT TOOL - EXPORT_APEX
----------------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.1.0.0 | FREEPDB1

WORKSPACES:
-----------

  WORKSPACE   WORKSPACE ID   OWNERS   APPS   DEVELOPERS   ACTIVE
  ---------   ------------   ------   ----   ----------   ------
  SANDBOX            90100        1      1            0   Y

APPLICATIONS PER LISTED OWNERS:
-------------------------------

  OWNER     WORKSPACE   APPS
  -------   ---------   ----
  SANDBOX   SANDBOX        1

APEX APPLICATIONS: SANDBOX | SANDBOX
------------------

  APP ID   NAME                                       PAGES   UPDATED AT
  ------   ----------------------------------------   -----   ----------
     100   Order Tracker                                  4

TIMER: 0s
```

- `ACTIVE` marks the workspace your connection file names in `apex.workspace`; one no workspace matches gets a `WARNING - WORKSPACE NOT FOUND:` line instead of an empty table.
- An empty `WORKSPACES:` header means a fresh environment or a `-ws` this instance lacks. Without `-ws`, a workspace the registry withholds is read off the applications, `DEVELOPERS` blank.
- `UPDATED AT` stays blank for an application nobody has edited since it was installed.

An export keeps that overview and puts a block under an `EXPORTING APP <id>/<alias>:` header, one dotted row per action, each countdown seeded from what that action last cost. The banner and connection block above it are the same lines `-reveal` prints:

```text
APEX APPLICATIONS: SANDBOX | SANDBOX
------------------

  APP ID   NAME                                       PAGES   UPDATED AT
  ------   ----------------------------------------   -----   ----------
     100   Order Tracker                                  4

EXPORTING APP 100/ORDERS:
-------------------------

  FULL APP EXPORT  0%                                                  0:00:01
  FULL APP EXPORT  1%                                                  0:00:01
  FULL APP EXPORT .............................................. 100%  0:00:01

  SPLIT COMPONENTS  0%                                                 0:00:01
  SPLIT COMPONENTS ............................................. 100%  0:00:01
```

- Each action row repaints in place. The lines above are those repaints, one per line.
- `-compact` replaces the per-application blocks with **one bar per schema segment**, under `EXPORTING <SCHEMA> APPS:`, keeping the overview above it. The bar is time-weighted rather than action-counted, so a full export and a REST slice each take the share of the bar they really take. It never spans schemas, and neither `-reveal` nor a report-only `-recent` draws one, since neither exports anything.
- The label names the slice in flight, `APP 100 | SPLIT COMPONENTS`, or the action alone for schema-level work that belongs to no application.
- A multi-schema export runs schema by schema, each with its own connection block and `TIMER`, banner printed once.
- Filtered component exports print the affected pages and components line by line instead of the dotted bar.

## Reveal, and how a schema is reached

`-reveal` scans every schema configured in the environment unless `-schema` narrows it, keeping one APEX connection open rather than reconnecting per schema. Names match case-insensitively.

Application group and application id scope come from each schema's own `apex:` block unless the command line overrides them. Workspace scope does too, except under `-reveal`, which lists the whole instance and narrows only to `-ws`.

For a normal export by application id, with no `-schema` and no `-reveal`, ADT.ai first reads the cached `config/internal/apex.db`. When an application's recorded owner differs from the default APEX schema, the run connects straight to that owner and skips the wasted default connection. Several ids that map to different owners each connect to their own.

An application not yet recorded, a missing cache, or an explicit `-schema` or `-reveal` all fall back to the default schema and discover the owner live.

What the cache holds, table by table, is on [storage_apex.md](storage_apex.md).

When `-app` names an application whose owner is not among the requested schemas, that lookup runs once inside the last requested schema's segment, and the owner it finds becomes its own appended segment.

## Formats are explicit

ADT.ai exports only the formats named on the command line. There are no configured format defaults and no suppressor flags: `-all` is how you ask for everything. What each flag writes, how the APEX version decides what is skipped, and why APEXlang carries no static payloads are on [export_apex_formats.md](export_apex_formats.md).

`-page` and `-component` narrow the split, readable and embedded output and the matching page comment YAML. They select no format on their own, so name one. Filtered runs do not update the application cache.

`-deep` beside `-page` also exports the components recorded for those pages in the dependency mirror, LOVs, lists and authorization schemes among them, and prints a `DB OBJECTS` section of the database objects those pages use.

## The application checksum

Every export records the application's checksum in `config/internal/apex.db`, beside the owner, alias and page count already cached there:

```yaml
100:
  owner: APP
  app_alias: DEMO
  app_name: Demo Hub
  pages: 42
  checksum: SH256:lmQxPul9ecXpn+7m/IoFYckC3znD6BnvxnQw0RGnsqk=
```

The value is stored exactly as APEX returns it, algorithm prefix included. It ignores internal component ids, so it moves when the application definition moves and stays stable across imports and environments. It answers "did anything actually change?" without diffing a full export.

It is not a format and there is no flag for it. APEX computes it over the whole application, so `-page`, `-component` and `-recent` never narrow it, and collecting it never advances a watermark. A static file genuinely named `checksum.txt` is left alone, since `-files` owns everything under the static-files folder.

## Where files land

`path_apex` in `config.yaml` is a path template, `'<schema>/apex/'` by default, and the schema token carries its own case, so `<SCHEMA>` writes `APP/apex/`. It resolves that token and nothing else, so any other token is refused before the export writes a folder named after it. `path_objects` is independent, and a project may spell the two differently.

`apex_path_app` names the per-application folder under it and resolves `{$APP_ID}`, `{$APP_ALIAS}`, `{$APP_NAME}` and `{$APP_GROUP}`. Any other token is refused.

An application name or alias is free text, so the value a token resolves to is reduced to a single folder name before it is used. A slash, a backslash, a colon and the other characters a file system reserves each become an underscore.

A run of them becomes one underscore, and a leading or trailing dot or space becomes one too. The separators in the template itself are untouched, so a two-level template still writes two levels.

When a token resolves to nothing at all for an application, the export stops and names that application instead of writing a folder with a level missing.

## Recent changes, by author

`-recent DAYS` prints the components changed in that window. DAYS may be a fraction of a day, `1/24` for the past hour. A whole-day window runs from midnight, so `-recent 1` means changed today, while a shorter one measures back from now. Bare `-recent` uses the application's stored watermark instead, keyed per environment, application and format.

Without an explicit format, a non-reveal `-recent` is report-only: it exports nothing and advances no watermark. With split, readable or embedded selected it also limits the output to those components, and each format advances its own watermark key. With `-reveal` it filters the application list to applications changed in that window, with no per-application detail.

`-by` filters by exact APEX developer username. `-my` compares your `git config user.name` and `user.email` against the workspace developers, which covers short initials-style logins as well as email-form authors. Either one leaves the application list complete and skips applications with no matching change in the detail sections below it. Developer-filtered exports do not update the application cache.

## Schema-level formats on their own

`-rest` and `-files_ws` write under a path carrying no application id, so both belong to the schema rather than to an application, and both run once per schema. That gives a run two console shapes depending on whether a per-application format was selected too, and both are on [export_apex_formats.md](export_apex_formats.md).

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-ws`, `--ws` | No | connection `apex.workspace` | APEX workspace scope. |
| `-group`, `--group` | No | connection `apex.group` | APEX application group scope. |
| `-app`, `--app` | Yes | connection `apex.app` | Application ids to reveal or export. Each value is a plain id, a closed range `MIN-MAX`, or an open range `MIN+`; combine freely. Any range makes the scan run without an id filter and select the matches locally. |
| `-page`, `--page` | Yes | none | Page ids for the split, readable and embedded exports. Plain ids, closed ranges, open ranges, comma-separated values. Requires an explicit component-based format. |
| `-deep`, `--deep` | No | off | Valid only with `-page`. Adds the components recorded for those pages to the export and prints the database objects they use. |
| `-component`, `--component` | Yes | none | Shared component filters as `TYPE:NAME_PATTERN`, with `%` and `*` as wildcards. Requires an explicit component-based format. |
| `-max_app_id`, `--max_app_id`, `--max-app-id` | No | none | In reveal mode, list only applications below this id, and scope the owner and application counts the same way. |
| `-recent [DAYS]`, `--recent [DAYS]` | No | off | Report components changed in the last DAYS days, or since the stored watermark when bare. Report-only without an explicit format. See above. |
| `-by`, `--by` | No | none | Filter the recent report and export set by exact APEX developer username. |
| `-my`, `--my` | No | off | Filter them to the current git user, resolving author aliases from the cache and the discovered workspace developers. |
| `-release`, `--release` | No | none | Override `p_release` values in the exported SQL. |
| `-reveal`, `--reveal` | No | off | Show the matching workspaces and applications, exporting nothing. |
| `-owners`, `--owners` | No | off | In reveal mode, count applications for all APEX owners rather than only the configured schemas. It widens the counts, never the list. |
| `-all`, `--all` | No | off | Export every supported format. |
| `-full`, `--full` | No | off | Export the full application SQL. |
| `-split`, `--split` | No | off | Export split application source. |
| `-readable`, `--readable` | No | off | Export readable YAML. On APEX 26.1+ the format no longer exists, so the slice is skipped silently and writes nothing; use `-apexlang` there. |
| `-embedded`, `--embedded` | No | off | Export the embedded code report. |
| `-apexlang`, `--apexlang`, `-apx`, `--apx` | No | off | Export APEXlang source. Requires APEX 26.1+, and on an older instance the slice is skipped with a note, or silently under `-all`. Whole-app format, never filtered and never advancing a watermark. Static-file payloads are skipped by design. |
| `-rest`, `--rest` | No | off | Export REST services. **Schema-level**, written once per schema, and it runs even when the schema hosts no application. Runs through SQLcl on a named `ADT_…` connection, wallet included. A schema publishing no REST modules exports an empty folder and succeeds; a session that could not connect, or one whose output carries a database error anywhere in it, fails the run with the full SQLcl output attached. An export that stopped before its closing `COMMIT;`, which is what a run cut off at the deadline looks like, fails the same way. A failed export writes no module file at all, including the modules that had already printed cleanly, so the folder is never left holding half a schema. Bounded by `rest_timeout_seconds` (default 60). |
| `-files`, `--files` | No | off | Export the static application files. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | Export the static workspace files. **Schema-level**, exactly like `-rest`. |
| `-compact`, `--compact` | No | off | Replace the per-application blocks and their rows with one time-weighted progress bar per schema segment, keeping the `APEX APPLICATIONS:` overview above it. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
