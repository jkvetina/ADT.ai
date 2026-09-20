# Upload Static Files As You Save Them (adtai patch -upload)

`patch -upload` watches the folder your APEX static files are exported to and uploads every file you save, straight into the application or the workspace.

Run it in a second terminal while you work on a stylesheet or a script in your own editor. Save the file, switch to the browser, refresh: the change is already there, with no export, no zip and no drag into the builder. It keeps watching until you stop it with Control+C.

With `-once` it does not watch at all: it uploads every file the folder holds, once, and exits. That is the push after a pull, a branch switch or a fresh checkout, when the whole folder is what APEX is missing.

It is a `patch` verb because it is the smallest thing `patch` does: every other verb ships a release to an environment, and this one ships a single saved file. It reads no commit and names no patch folder, so it is refused beside `-create`, `-deploy`, `-install`, `-archive`, `-drop`, `-hash`, `-baseline` and `-name`.

<br>

## Examples

Watch application 100's static files, from your project folder:

```bash
adtai patch -upload -app 100
```

Watch the workspace static files instead, which every application in the workspace shares:

```bash
adtai patch -upload -app 100 -files_ws
```

Watch a folder of your own, on a tree ADT.ai did not export:

```bash
adtai patch -upload -app 100 -folder ./src/assets
```

List what the folder already holds before the watch starts, and look less often:

```bash
adtai patch -upload -app 100 -show -interval 3
```

Upload everything in application 100's files folder once, and exit:

```bash
adtai patch -upload -app 100 -once
```

<br>

## Output

The connection block, the folder, and one row per file as it goes up. One saved stylesheet, so one edit and two rows: the file itself and the minified copy the save produced.

```text
APEX DEPLOYMENT TOOL - PATCH
----------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1


MONITORING FOLDER:
------------------
  sandbox/apex/901_ORDERS/files
  press Control+C to quit
  css/app.css ............................................................. OK
  css/app.min.css ......................................................... OK
  UPLOADED ................................................................. 2


TIMER: 12s
```

- The watch uploads nothing already in the folder when it starts. It ships what you save while it runs, so a first pass over the whole folder would overwrite APEX with whatever the repository happened to hold. When that is what you want, run `-once` instead.
- A file counts as changed when its timestamp moves forward. Restoring an older copy therefore does not push the stale file over the newer one already in APEX.
- `UPLOADED` counts the files this session sent, and prints once you stop the watch.
- A file that fails to upload, or fails to minify, ends its row `UPLOAD FAILED` or `MINIFY FAILED` instead of `OK`. The watch keeps going and picks up the next saved file.

With `-once`, every file in the folder gets a row in path order, subfolders included, and the count closes the run. Here the folder held a script, a stylesheet in `css/` and that stylesheet's minified copy:

```text
APEX DEPLOYMENT TOOL - PATCH
----------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.3.0.0 | FREEPDB1


MONITORING FOLDER:
------------------
  sandbox/apex/100_ORDERS/files
  adt_fixture_once.js ..................................................... OK
  css/adt_fixture_once.css ................................................ OK
  css/adt_fixture_once.min.css ............................................ OK
  UPLOADED ................................................................. 3


TIMER: 0s
```

- A failed file ends its row `UPLOAD FAILED` and the rest still go up. The run then exits 1 instead of 0.
- An empty folder uploads nothing and closes on `UPLOADED` with 0.

<br>

## Which folder it watches

Without `-folder`, the folder is the one `export_apex -files` and `-files_ws` write into, resolved from the project's own `path_apex`, `apex_path_app` and `apex_path_files` settings. The two cannot disagree about where static files live, so what you edit is what the next export reads back.

`-files_ws` moves the watch up one level, to the workspace files folder, and changes nothing else. It is the same flag `-create` uses to widen what a build carries, which is deliberate: one command says "workspace static files" one way. Outside upload mode it still applies only to `-create`.

`-folder` overrides both and takes any path, which is what makes the verb usable on a tree ADT.ai did not export. A folder that is not there is named and the run stops rather than watching nothing.

<br>

## Which application, and through which schema

`-app` names the one application to upload into, and it is required. It is `patch`'s own flag: elsewhere the value is the id an APEXlang tree lands on, here it is the application a save lands in, and both answer where to write.

One id per run, in both modes. A second is refused rather than reduced to the first.

The id is required even with `-files_ws`. The workspace is never named on the command line: it is the one owning this application, and the session has to be bound to it before APEX will write anything.

`-schema` names the schema to connect through, and defaults to the connection's configured APEX schema. It is `-install`'s repeatable flag, so it parses as a list; upload mode watches one folder through one connection, so a second value is refused rather than quietly dropped. `-target` names the environment.

<br>

## Minification

A saved `.css` or `.js` file is minified beside itself, as `app.css` and `app.min.css`. The minified copy is a new file in the watched folder, so the next pass uploads it the way it uploads anything else you save, and both copies reach APEX from one edit. A name already carrying `.min.` is uploaded and never minified again.

Minification needs `rcssmin` and `rjsmin`, which ADT.ai does not install:

```bash
pip install rcssmin rjsmin
```

Without them the command says `WARNING - MINIFIERS NOT INSTALLED:` once at the start and uploads without minifying.

`-once` never minifies, and so never asks for either package. It pushes the folder exactly as it is on disk and writes nothing into the repository. A file already named `.min.` is uploaded like any other.

<br>

## Differences from old ADT

The behaviour is a port of old ADT's `live_upload`, and one thing is deliberately not carried over. Old ADT sent JavaScript through the CSS minifier and left its own CSS branch unreachable, so `.css` was never minified and `.js` was minified by the wrong tool. Here each suffix goes to its own minifier.

ADT.ai shipped it as a command of its own, `adtai live_upload`, until it folded into `patch` as the verb above. The old spelling stops parsing; there is no fallback.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-upload`, `--upload` | No | off | Upload the application's static files into APEX as you save them, or the whole folder once with `-once`. |
| `-app [ID]`, `--app [ID]` | No | required here | Application to upload into, and the workspace the session binds to. One id per run. |
| `-files_ws`, `--files_ws`, `--files-ws` | No | off | Upload to the workspace static files instead of the application ones. |
| `-folder`, `--folder` | No | the exported static files folder | Folder to watch instead of the exported one. |
| `-interval`, `--interval` | No | `1` | Seconds to wait between passes over the folder. |
| `-show`, `--show` | No | off | List what the folder already holds before the watch starts, or before `-once` uploads it. |
| `-once`, `--once` | No | off | Upload every file in the folder once and exit instead of watching. Refused together with `-interval`. |
| `-target`, `--target` | No | connection file default environment | Environment to upload into. |

`-schema` and the other shared options (-root, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments). Every other `patch` argument is on [patch.md](patch.md), and refused beside `-upload` where it names a patch the verb has none of.
