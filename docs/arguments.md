# Shared Arguments (adtai)

Eight flags mean the same thing wherever they are accepted, so they are documented here once instead of in every command's own table. A command page lists only the flags that are its own and points back to this one. Not every command takes all eight: the second table below says which takes which.

<br>

## The eight flags

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder. Config and connection files resolve from here, and so does the Git history the history commands read. |
| `-config-dir`, `--config-dir` | Yes | none | Folder holding project config YAML. ADT.ai loads the shipped defaults first, then overlays these. |
| `-env`, `--env` | No | connection default environment | Connection environment to use, for example `DEV`. |
| `-schema`, `--schema` | Yes (one per run on `connection` and `discovery`) | environment default schema | Schema to work on. Where it repeats, pass it several times, space-separate it (`-schema APP CORE`), use comma lists, or use `%` patterns such as `CORE%`. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value, or the path to a key file, for encrypted connection passwords. |
| `-debug`, `--debug` | No | off | Show the input parameters and the SQL behind the run, and keep Python tracebacks for troubleshooting. |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

<br>

## Which command takes which

A flag a command does not take is a parser error, not a flag it ignores.

| Argument | Commands that take it |
| -------- | --------------------- |
| `-root` | every command |
| `-config-dir` | `connection`, `dependencies`, `discovery`, `export_apex`, `export_data`, `export_db`, `flow`, `recompile`, `ut`, `validate` |
| `-env` | `connection`, `dependencies`, `discovery`, `export_apex`, `export_data`, `export_db`, `flow`, `recompile`, `ut` |
| `-schema` | `connection`, `dependencies`, `discovery`, `export_apex`, `export_data`, `export_db`, `recompile`, `ut` |
| `-key` | `connection`, `dependencies`, `discovery`, `export_apex`, `export_data`, `export_db`, `flow`, `recompile`, `ut` |
| `-debug` | `connection`, `discovery`, `export_apex`, `export_data`, `export_db`, `flow`, `recompile`, `ut`, `validate` |
| `-beep` | every command |
| `-nobeep` | every command |

<br>

## How the files are found

`-root` is where a project starts. Config is layered rather than replaced: ADT.ai reads the shipped `ADT.ai/config/` defaults first, then overlays `-config-dir` folders, then `<root>/config/`, then `<root>/`. A project-local value therefore wins over a shipped one.

Connection files work the other way round. It is **first match wins**, with no merging, and the candidates are tried in this order, where `<FOLDER>` is the `-root` directory's own name:

1. paths set through `connections.path`, `connections_path` or `connections_dir`, then any explicit `-config-dir` folder
2. `<root>/connections.yaml`
3. `<root>/connections/<FOLDER>.yaml`
4. `<ADT.ai checkout>/connections/<FOLDER>.yaml`

Because the third and fourth candidates are named after the folder, any directory holding a `connections.yaml` works as a project root. Setting `connections.file` replaces the derived filename in every location above, which is how a connection file is kept outside the repository while the project keeps its config.

`-env` selects an environment inside whichever file was matched. Naming one that is not configured prints the loaded file to edit and the environments that are configured, as a sorted indented list, and the same holds for `-schema`.

The full picture, including wallets, identity, timeouts and the session startup script, is on [config.md](config.md).
