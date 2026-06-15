# Recompile Objects (adtai recompile)

Recompile invalid database objects in the current project's environment:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai recompile -target DEV
```

Force-recompile everything with native code and optimize level 3:

```bash
adtai recompile -target DEV -force -native -level 3
```

Scope the recompile by object type and name:

```bash
adtai recompile -target DEV -type PACKAGE% -name XX%
```

The command reads the object overview, recompiles invalid (or all, with `-force`) objects, retries failures in reverse order on a fresh connection, then re-checks what is still invalid. If there is nothing to compile, it keeps the initial overview and skips the final re-check. It prints an OBJECTS OVERVIEW table with invalid-object and missing-PL/Scope counts and, when objects remain invalid, an INVALID OBJECTS table, then exits non-zero. Parity gaps vs old ADT: the OBJECTS OVERVIEW table always prints (old ADT showed it only under `__main__`) and there is no Slack-style team notification.

## Arguments

| Argument       | Repeatable | Default | Description |
| -------------- | ---------- | ------- | ----------- |
| `-root`, `--root` | No | `.` | Project root folder used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML. ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to recompile in. |
| `-target`, `--target` | No | none | Connection environment (alias of `-env`, for old ADT muscle memory). |
| `-schema`, `--schema` | No | environment default DB schema | Schema to recompile. |
| `-type`, `--type` | No | `%` | Object type pattern to recompile, supports `%` wildcards. |
| `-name`, `--name` | No | `%` | Object name pattern to recompile, supports `%` wildcards. |
| `-force`, `--force` | No | off | Recompile all matching objects, not just invalid ones. |
| `-level`, `--level` | No | none | PL/SQL optimize level (1-3). |
| `-native`, `--native` | No | off | Compile PL/SQL to native code. |
| `-interpreted`, `--interpreted` | No | on | Compile PL/SQL to interpreted code (default; `-native` takes precedence). |
| `-scope`, `--scope` | No | none | PL/Scope settings (`IDENTIFIERS`, `STATEMENTS`, `ALL`). |
| `-warnings`, `--warnings` | No | none | PL/SQL warnings (`SEVERE`, `PERF`, `INFO`). |
| `-silent`, `--silent` | No | off | Suppress object overview details while keeping the standard banner, connection block, and final timer. |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values; keep Python tracebacks. |
| `-beep`, `--beep` | No | off | Force the completion chime on for this run, even from a worktree checkout. |

---

← [USAGE.md](../USAGE.md) index
