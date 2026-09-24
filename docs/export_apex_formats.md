# The Export Formats (adtai export_apex)

Which format each flag writes, how the APEX version decides what is skipped, why APEXlang is a whole-application format that carries no static payloads, and what a schema-level format does to the console. The command and its flags are on [export_apex.md](export_apex.md).

<br>

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

<br>

## A full export leaves no stale file behind

`-apexlang` writes LF line endings whatever the database returns and whatever `file_crlf` says, because SQLcl's APEXlang compiler cannot read CRLF.

`-split`, `-embedded`, `-apexlang`, `-files`, `-files_ws` and `-rest` each own a folder, and an unfiltered run of one of them deletes the files in that folder it did not write, as does the page comment export under `comments/`. A page, component, static file or REST module deleted in APEX therefore leaves no orphan in the repository.

`-split` sweeps the `.sql` files under `application/`, `-embedded` the `.sql`, `.js` and `.css` files under `embedded_code/`, `-apexlang` the `.apx`, `.json`, `.sql` and `.rtf` files under `apexlang/`, the page comments the `.yaml` files under `comments/`, and `-rest` the `.sql` files under its folder. `-files` and `-files_ws` sweep every file in theirs, since a static file can have any extension.

Every other export folder is left alone, and so is the `.yaml` a `-readable` export writes under the same `application/` tree.

Apart from the static files, each sweep is limited to the extensions its format writes, so a file of your own kept beside an export, a `NOTES.md` next to the APEXlang tree or inside `embedded_code/`, is never deleted. `diff -restore` deletes by the same rules.

The sweep runs against what the export actually wrote rather than clearing the folder first, so an unchanged file keeps its modification time. A run narrowed by `-page`, `-component` or `-recent` wrote a subset on purpose and never sweeps, except `-apexlang`, which those flags do not narrow.

`-rest` sweeps only once the export reached its closing `COMMIT;`, the one proof that no module is missing. No sweep runs in a folder another export also writes into, such as the application folder that `apex_path_files: ./` names.

On a disk that ignores case, as macOS's does by default, a component renamed only in case keeps its file, renamed to the new spelling rather than deleted.

`-full` and `-readable` write no folder of their own and delete nothing.

`-deep` beside `-page` also exports the components recorded for those pages in the dependency mirror, LOVs, lists and authorization schemes among them, and prints a `DB OBJECTS` section of the database objects those pages use.

Version handling reads the one APEX version the connection block already printed. `-apexlang` on an older instance is skipped and the run continues, so `-all` never fails on a pre-26.1 environment. The skip is announced only when you named the format yourself, and is silent under `-all`.

**APEX 24.1 is the oldest release the export blocks compile on.** Its `APEX_EXPORT.GET_APPLICATION` takes 15 formal parameters; 24.2 added a 16th, `p_with_runtime_instances`. ADT.ai names none of the parameters it does not use, so the same block compiles on 24.1 and on every release after it, and an instance whose version could not be read is not guessed about. A parameter added to a future release is opted into deliberately, behind the version the connection block prints, rather than passed as a default nobody reads.

<br>

## APEXlang is a whole-app format

`-apexlang` writes the folder tree beside `readable/` and `embedded_code/`: `application.apx`, `pages/`, `shared-components/`, `workspace-components/`, `supporting-objects/`, and the deployment and project metadata. Members land verbatim, since `.apx` is compiler input, so none of the SQL-export postprocessing applies.

The folder is swept on every export, so a component deleted in App Builder leaves no stale `.apx`. The sweep covers the `.apx`, `.json`, `.sql` and `.rtf` members the format writes and nothing else, so anything else you keep in that folder stays. `-page`, `-component` and `-recent` never filter it, and an APEXlang run never advances a `-recent` watermark.

**The application's own static files are deliberately left out.** An APEXlang export carries them as binary payloads under `shared-components/static-files/`, and ADT.ai skips those members so the repository never holds two copies, `-files` being the single static-file channel. The metadata that references them is still exported.

**Plugin and theme files are written into the tree**, under each plugin's and each theme's own `static-files/` folder, byte for byte as APEX holds them. `-files` never exports those, so the tree is their only copy, and the import refuses a tree that names one and lacks it. A file APEX no longer holds is swept from those folders like any other member.

The tree is still directly validatable and importable. `-files` hardlinks each payload into the tree's own `shared-components/static-files/` as it writes it, one inode under two names.

The checkout's private `.git/info/exclude` keeps those links out of git without putting an ignore sentinel inside the compiler input tree. An export also removes the retired `.gitignore` descriptor from applications affected by older ADT builds.

So [`validate`](validate.md) and [`patch -deploy -app`](patch_import.md) read the folder on disk rather than a copy of it. Run `-files` alongside `-apexlang`, or `-all`, so the payloads exist to link.

The loop from export to promotion is on apex_round_trip.md.

<br>

## Schema-level formats on their own

`-rest` and `-files_ws` write under a path carrying no application id, so both belong to the schema rather than to an application. That gives a run two shapes, and which one you get depends on whether a per-application format was selected too.

`diff -rest` runs this same `-rest` export on both schemas and compares the text, so what it reports is what an `export_apex -rest` of either schema would write.

**Only schema-level formats selected.** The run exports no application, so it lists none: no `APEX APPLICATIONS:` table, no per-application block, and one bare `EXPORTING:` header over the progress rows in each schema segment. The schema is not repeated there: the connection block three lines above already names it. Nothing per-application runs, so a schema with seventeen applications costs one workspace export rather than seventeen passes. One application is still used, silently and never named, to put the workspace security context in place, and a schema hosting none needs no context at all.

**A per-application format selected too.** The screen is unchanged: the overview, a block per application, and the schema-level row inside the **first** application's block among its other rows, so one row does not cost a section of its own. A schema hosting no application has no block for that row to sit in, and is the one case that prints its own `SCHEMA <name>, EXPORTING:` header.

Either way the slices run once per schema, and both are timed under the workspace slot rather than under whichever application carried the row. A report-only `-recent` exports nothing and so reaches neither shape.
