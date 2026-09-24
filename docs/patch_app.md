# Shipping an APEX application whole (adtai patch -app)

`-app` ships an APEX application whole instead of as the components that changed, and it is the flag `patch -deploy` imports an APEXlang tree under. Which of the two whole-application formats that means is read off the repository, never off the flag. The import itself is on [patch_import.md](patch_import.md), the round trip around it on apex_round_trip.md.

<br>

## Which mode the flag selects

There are two whole-application formats and only one of them is a file a patch can link, so the application's own exported files decide what `-app` does with it:

| Its export in the selected commits | What `-app` ships | What refuses the build |
| --- | --- | --- |
| An `apexlang/` tree ([export_apex_formats.md](export_apex_formats.md)) | The tree, plus that application's other changed files and minus its static-file payloads. `patch -deploy -app` imports the folder in place, so the patch links no APEXlang file, snapshots only the ones it changed, and leaves the payloads to the staging that puts them back in the tree | Nothing on this application. It needs no `f<id>.sql`, so there is none to be stale against |
| An `f<id>.sql` full export | The `f<id>.sql` alone, that application's component files dropped, the export already containing them | An export older than a component committed after it, or missing from the window |

An APEXlang application is therefore built and deployed with no `f<id>.sql` anywhere in the commits and no `-force`. Until the mode was read off the format, `-create -app` demanded a fresh export for every application in the patch.

It installs as the owner `export_apex` recorded for it, from the folder the export wrote, so a tree exported through another schema that can see the application still lands in the owner's schema.

The page comments under `comments/` and the code `-embedded` writes under `embedded_code/` are never linked. Both are written for a reader, and neither is SQL the target should run.

The page comments are no part of a patch at all, whatever git says about them: not in the install script's file lists, not snapshotted, not on any screen, and not in hash mode's `CHANGED FILES:`.

The tree is listed as its one `apexlang/` folder, however many of its files changed: under `PROCESSED FILES: <OWNER>.<id>`, under `-deploy`'s `PATCH CONTENTS: <OWNER>.<id>` and under `WARNING - UNCOMMITTED FILES:`. The import reads the folder, so the folder is what the patch ships.

`PATCH CONTENTS:` reads the folder off the `init` half's header, since the application's scripts link none of the tree. That header still names every file.

A tree committed with Windows line endings (CRLF), the way some databases hand an export back, or checked out that way by `core.autocrlf`, is converted to LF in place before the import, under `WARNING - APEXLANG PRECHECK ISSUE:` asking for the commit. It is converted rather than refused because a re-export would throw away an `.apx` edited by hand.

The deploy's skip receipt reads the converted bytes, so the next `-deploy` of the same patch still skips.

Every file a `static-files.apx` names is checked in the tree before anything deploys, the application's own, each plugin's and each theme's. `apex import` compiles before it writes and refuses a tree missing even one, so a missing file refuses the patch there, naming the files and `export_apex -app <id> -apexlang -files`. `-force` does not skip it.

SQLcl's APEXlang compiler cannot read a `\r`. On a whole application it crashed rather than reported: a `NullPointerException`, then an `ORA-01403` from the import block it ran anyway. A failed import leads its `ERROR - DEPLOYMENT FAILED:` stanza with such an exception and leaves the stack in the log.

Committing one then made a `-app <sandbox>` retarget refuse the same patch, for installing the source application in place: two gates whose only common key was `-force`, which also silences the drift check standing beside them.

The flag covers every APEX application the patch touches. A database file is not its business, and neither is an application the flag was given ids for and did not name.

<br>

## The optional value is where the tree lands

Bare `-app` changes no application id. `-app <id>` installs the same tree on that id, which is what a sandbox import is: app `1100` under task `123` lands on `1100123`, so the number carries the task and no two developers collide. The alias is derived in the same step, an APEX alias being unique per workspace.

One id per run. Retargeting is a flag on the import, never an edit to `deployments/default.json`, which lets a promote install the byte-identical tree a sandbox import validated. `-fullapp` was the previous name, rejected rather than aliased: the value changed meaning under it.

<br>

## The stale export refusal

A full export older than its own components refuses the build, since it cannot hold a change committed after it, and one missing from the window refuses for the same reason. The `ERROR - PATCH FAILED:` screen names the export's commit, the newer ones, and the `export_apex -full -app <id>` that clears it.

An `apex_files_ignore` match and a static-file payload are never compared, a re-export answering for neither. Nor is an application shipping an APEXlang tree, which has no export for the comparison to be about.
