# Map APEX Page Navigation (adtai flow)

`flow` answers two questions about an application's navigation graph: which pages link **into** a page, and which pages you can reach **from** it. Reach for it before changing a page, when you need to know what will send users there and where they will go next. It scrapes the links once, stores them locally, and answers offline from then on.

The store is a SQLite file at `config/internal/flow.db` (gitignored), and every refresh also writes Mermaid, Graphviz DOT and JSON diagrams under `config/flow/`.

<br>

## Examples

Build or rebuild one application's graph from the database:

```bash
adtai flow -app 100 -refresh -env DEV
```

Ask what links into a page, and what a page links out to:

```bash
adtai flow -app 100 -to 2
adtai flow -app 100 -from 1
```

Drop an application you no longer track:

```bash
adtai flow -app 100 -delete
```

`-app` is required for every action. The store holds many applications side by side, keyed by workspace and application id, so every run has to say which one it means.

<br>

## Output

`-refresh` connects, scans under its own `APP <id>, REFRESHING:` header, and reports what it stored:

```text
APEX DEPLOYMENT TOOL - FLOW
---------------------------

CONNECTING TO SCHEMA SANDBOX, DEV:
----------------------------------
              APEX | 26.1.0
          DATABASE | 23.26.1.0.0 | FREEPDB1


APP 100, REFRESHING:
--------------------


APP 100/ORDERS, REFRESHED:
--------------------------

  PAGES   EDGES   DIAGRAMS
  -----   -----   --------
      4       9          3


TIMER: 1s
```

A query reads that store and prints one table of resolvable links:

```text
APEX DEPLOYMENT TOOL - FLOW
---------------------------

LINKS INTO APP 100 PAGE 1 (4):
------------------------------

  FROM APPS   FROM PAGES   SRC TYPES    COMPONENTS       FLAGS
  ---------   ----------   ----------   --------------   -----
        100   2            BRANCH       Back to Orders   PAGE
        100   4            BRANCH       Back to Orders   PAGE
        100   3            BUTTON       BACK_TO_ORDERS   PAGE
        100   shared       LIST_ENTRY   Orders           PAGE


TIMER: 0s
```

- The number in the header is how many rows follow it.
- `-from` prints the same table with `TO APPS` and `TO PAGES` where `FROM APPS` and `FROM PAGES` stand, under a `LINKS FROM APP <id> PAGE <n>` header.
- `FROM PAGES` reads `shared` when the source belongs to the application rather than to one page, which is where list entries, tabs and navigation-bar entries sit.
- Component text is capped at 30 characters so the width stays stable, and an empty result prints `(none)` rather than nothing.

Before the store exists, every mode says so and exits `1`:

```text
APEX DEPLOYMENT TOOL - FLOW
---------------------------
No APEX flow database found. Run 'adt flow -app N -refresh' to build it.


TIMER: 0s
```

- An application missing from an existing store reports `Application N is not loaded` and exits `1`.
- With a store present and no action flag, the run prints a short hint and exits `2`.
- Each refreshed application scans under its own `APP <id>, REFRESHING:` header, printed before the reads rather than after them, so the wait never sits on a blank screen. The owner lookup for every requested application runs once, up front, under the banner.

<br>

## The three modes

- **Query** (`-to PAGE` or `-from PAGE`) reads the local store and lists the incoming or outgoing links for one page. It opens no connection.
- **Refresh** (`-refresh`) connects through the default APEX schema, resolves the application's owner, reconnects through that schema, and reloads the application's metadata, pages and edges. A refresh is a full rewrite in one transaction: the old rows are deleted and replaced, never appended to.
- **Delete** (`-delete`) removes the application, its pages and its edges from the store.

<br>

## What counts as an edge

An edge is any link from a source component to a target page: page branches, buttons, list entries, tabs, navigation-bar entries and report column links. Report column links use the APEX dictionary's `COLUMN_ALIAS` value as the label, since that is the field the report-column views expose.

Every edge carries a flag describing how resolvable its target is:

| Flag | Meaning |
| ---- | ------- |
| `PAGE` | A page in the same application. The common case. |
| `CROSS_APP` | A link into another application (`f?p=<other_app>:<page>`), indexed by the resolved target application and page. |
| `DYNAMIC` | The target page number is computed at runtime, from a substitution string or an item value, and cannot be resolved statically. |
| `NONE` | The link leaves APEX entirely, or carries no page target at all. |

`-to` and `-from` surface only the resolvable flags, `PAGE` and `CROSS_APP`. The Mermaid and DOT diagrams draw the same set. The JSON dump is lossless and keeps every edge, `DYNAMIC` and `NONE` included, so downstream tooling can decide for itself what to draw.

A store written before the report-column label fix can still hold rendered HTML or heading text where a column name belongs. Those rows display as `COL_<component_id>` until the application is refreshed again.

<br>

## Arguments

| Argument | Repeatable | Default | Description |
| -------- | ---------- | ------- | ----------- |
| `-app`, `--app` | Yes | none (required) | Application id or ids, required for every action. Space-separate, repeat, or use a range: `-app 100 200`, `-app 100-200`, `-app 100+`. Ranges resolve against the APEX catalog on `-refresh` and filter the loaded store on query or delete. |
| `-to`, `--to` | No | none | Show the pages that link INTO this page. |
| `-from`, `--from` | No | none | Show the pages reachable FROM this page. |
| `-refresh`, `--refresh` | No | off | Resolve the owner schema, rescrape the application, rewrite its stored edges, and write the Mermaid, DOT and JSON diagrams. |
| `-delete`, `--delete` | No | off | Delete the application, its pages and its edges from the store. |

Shared options (-root, -env, -config-dir, -key, -debug, -beep, -nobeep) are on [console.md](console.md#shared-arguments).
