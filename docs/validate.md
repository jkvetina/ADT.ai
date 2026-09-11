# Validate APEXlang Source (adtai validate)

![Compiled offline, with the database never invited.](images/validate.png)

`validate` runs the APEXlang compiler over an exported `apexlang/` folder and reports what it finds, with a non-zero exit code when anything is wrong. It closes the loop that [`export_apex -apexlang`](export_apex.md) opens: export the `.apx` source, edit it by hand or with an agent, validate, fix, and import only on a clean run.

**The command never connects.** The compiler ships inside SQLcl and answers on a bare `sql -S /nolog` session, so `validate` takes no `-env`, no `-schema` and no credentials, and it works in CI and from any checkout. The only requirement is a SQLcl new enough to carry the compiler, 26.1 or later.

<br>

## Examples

Validate every exported application in the project:

```bash
adtai validate
```

Validate one application by id, resolved offline through `config/internal/apex.db`:

```bash
adtai validate -app 100
adtai validate -app 100 101
```

Validate an explicit folder or zip, with no ADT project around it:

```bash
adtai validate -input ./apex/100_DEMO/apexlang
adtai validate -input ./exports/f100.zip
```

Use it as a gate straight after an export:

```bash
adtai export_apex -app 100 -apexlang -files && adtai validate -app 100
```

<br>

## Output

One streamed row per folder, its label printed before the compile starts and its result after, then one section per folder that has anything to report:

```text
APEX DEPLOYMENT TOOL - VALIDATE
-------------------------------

VALIDATING:
-----------
  apex/100_DEMO ........................................................... OK
  apex/101_REPORTS ......................................................... 1

ERRORS IN apex/101_REPORTS:
---------------------------

  application.apx:1:0
    SYNTAX
    token recognition error at: 'application\n'

TIMER: 20s
```

- **A row names the application folder, not the `apexlang/` tree inside it.** Every folder `validate` finds for itself ends that way, so printing the segment on every row says what the command is rather than what the row is. An `-input` path is echoed exactly as it was typed, because that mode validates what it was handed and may be a zip or a single `.apx`.
- One stanza per message: `file:line:col` on its own line, the locator format an editor or terminal will linkify, then the compile type and the message text nested under it. The folder is the section header rather than a repeated field.
- **Messages wrap at 80 columns rather than being truncated.** The message *is* the answer here, since a `REFERENCE_NOT_FOUND` names the file that is missing, so no width may cut it. That is also why this is a list and not a table: the compiler's prose runs well past 150 characters. A single unbreakable token, almost always a path, is allowed to overhang.
- A folder that also produced warnings prints a `WARNINGS IN <folder>:` section above its errors, in the same shape.
- **The rows are always printed.** There is no `-silent` on this command: a run validates a handful of folders, so the rows are the report rather than noise above it, and the section that says what went wrong needs the row that says which folder it was.

<br>

## The exit code is the deliverable

`validate` is a gate. Exit `0` means every requested folder validated clean, and everything else is non-zero, so CI and agents can branch on it directly. Non-zero covers more than compiler errors on purpose: a run that checked nothing is not a pass.

| Row | Meaning | Exit |
| --- | ------- | ---- |
| `OK` | The compiler validated the folder with no errors. | `0` |
| `OK (n warnings)` | Clean, but the compiler raised warnings, listed in their own section. | `0` |
| *a number* | That many compiler errors, one stanza each. | non-zero |
| `EMPTY` | The folder exists but holds no APEXlang files. A broken export, not a quiet success. | non-zero |
| `UNRECOGNISED` | SQLcl printed something this version cannot read. The raw output is shown verbatim. | non-zero |
| a `NOTES:` row | An `-app` with no export on disk. | non-zero |

