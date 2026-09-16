# Which Version Ships (adtai patch -create)

A patch folder holds a copy of every file it installs, and one path can offer four different bodies: what a commit recorded, what the newest commit recorded, what a colleague pushed, and what sits unsaved in your working tree. Which one lands in `snapshots/` is a decision, so it has a flag. The command is on [patch.md](patch.md).

The mode flags never change WHICH files a patch carries. The file list comes from your commit selection; they only choose the body each of those files ships with. The one flag that widens the list, `-files_ws`, is at the end of this page.

<br>

## Examples

The default, the committed body of every selected file:

```bash
adtai patch -target DEV -name 12 -create
```

Ship what is on disk right now, uncommitted edits included:

```bash
adtai patch -target DEV -name 12 -create -local
```

Ship the newest committed body, including a fix a colleague pushed and you have not pulled:

```bash
adtai patch -target DEV -name 12 -create -head
```

Ship no copies at all, and link the repository files where they live:

```bash
adtai patch -target DEV -name 12 -create -nosnap
```

<br>

## The four modes

The three flags are mutually exclusive, and passing two exits `2`. The default has no flag of its own, so there is no spelling of it that parses and changes nothing.

| Mode | What the snapshot holds |
| ---- | ----------------------- |
| default | the file as of its own newest commit inside the patch window |
| `-local` | the working-tree file, uncommitted edits included |
| `-head` | the newest committed version, from the branch or the remote; no newer-commit warning |
| `-nosnap` | nothing; the install script links the repo file where it lives |

<br>

## Why the default is the committed body

A patch you can rebuild tomorrow and get byte-for-byte is what makes a review worth anything, and a working tree is not reproducible: it holds edits no commit records and nobody else can see.

So `-create` reads the git blob at each file's **authoritative commit**, the newest commit in the patch window that touched that path. A file with no commit behind it at all, an `apex_files_copy` entry pulled in from disk, falls back to the working tree and is reported under `UNCOMMITTED FILES` rather than shipped in silence.

A selected committed file still ships when it has been deleted locally without committing. Deletion in the selected history is authoritative too: an uncommitted local restoration does not make the default mode ship that file again. Snapshots, installer links, reports and object guards use the same source decision.

<br>

## What the head mode reads

`-head` answers a different question: what is the newest version of this file anywhere, rather than the version this patch's commits recorded. It runs `git fetch --prune origin` first, before anything reads history, best effort so an offline repository still succeeds on its local refs.

Then, **per file**, it compares the newest commit touching that path on the local `HEAD` against the newest on the remote default branch, `origin/main` or, failing that, `origin/master`, and snapshots whichever of the two is newer.

That is the flag for the case where somebody else fixed an object you are already shipping. Their fix is pushed and you have not pulled it; `-head` puts their body in your snapshot and leaves your file list alone. A file you committed after them keeps yours, and a tie keeps the branch.

Every fallback is silent, and each one is what `-head` did before it read the remote. No origin, neither remote name resolving, or a remote that never carried the file: all read the local `HEAD`. `-head` also suppresses the `^` newer-commit warning, there being nothing newer left to warn about.

<br>

## What no snapshot costs

`-nosnap` writes no copies. The install script's `@` lines point out of the patch folder at the repository file (`@"./../../database/..."`), which costs two things worth knowing before you reach for it.

The folder stops being self-contained, so a later edit to the repository changes what a re-deploy installs. And the two transforms a snapshot carries cannot happen, because there is no copy to apply them to: the `patch_force_views` rewrite, and the audit columns stamped into an APEX page.

An APEX static file is the one exception and is written in every mode. It has no runnable form in the repository, what deploys being a generated `wwv_flow_imp` wrapper, so there is nothing for a link to point at.

<br>

## An APEXlang file is always deployed where it lives

An `.apx` file is the mirror image of that exception: it is snapshotted like any other changed file, and it is the one kind the install script never links. `patch -deploy -app` imports the application by pointing `apex import` at its own `apexlang/` folder, so the copy is a record rather than an install step.

Only the files the patch's own commits touched are copied, never the rest of the tree, which on a large application is the whole cost of changing one page.

Its static files go the same way, which makes them the exception to the exception above. `-apexlang` writes the tree without the payloads, and the deploy stages the sibling `files/` export back into `shared-components/static-files/` before `apex import` reads the folder, so each file arrives with the application.

A wrapper generated for one of them would therefore install it twice, and an APEXlang application's payloads are left out of the patch entirely. An application exported as `f<id>.sql` has no tree to stage them out of and keeps its wrappers.

Nothing from the legacy `.sql` import path is written beside them. `application/set_environment.sql` and `application/end_environment.sql` are the begin/end scaffolding that path runs, so an `apex_files_copy` entry naming either is skipped for an application shipping a tree, and the install script inlines neither.

The file stays in the patch's file list, so the install script and the processing report still name it. What the script carries in place of an `@` line is the folder the application deploys from:

```text
PROMPT --;
PROMPT -- APEXLANG SOURCE: apex/100_DEMO/apexlang
PROMPT -- imported from that folder by patch -deploy -app, not from this patch
PROMPT --;
```

The import's own deploy log repeats it as a `DEPLOYED FROM` row beside the three signature rows, which is where to look when you need to know which bytes landed. The `.sql` component exports beside the tree are unaffected and link as they always have.

<br>

## Hash mode picks for you

`-hash` builds a patch from what the working tree no longer agrees with the target about, so what was compared has to be what ships. It forces the `local` mode, and `-head` or `-nosnap` beside it exit `2`. Hash mode itself is on [patch_hash.md](patch_hash.md).

<br>

## Workspace static files

A patch carries the workspace static files its commits changed, like any other file. `-files_ws` beside `-create` carries every file under `<path_apex>/<apex_workspace_dir>/<apex_path_files>/`, touched or not:

```bash
adtai patch -target DEV -name 12 -create -files_ws
```

Which files count as "every" follows the mode, so the list and the bodies agree. The default reads the tree of the newest selected commit, so an uncommitted edit or an untracked file never ships. `-head` reads `HEAD`, and `-local` and `-nosnap` read the working tree.

Application static files are never widened: ship the changed ones, or the whole application with `-app`. A run that builds nothing exits `2`, and so does a `-create -deploy` whose folder is already built.

Each snapshot is one self-contained PL/SQL block. Base64 rows go into a temporary CLOB, `apex_web_service.clobbase642blob` decodes it, and `create_workspace_static_file` stores it. `-deploy` runs that same script through SQLcl, so a DBA or a pipeline running it by hand gets the same result.

The stored name is the path below the static-files folder, so `files/css/app.css` installs as `css/app.css`. That is the name `export_apex -files_ws` reads back and the drift guard compares.
