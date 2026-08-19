# Validate APEXlang Source (adtai validate)

`validate` runs the APEXlang compiler over an exported `apexlang/` folder and reports what it finds as an ADT error table with a non-zero exit code. It is the closing step of the APEXlang loop that `export_apex -apexlang` opens: export `.apx` source, edit it (by hand or with an agent), validate, fix, repeat, and import only on a clean run.

**The command never connects.** The APEXlang compiler ships inside SQLcl and answers on a bare `sql -S /nolog` session, so `validate` takes no `-env`, no `-schema`, and no credentials, and it works in CI and from any checkout. The only requirement is a SQLcl new enough to carry the compiler (26.1+, the same release that introduced APEXlang).

Validate every exported application in the project:

```bash
cd ~/Dropbox/PROJECTS/www.jankvetina.cz
adtai validate
```

Validate one application by id, resolved offline through `config/internal/apex.db`, with no database round-trip:

```bash
adtai validate -app 800
```

Validate several applications in one run:

```bash
adtai validate -app 800 808
```

Validate an explicit folder or a zip, from anywhere, with no ADT project around it:

```bash
adtai validate -input ~/exports/f800/apexlang
adtai validate -input ~/exports/f800.zip
```

## Exit Code Is the Deliverable

`validate` is a gate. Exit `0` means every requested folder validated clean; anything else is non-zero, so CI and agents can branch on it directly:

```bash
adtai export_apex -app 800 -apexlang && adtai validate -app 800
```

Non-zero covers more than compiler errors, deliberately, a run that checked nothing is not a pass:

| Outcome | Meaning | Exit |
| ------- | ------- | ---- |
| `OK` | The compiler validated the folder with no errors. | `0` |
| `OK (n warnings)` | Clean, but the compiler raised warnings, listed in a `WARNINGS:` section. | `0` |
| *a number* | That many compiler errors; each one is a row in the folder's `ERRORS:` table. | non-zero |
| `EMPTY` | The folder exists but holds no APEXlang files, a broken export, not a quiet success. | non-zero |
| `NOT_FOUND` | SQLcl could not find the input path. | non-zero |
| `UNRECOGNISED` | SQLcl printed something this version cannot read. The raw output is shown verbatim. | non-zero |
| a `NOTES:` row | An `-app` with no export on disk, or a bare run with no `apexlang/` folder anywhere. | non-zero |

`UNRECOGNISED` exists because SQLcl exits `0` whatever the compiler says, which makes the printed text the only signal. If a future SQLcl changes its wording, the run fails loudly and shows what it actually said, rather than reporting a pass on output nobody parsed.

**Warnings do not fail the run**, the compiler's own verdict is still `Validation successful.`, but they are never hidden. A folder with warnings reports `OK (n warnings)` and prints a `WARNINGS:` section in the same shape as the error list. This matters most for `FILE_IGNORED`, which means the compiler did not check that file at all: a bare `OK` there would be a pass over work that never happened.

## Static-File Payloads Are Staged In, Not Duplicated

An `export_apex -apexlang` tree deliberately omits the `shared-components/static-files/` payloads so the repo never holds two copies of every static file (`-files` is the single static-file channel). The compiler does not accept that: `shared-components/static-files.apx` references each payload by `fileName`, and every missing one is a `REFERENCE_NOT_FOUND` error. On a real application that is eight errors before you have looked at anything that matters.

So `validate` assembles a complete tree instead of validating an incomplete one. For every `-app` and discovered target it builds a staging tree under the gitignored `config/temp/apexlang/<app-folder>/`, **hardlinking** the `apexlang/` metadata and then hardlinking the sibling `files/` export into place under `shared-components/static-files/`. The compiler is pointed at that tree.

A hardlink is one inode with two names, so this copies no bytes and adds nothing to git, the repo still holds exactly one copy of every static file. Measured on the real app 800: unstaged, nine errors; staged, one (a genuine `MISSING_PROPERTY_VALUE` in the app itself), with the staged payloads sharing inodes with `files/`.

- **`files/<X>` maps 1:1 onto `shared-components/static-files/<X>`**, same relative paths, no rename, no transform. That is what makes staging a link operation rather than a translation.
- **Hardlinks, not symlinks.** A hardlink is indistinguishable from a regular file to any directory walker; Java's `Files.walk` will not descend a symlinked directory without `FOLLOW_LINKS`, so a symlinked tree risks the compiler silently skipping whole folders. A copy is the fallback when a filesystem refuses to link.
- **The staging tree is rebuilt per run**, mirroring the `apexlang/` folder's own contract, so a component deleted in App Builder cannot survive as a stale `.apx`.
- **A payload that was never exported stays missing.** It is never touched into existence, see below.
- **`-input` is never staged.** That mode reads no project config by contract and may point at a zip or a single `.apx`, so it validates exactly what you gave it. Use it to see the raw committed tree.