A run that cannot start at all refuses instead of reporting, on stderr, under the shared `ERROR - INPUT NOT FOUND:` header every ADT.ai refusal takes ([console.md](console.md#failure-screens)). The lead line under it says which of the two cases you are in:

| Lead line | What happened |
| --- | --- |
| `These inputs are not on disk:` | An `-input` path is not on disk. Decided here, so no SQLcl session is started to be told so. |
| `Nothing to validate:` | A bare run found no `apexlang/` folder at the configured export shape, and names that shape. |

Both are chrome rather than a result, so nothing suppresses them: a refusal that went quiet would exit non-zero with a banner, a timer and nothing else.

`UNRECOGNISED` exists because SQLcl exits `0` whatever the compiler says, which makes the printed text the only signal. If a future SQLcl changes its wording, the run fails loudly and shows what it actually said rather than reporting a pass on output nobody parsed.

**Warnings do not fail the run.** The compiler's own verdict is still success, but they are never hidden. This matters most for `FILE_IGNORED`, which means the compiler did not check that file at all, and a bare `OK` there would be a pass over work that never happened.

<br>

## Static files are linked in, not duplicated

An `-apexlang` export deliberately omits the `shared-components/static-files/` payloads, so the repository never holds two copies of every static file. The compiler does not accept that: `shared-components/static-files.apx` names each payload in a `file "<path>"` declaration, and every missing one is a `REFERENCE_NOT_FOUND`.

So the export's own tree is completed rather than copied. The payloads are **hardlinked** from the sibling `files/` export into `apexlang/shared-components/static-files/`, and kept in step with it on every export, validate and deploy. The compiler opens the folder that is committed.

A hardlink is one inode with two names, so this copies no bytes. The folder carries its own `.gitignore` holding `*`, which git applies to everything inside it including that file, so the payloads are neither tracked nor reported as untracked. Nothing in your project's own `.gitignore` has to change.

- **`files/<X>` maps one-to-one onto `shared-components/static-files/<X>`**, same relative paths, no rename and no transform. That is what makes this a link operation rather than a translation.
- **Hardlinks, not symlinks.** A hardlink is indistinguishable from a regular file to a directory walker, while Java's `Files.walk` will not descend a symlinked directory without `FOLLOW_LINKS`, so a symlinked tree risks the compiler skipping whole folders. A symlink is no cheaper either, being a directory entry plus its own inode. A copy is the fallback when a filesystem refuses to link.
- **Reconciled, not rebuilt.** A run relinks only what moved and removes a payload whose source is gone, mirroring the `apexlang/` folder's own contract, so a static file deleted in App Builder cannot survive here and editing one page churns nothing.
- **`-input` is never completed.** That mode reads no project config by contract and may point at a zip or a single `.apx`, so it validates exactly what you gave it. Use it to see the raw committed tree.

An application with no `files/` export gets a `NOTES:` row naming `export_apex -files`, because eight `REFERENCE_NOT_FOUND` messages say what is missing but not how to get it.

There used to be a staging tree here, assembled under `config/temp/apexlang/<app-folder>/` and compiled in place of the real one. It was named after the app folder alone, so two `apexlang/` trees sharing that name, an export and a patch snapshot of it, resolved to one directory and overwrote each other before either was compiled.

A project that ran that version still carries the copy, git-ignored and so reported by nothing. `validate` and `patch -deploy` delete `config/temp/apexlang/` when they find it, and leave `config/temp/` itself alone: that is where every SQLcl call writes its throwaway script.

<br>

### Why an empty placeholder is worse than a missing file

The compiler checks that a referenced path **exists**, not what is in it. Real payload bytes and payloads truncated to zero bytes validate identically, while deleting one file produces its `REFERENCE_NOT_FOUND`.

That makes touching a placeholder into existence the most dangerous shortcut available here: it turns the gate green and then imports an application with broken images. Only payloads that genuinely exist are linked, and the compiler reports every real gap.

This is a live hazard in existing repositories too. A tree whose `shared-components/static-files/` holds zero-byte files validates clean today and would import an application with no icons. A payload folder that is suspiciously all-zero is not a valid export.

<br>

## Which folders get validated

Targets are collected in this order, and `-input` and `-app` can be combined:

- `-input PATH`, an explicit folder, a zip, or a single `.apx` file, passed to SQLcl untouched. This mode reads no project config at all.
- `-app ID`, resolved offline: `config/internal/apex.db` gives the owner and alias, which locate `apex/<owner>/<id>_<alias>/apexlang/` under the configured `path_apex`. An application with no export on disk produces a `NOTES:` row naming the path where one was expected, never a traceback.
- Neither, in which case every `apexlang/` folder sitting at the project's own export shape is validated, sorted, hidden folders skipped. That is what makes a bare `adtai validate` after an `-all` export one obvious command.

**A bare run matches a shape, it does not search.** An export lands at `path_apex` / `apex_path_app` / `apexlang`, and those two config keys are already the project's statement of where. Read back with their tokens globbed they give one pattern, `*/apex/*/apexlang` on the shipped defaults, and only a folder at that pattern is an export.

That is why the copies are not discovered and why no list of them is kept:

- A patch snapshot carries the whole export path under `patch/<name>/snapshots/`, three levels deeper than the pattern.
- `config/temp/apexlang/` names `temp` where the pattern names `apex`.
- A project that renames `patch_root`, or grows some other folder full of trees nobody edits, is covered for the same reason rather than by an exclusion somebody has to remember to add.

Pass one to `-input` when you do want to compile a copy.

A `Nothing to validate:` refusal names that shape as your config spells it, `<schema>/apex/{$APP_ID}_{$APP_ALIAS}`, which is the two keys to go and check when an export you expected is missing.

<br>

## Notes

- The compiler validates against metadata from the APEX version that exported the application, so a result is only as meaningful as the SQLcl build running it. An old SQLcl against a 26.1 export is not a trustworthy pass.
- One SQLcl session per folder. Batching several calls into one session is measurably cheaper, since JVM startup dominates the few seconds a run costs, but a batch is a single blocking call and could not stream a per-folder row, so the per-folder call wins.
- Importing the tree back is `patch -deploy -app`, which reads exactly this tree and lands it on a sandbox id first, on [patch_import.md](patch_import.md). The loop from export to promotion is on apex_round_trip.md.

<br>

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-input`, `--input` | Yes | every exported `apexlang/` folder | APEXlang folder or folders, or zips, to validate. Comma-separated, space-separated, or the flag repeated. |
| `-app`, `--app` | Yes | none | Application id or ids whose exported `apexlang/` folder to validate, resolved offline through `config/internal/apex.db`. |

Shared options (-root, -config-dir, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
