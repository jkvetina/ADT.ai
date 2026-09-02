# The Export Formats (adtai export_apex)

Which format each flag writes, how the APEX version decides what is skipped, why APEXlang is a whole-application format that carries no static payloads, and what a schema-level format does to the console. The command and its flags are on [export_apex.md](export_apex.md).

## Formats are explicit

ADT.ai exports only the formats named on the command line. There are no configured format defaults and no suppressor flags: `-all` is how you ask for everything.

| Flag | What it writes |
| ---- | -------------- |
| `-full` | The whole application as one SQL file. |
| `-split` | Per-component source files. |
| `-readable` | Readable YAML. Skipped silently on APEX 26.1+, which folded it into APEXlang. |
| `-embedded` | The embedded code report. |
| `-apexlang`, `-apx` | APEXlang `.apx` source under `apexlang/`. Needs APEX 26.1+. |
| `-rest` | REST module definitions. Schema-level. |
| `-files` | Static application files. |
| `-files_ws` | Static workspace files. Schema-level. |

`-page` and `-component` narrow the split, readable and embedded output and the matching page comment YAML. They select no format on their own, so name one. Filtered runs do not update the application cache.

`-deep` beside `-page` also exports the components recorded for those pages in the dependency mirror, LOVs, lists and authorization schemes among them, and prints a `DB OBJECTS` section of the database objects those pages use.

Version handling reads the one APEX version the connection block already printed. `-apexlang` on an older instance is skipped and the run continues, so `-all` never fails on a pre-26.1 environment. The skip is announced only when you named the format yourself, and is silent under `-all`.

## APEXlang is a whole-app format

`-apexlang` writes the folder tree beside `readable/` and `embedded_code/`: `application.apx`, `pages/`, `shared-components/`, `workspace-components/`, and the deployment and project metadata. Members land verbatim, since `.apx` is compiler input, so none of the SQL-export postprocessing applies.

The folder is recreated on every export, so a component deleted in App Builder leaves no stale `.apx`. `-page`, `-component` and `-recent` never filter it, and an APEXlang run never advances a `-recent` watermark.

**Static files are deliberately left out.** An APEXlang export carries the application's static files as binary payloads, and ADT.ai skips those members so the repository never holds two copies, `-files` being the single static-file channel. The metadata that references them is still exported.

That makes `apexlang/` a source and editing surface rather than a directly importable artifact. It does not have to be one: [`validate`](validate.md) and `patch -deploy -app` both assemble the complete application on demand by hardlinking the metadata and the `files/` export into one staging tree. Run `-files` alongside `-apexlang`, or `-all`, so the payloads exist to stage.

The loop from export to promotion is on apex_round_trip.md.

## Schema-level formats on their own

`-rest` and `-files_ws` write under a path carrying no application id, so both belong to the schema rather than to an application. That gives a run two shapes, and which one you get depends on whether a per-application format was selected too.

**Only schema-level formats selected.** The run exports no application, so it lists none: no `APEX APPLICATIONS:` table, no per-application block, and one bare `EXPORTING:` header over the progress rows in each schema segment. The schema is not repeated there: the connection block three lines above already names it. Nothing per-application runs, so a schema with seventeen applications costs one workspace export rather than seventeen passes. One application is still used, silently and never named, to put the workspace security context in place, and a schema hosting none needs no context at all.

**A per-application format selected too.** The screen is unchanged: the overview, a block per application, and the schema-level row inside the **first** application's block among its other rows, so one row does not cost a section of its own. A schema hosting no application has no block for that row to sit in, and is the one case that prints its own `SCHEMA <name>, EXPORTING:` header.

Either way the slices run once per schema, and both are timed under the workspace slot rather than under whichever application carried the row. A report-only `-recent` exports nothing and so reaches neither shape.
