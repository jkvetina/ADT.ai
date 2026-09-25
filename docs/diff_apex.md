# Compare APEX Applications and Files (adtai diff -apex)

`diff -apex` compares the APEX applications and static files the two schemas own instead of their objects, and skips the object export entirely. It answers the question the object comparison cannot: SQLcl puts `releases/apex/f<id>/f<id>.sql` into its artifact whether or not the application changed.

How the two sides connect and the shared flags are on [diff.md](diff.md).

<br>

## Examples

Compare every application and static file the two schemas own, then list what differs:

```bash
adtai diff -source DEV -target UAT -apex
adtai diff -source DEV -target UAT -apex -verbose
```

Only application 100, and every application from 200 to 299:

```bash
adtai diff -source DEV -target UAT -apex -app 100 200-299 -verbose
```

Only pages 10 to 20 and page 99 of application 100, leaving out everything application-wide:

```bash
adtai diff -source DEV -target UAT -apex -app 100 -page 10-20 99 -verbose
```

Only whether anything differs, at most five lines per listing:

```bash
adtai diff -source DEV -target UAT -apex -verbose -limit 5
```

<br>

## Output

The connection blocks and the `COMPARING SCHEMAS:` row are the ones [diff.md](diff.md#output) prints. What differs is decided per object type:

| Object type | Named | `CHANGED` when |
| --- | --- | --- |
| `APEX APPLICATION` | its id and alias | APEX's own fingerprint of the application differs |
| `APEX APP FILE` | `<app id>/<file name>` | its bytes differ |
| `APEX WORKSPACE FILE` | its file name | its bytes differ |

Every application that differs opens on a summary: one row per page, then one per component type outside a page, each counting its lines by status. The workspace's static files, the one thing outside an application a comparison of applications can meet, close the screen. That is the whole default screen:

```text
CHANGED COMPONENTS - 100/ORDERS:
--------------------------------

  TYPE                  NAME              MISSING   EXTRA   CHANGED
  -------------------   ---------------   -------   -----   -------
  PAGE                  10 Orders                                 2
  PAGE                  15 Order Detail         1
  APPLICATION                                                     1
  LIST                                                            1
  ENTRY                                                           1
  PLUGIN                                            1
  WORKSPACE APP GROUP                                             1
  APP FILE                                                        1


CHANGED COMPONENTS - 101/ORDERS101:
-----------------------------------

  TYPE          NAME            MISSING   EXTRA   CHANGED
  -----------   -------------   -------   -----   -------
  APPLICATION   101/ORDERS101         1


CHANGED WORKSPACE FILES:
------------------------

  NAME       SIZE [KB]   STATUS
  --------   ---------   ------
  logo.png        47.1   EXTRA
```

`-verbose` adds, between the summaries and the workspace files, one section per page. Every changed property is one line, and every line names the component's type, name and status, so a component changed in two properties is two full lines.

```text
CHANGED PAGE 100.10 - Orders:
-----------------------------

  TYPE      NAME          STATUS    PROPERTY
  -------   -----------   -------   ---------
  PROCESS   Load Orders   CHANGED   sequence
  PROCESS   Load Orders   CHANGED   condition
  REGION    Order Lines   CHANGED   template


CHANGED PAGE 100.15 - Order Detail:
-----------------------------------

  TYPE   NAME              STATUS
  ----   ---------------   -------
  PAGE   15 Order Detail   MISSING
```

- **A page is its own section, by number**, titled `APP.PAGE` and its name. Each region, button, process or item that changed has a line. The page itself has a line only when one of its own properties changed, or when one side lacks it. A component nested in another, such as a saved report in a region, is named by the innermost one.
- **Nothing outside a page gets a section.** The summary's type rows are that listing: the application's own properties, its shared components (lists, templates, plugins, lists of values) and their changed entries, the workspace components its export carries, marked `WORKSPACE`, and its own static files, `APP FILE`.
- **The summary counts components, not lines**: a page row counts its section's components by status, a type row the components of that type outside any page. A zero is left blank.
- **`PROPERTY` names one changed property per line**, the way `diff -data` lists one column per line. A `MISSING` or `EXTRA` component names none, and neither does one whose changes are all in the components under it.
- A line under a component is `MISSING` when the target lacks every property it has, `EXTRA` when the source does, else `CHANGED`.
- An application only one side has is one line in its summary.
- `CHANGED WORKSPACE FILES:` closes the listings. `SIZE [KB]` is the source's copy, or the target's when only the target has the file, in KB with one decimal on every row.
- Every line fits the 80-column screen. The name gives way first, then the property, the type last. The status and the counts never do.
- Matching applications and files print `NO DIFFERENCES:`. The countdown keeps a history apart from the object and REST comparisons, and apart per `-app` and `-page` selection.

<br>

### One page with -page

`-page` narrows the comparison to the pages you name, and leaves out everything application-wide: the application's own properties, its shared components, its files and the workspace's files. The summary follows, so it counts only the selected pages:

```text
CHANGED COMPONENTS - 100/ORDERS:
--------------------------------

  TYPE   NAME        MISSING   EXTRA   CHANGED
  ----   ---------   -------   -----   -------
  PAGE   10 Orders                           2


CHANGED PAGE 100.10 - Orders:
-----------------------------

  TYPE      NAME          STATUS    PROPERTY
  -------   -----------   -------   --------
  PROCESS   Load Orders   CHANGED   sequence
                                    condition
  REGION    Order Lines   CHANGED   template
```

- An application whose selected pages match prints nothing. When none of the selected pages differ, the run prints `NO DIFFERENCES:` with `the selected pages match on both sides`.
- An application only one side has is still listed, since none of its pages can match on the side without it.

<br>

### A working copy with -target-app

`-target-app` compares the one application `-app` names with another id on the target, such as a copy you have been changing next to the original, in the same schema or another:

```bash
adtai diff -source DEV -target DEV -apex -app 100 -target-app 101 -verbose
```

- The summary is titled with both, `CHANGED COMPONENTS - 100/ORDERS -> 101/ORDERS_COPY:`, the longer name giving way when the pair would outrun the screen. A page section names the source's id, `CHANGED PAGE 100.10 - Orders:`.
- Everything else pairs as usual: the fingerprints, the pages by number, the components by name, and the app files by name with each application's own id left off.
- It needs `-apex` and exactly one `-app` id, not a range. The countdown keeps a history apart per copy.
- With `-restore`, the copy's export is written into the original's folder under the original's id, `f101.sql` landing as `f100.sql`, so `git diff` reads as the change from 100 to 101.

<br>

### What -restore writes

[`-restore`](diff.md#restoring-the-targets-versions) exports each differing application from the target in the formats the checkout already holds for it: full, split, readable, embedded code, APEXlang and app files, whichever are there.

An application the checkout has no folder for takes its siblings' formats, and split SQL when there are none. An application the target lacks has its folder cleared, keeping anything that is not an export.

With `-page`, only the selected pages' files are rewritten, in every format, and neither the application-wide files nor the workspace's files move. Otherwise a differing workspace file is written to the `-files_ws` folder, or deleted when the target has none.

<br>

## What is compared

- **Applications pair by id.** Application 100 on the source is compared with application 100 on the target, the way `patch` deploys it. A paired application is named by the source's alias.
- **An application is `CHANGED` when its `CHECKSUM-SH256` differs.** That is the fingerprint `APEX_EXPORT.GET_APPLICATION` computes over the whole application and `export_apex` already records. APEX builds it independently of component ids, so an environment imported with a different id offset still compares equal.
- **Only a `CHANGED` application is exported, on both sides, to find its components.** An application whose fingerprints match costs no export at all.
- **Both sides are exported in the best format both instances have**: APEXlang when both run APEX 26.1 or later, readable YAML when neither does, and split SQL when they straddle 26.1 or when the readable export comes back empty. Split SQL is the last resort because it also writes default values that one instance stores and the other omits.
- **Component ids and the APEX release never make a difference.** Neither do the application and workspace ids, a subscription's version number and referenced id, the timestamp before which APEX refuses bookmarked URLs, audit stamps, or trailing whitespace. Two environments holding the same application disagree about all of these.
- **A page pairs by its number, a shared component by its type and name**, and a region, button or process by its kind and name, so reordering a page's regions changes nothing but their sequence.
- **A supporting-objects script compares by its body.** APEXlang keeps each install, upgrade and deinstall script, and each report layout, in a file of its own that the `.apx` only names. That file is read, so a changed script is listed `CHANGED` by its `contentFile` or `scriptFile`. A script named with a space, `installScript "emp adams"`, is read like any other.
- **An application's own static files are compared only when both sides own the application**, and they are counted in its summary as `APP FILE`. A `MISSING` or `EXTRA` application already says its files are missing.
- **Workspace static files are the workspace the schema is mapped to**: the connection's `apex.workspace` when it is set, else the first by name. A schema mapped to no workspace has none.
- Files are read with the query `export_apex -files` and `-files_ws` write from, so a file compares equal exactly when that export would write the same bytes. A name stored twice is compared copy by copy.

<br>

## Scope

- Each side owns the applications its schema parses, narrowed by the connection's `apex` workspace, group and application list, exactly as `export_apex` narrows them.
- `-app` replaces that application list on both sides. It takes ids and `MIN-MAX` / `MIN+` ranges, repeatable, and is refused without `-apex`.
- `-page` takes page ids and the same ranges, repeatable, and is refused without `-apex`. Pages are still read from each application's whole export.
- `-target-app` takes one id and replaces the target's side of the one `-app` id; it is refused without `-apex` or with anything but a single `-app` id.
- With no `-schema`, a side is the environment's APEX schema (`schema_apex`), the one `export_apex` exports from.
- `-name` narrows the summaries and sections by application and the workspace files by name, so `-name 100%` keeps application 100's summary and sections, its files included.
- `-limit N` lists at most `N` lines per summary, per section and per file listing, and says how many it left off.
- `-type` and `-out` are refused, because they belong to the object comparison and there is no artifact.
