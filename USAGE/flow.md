# APEX Flow — map page navigation (adtai flow)

`flow` answers two questions about an APEX application's navigation graph: **"where do links point INTO this page?"** and **"which pages can I reach FROM this page?"**. It scrapes the navigation links of one application from the database once, stores them in a local SQLite file (`config/flow.db`, gitignored), writes Mermaid, Graphviz DOT, and JSON diagrams under `config/flow/`, and then answers both questions offline.

`-app` is mandatory for **every** action — the store holds many applications side by side, keyed by `(workspace, app_id)`, so every command must say which application it means.

The command has three modes:

- **Query** (`-to PAGE` / `-from PAGE`): read the local store and list the incoming or outgoing page links for one page. Query tables show source component names, with component text capped at 30 characters for stable console width. Column headers render in UPPERCASE, PLURAL form — `FROM APPS`/`FROM PAGES`/`SRC TYPES`/`COMPONENTS`/`FLAGS` for `-to`, and `TO APPS`/`TO PAGES`/`SRC TYPES`/`COMPONENTS`/`FLAGS` for `-from` — matching the refresh summary's `PAGES`/`EDGES`/`DIAGRAMS` style. Existing stores created before the report-column fix can still contain rendered HTML or heading text; those stale report-column rows display `COL_<component_id>` instead of the bad stored text until the app is refreshed. With no action flag it prints a short hint and exits `2`. If the store file does not exist yet it reports `No APEX flow database found` and exits `1`; if the application is not in the store it reports `Application N is not loaded` and exits `1`.
- **Refresh** (`-refresh`): connect through the default APEX schema, resolve the application's owner, reconnect through that configured schema, and (re)load the application's metadata, pages, and navigation edges. A refresh is a full rewrite: the application's old rows are deleted and replaced in one transaction, never appended. Each refresh writes all diagram formats automatically to `config/flow/app_<id>.mmd`, `.dot`, and `.json`.
- **Delete** (`-delete`): delete the application and all its pages and edges from the store.

Every run prints the generic `APEX DEPLOYMENT TOOL: FLOW` banner and a completion timer.

## What counts as an edge

A navigation edge is any link from a source component to a target page: page branches, buttons, list entries, tabs, navigation-bar entries, and report column links. Report column links use the APEX dictionary `COLUMN_ALIAS` value as the component label because that is the field exposed by APEX report-column views. Values that look like headings, link text, or HTML templates are replaced with `COL_<component_id>` in query output. Each edge carries a `flag` describing how resolvable its target is:

- `PAGE` — a same-application page link (the common case).
- `CROSS_APP` — a link into another application (`f?p=<other_app>:<page>`); indexed by the resolved target application and page.
- `DYNAMIC` — the target page number is computed at runtime (substitution strings, item values) and cannot be resolved statically.
- `NONE` — the link leaves APEX entirely (an external URL) or carries no page target.

`-to` and `-from` only surface **resolvable** links (`PAGE` and `CROSS_APP`). The `json` dump is lossless and keeps every edge, including `DYNAMIC` and `NONE`, so diagrams and downstream tooling can decide what to draw. The Mermaid and DOT dumps draw only the resolvable edges.

## Examples

Build (or rebuild) one application's navigation graph from the database:

```bash
cd ~/Dropbox/PROJECTS/CORE23
adtai flow -app 956 -refresh -env DEV
```

Ask where links point into a page, and which pages you can reach from a page:

```bash
adtai flow -app 100 -to 2
adtai flow -app 100 -from 1
```

Delete an application from the store when you no longer track it:

```bash
adtai flow -app 100 -delete
```

The default diagram paths are `<root>/config/flow/app_<id>.<ext>` (`.mmd`, `.dot`, and `.json`). Empty link results print an explicit `(none)` rather than nothing.

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-app`, `--app` | Yes | none (required) | Application id(s). Mandatory for every action. Space-separate, repeat, or use ranges: `-app 100 200`, `-app 100 -app 200`, `-app 100-200`, `-app 100+`. On `-refresh`, ranges are resolved against the APEX catalog; on query/delete they filter the loaded store. |
| `-to`, `--to` | No | none | Show pages that link INTO this page (incoming links). |
| `-from`, `--from` | No | none | Show pages reachable FROM this page (outgoing links). |
| `-refresh`, `--refresh` | No | off | Resolve the app owner schema, rescrape the application from the database, rewrite its stored edges, and write Mermaid/DOT/JSON diagrams. |
| `-delete`, `--delete` | No | off | Delete the application and all its pages and edges from the store. |
| `-root`, `--root` | No | `.` | Project root folder holding `config/flow.db` and used for config and connection lookup. |
| `-config-dir`, `--config-dir` | Yes | none | Folder containing project config YAML (refresh only). ADT.ai always loads repo defaults first, then overlays these project configs. |
| `-env`, `--env` | No | connection default environment | Connection environment to refresh from, for example `DEV` (refresh only). |
| `-debug`, `--debug` | No | off | Show input parameters and SQL queries with bind values. |
| `-key`, `--key` | No | `ADT_KEY` | Encryption key value or path to a key file for encrypted connection passwords (refresh only). |
| `-beep [THEME]`, `--beep [THEME]` | No | off | Force the completion chime on for this run, optionally using a theme override such as `-beep zelda`. |
| `-nobeep`, `--nobeep` | No | off | Suppress completion sounds for this run; this wins over `chime_theme` and `-beep`. |

---

← [USAGE.md](../USAGE.md) index
