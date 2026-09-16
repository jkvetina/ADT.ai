# Upload Static Files As You Save Them (adtai live_upload)

`live_upload` watches the folder your APEX static files are exported to and uploads every file you save, straight into the application or the workspace.

Run it in a second terminal while you work on a stylesheet or a script in your own editor. Save the file, switch to the browser, refresh: the change is already there, with no export, no zip and no drag into the builder. It keeps watching until you stop it with Control+C.

With `-once` it does not watch at all: it uploads every file the folder holds, once, and exits. That is the push after a pull, a branch switch or a fresh checkout, when the whole folder is what APEX is missing.

<br>

## Examples

Watch application 100's static files, from your project folder:

```bash
adtai live_upload -app 100
```

Watch the workspace static files instead, which every application in the workspace shares:

```bash
adtai live_upload -app 100 -workspace
```

Watch a folder of your own, on a tree ADT.ai did not export:

```bash
adtai live_upload -app 100 -folder ./src/assets
```

List what the folder already holds before the watch starts, and look less often:

```bash
adtai live_upload -app 100 -show -interval 3
```

Upload everything in application 100's files folder once, and exit:

```bash
adtai live_upload -app 100 -once
```

<br>

## Output

The connection block, the folder, and one row per file as it goes up. One saved stylesheet, so one edit and two rows: the file itself and the minified copy the save produced.

```text
APEX DEPLOYMENT TOOL - LIVE_UPLOAD
----------------------------------

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
APEX DEPLOYMENT TOOL - LIVE_UPLOAD
----------------------------------

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

`-workspace` moves the watch up one level, to the workspace files folder, and changes nothing else.

`-folder` overrides both and takes any path, which is what makes the command usable on a tree ADT.ai did not export. A folder that is not there is named and the run stops rather than watching nothing.

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

The command is a port, and one behaviour is deliberately not carried over. Old ADT sent JavaScript through the CSS minifier and left its own CSS branch unreachable, so `.css` was never minified and `.js` was minified by the wrong tool. Here each suffix goes to its own minifier.

`-schema` names the schema to connect through, as it did, and defaults to the connection's configured APEX schema.

<br>

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-app`, `--app` | No | required | Application to upload into, and the workspace the session binds to. |
| `-workspace`, `--workspace` | No | off | Upload to the workspace static files instead of the application ones. |
| `-folder`, `--folder` | No | the exported static files folder | Folder to watch instead of the exported one. |
| `-interval`, `--interval` | No | `1` | Seconds to wait between passes over the folder. |
| `-show`, `--show` | No | off | List what the folder already holds before the watch starts, or before `-once` uploads it. |
| `-once`, `--once` | No | off | Upload every file in the folder once and exit instead of watching. Refused together with `-interval`. |

Shared options (-root, -env, -schema, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
