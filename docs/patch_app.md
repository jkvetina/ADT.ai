# Shipping an APEX application whole (adtai patch -app)

`-app` ships an APEX application whole instead of as the components that changed, and it is the flag `patch -deploy` imports an APEXlang tree under. Which of the two whole-application formats that means is read off the repository, never off the flag. The import itself is on [patch_import.md](patch_import.md), the round trip around it on apex_round_trip.md.

## Which mode the flag selects

There are two whole-application formats and only one of them is a file a patch can link, so the application's own exported files decide what `-app` does with it:

| Its export in the selected commits | What `-app` ships | What refuses the build |
| --- | --- | --- |
| An `apexlang/` tree ([export_apex_formats.md](export_apex_formats.md)) | The tree, plus that application's other changed files. `patch -deploy -app` imports the folder in place, so the patch links no APEXlang file and carries no copy of one | Nothing on this application. It needs no `f<id>.sql`, so there is none to be stale against |
| An `f<id>.sql` full export | The `f<id>.sql` alone, that application's component files dropped, the export already containing them | An export older than a component committed after it, or missing from the window |

An APEXlang application is therefore built and deployed with no `f<id>.sql` anywhere in the commits and no `-force`. Until the mode was read off the format, `-create -app` demanded a fresh export for every application in the patch.

Committing one then made a `-app <sandbox>` retarget refuse the same patch, for installing the source application in place: two gates whose only common key was `-force`, which also silences the drift check standing beside them.

The flag covers every APEX application the patch touches. A database file is not its business, and neither is an application the flag was given ids for and did not name.

## The optional value is where the tree lands

Bare `-app` changes no application id. `-app <id>` installs the same tree on that id, which is what a sandbox import is: app `1100` under task `123` lands on `1100123`, so the number carries the task and no two developers collide. The alias is derived in the same step, an APEX alias being unique per workspace.

One id per run. Retargeting is a flag on the import, never an edit to `deployments/default.json`, which lets a promote install the byte-identical tree a sandbox import validated. `-fullapp` was the previous name, rejected rather than aliased: the value changed meaning under it.

## The stale export refusal

A full export older than its own components refuses the build, since it cannot hold a change committed after it, and one missing from the window refuses for the same reason. The `PATCH FAILED:` screen names the export's commit, the newer ones, and the `export_apex -full -app <id>` that clears it.

An `apex_files_ignore` match and a static-file payload are never compared, a re-export answering for neither. Nor is an application shipping an APEXlang tree, which has no export for the comparison to be about.