An app with no `files/` export gets a `NOTES:` row naming `export_apex -files`, because eight `REFERENCE_NOT_FOUND` messages say what is missing but not how to get it.

### Why An Empty Placeholder Would Be Worse Than A Missing File

The compiler checks that a referenced path **exists**, not what is in it. Measured on 2026-07-27 against one tree in three variants: real payload bytes and payloads truncated to *zero bytes* validate identically, while deleting one file produces its `REFERENCE_NOT_FOUND`.

That makes `touch`-ing a placeholder the single most dangerous shortcut available here, it would turn the gate green and then import an application with broken images. Staging therefore links only payloads that genuinely exist and lets the compiler report every real gap.

This is also a live hazard in existing repos: a tree whose `shared-components/static-files/` holds zero-byte files validates clean today and would import an app with no icons. If a payload folder is suspiciously all-zero, it is not a valid export.

## Output

One streamed row per folder, the label appears before the compile starts, the result after it finishes, then one `ERRORS:` section per folder that has any:

```text
APEX DEPLOYMENT TOOL - VALIDATE
------------------------------

VALIDATING:
-----------
  apex/800_JANKVETINA-CZ/apexlang ......................................... OK
  apex/808_MASTER/apexlang ................................................. 2

ERRORS: apex/808_MASTER/apexlang
--------------------------------

  application.apx:1:0
    SYNTAX
    token recognition error at: 'this is n'

  shared-components/static-files.apx:16:0
    REFERENCE_NOT_FOUND
    referenced file shared-components/static-files/icons/app-icon-32.png in
    the fileName property is not found


TIMER: 9s
```

One stanza per message: `file:line:col` on its own line, the same locator format an editor or terminal will linkify, then the compile type and the message text nested under it. The folder is the section header, not a repeated field.

**Messages wrap at 80 columns rather than being truncated.** The message *is* the answer here (a `REFERENCE_NOT_FOUND` names the file that is missing), so no width may cut it. This is why the section is a list and not a table: the compiler's prose runs well past 150 characters, and a `MESSAGE` column that wide destroys its own alignment the moment the terminal re-wraps it. The one thing allowed to overhang is a single unbreakable token, almost always a path, which is worth more intact than wrapped.

A folder that also produced warnings prints a `WARNINGS:` section above its errors, in the same shape. `-silent` drops the per-folder progress rows and keeps the banner, the message sections, and the timer.

## Target Resolution

Targets are collected in this order, and `-input` and `-app` can be combined:

- `-input PATH`, an explicit folder, a zip, or a single `.apx` file, passed through to SQLcl untouched. This mode reads no project config at all.
- `-app ID`, resolved offline: `config/internal/apex.db` gives the owner and alias, which locate `apex/<owner>/<id>_<alias>/apexlang/` under the configured `path_apex`. An app with no export on disk produces a `NOTES:` row naming the path where one was expected, never a traceback.
- Neither, every `apexlang/` folder under the configured APEX root, sorted, skipping hidden folders. This is what makes `adtai validate` after an `-all` export a single obvious command.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config lookup and for resolving `-app` exports. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-input`, `--input` | Yes | every exported `apexlang/` folder | APEXlang folder(s) or zip(s) to validate; comma- or space-separated, repeatable. |
| `-app`, `--app` | Yes | none | Application id(s) whose exported `apexlang/` folder to validate, resolved offline through `config/internal/apex.db`. |
| `-silent`, `--silent` | No | off | Suppress per-folder progress rows; keep the banner, error tables, and timer. |
| `-debug`, `--debug` | No | off | Show the generated SQLcl script and keep Python tracebacks for troubleshooting. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

## Notes

- The compiler validates against metadata from the APEX version that exported the app, so a validate result is only as meaningful as the SQLcl build running it. An old SQLcl against a 26.1 export is not a trustworthy pass.
- One SQLcl session per folder. Batching several `apex validate` calls into one session is measurably cheaper, JVM startup dominates the ~4.5s, so three folders cost about as much as one, but a batch is a single blocking call and could not stream a per-folder progress row, so the per-folder call wins.
- Importing APEXlang source back into APEX is deliberately out of scope: `apex import` replaces the Builder application wholesale and needs its own safety rails.

---

← [docs/README.md](README.md) index
