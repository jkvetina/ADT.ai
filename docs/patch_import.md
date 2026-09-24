# Importing an APEXlang Application (adtai patch -deploy -app)

What `-app` on a `-deploy` run does with the application's committed `apexlang/` tree: which id it lands on, where it is staged, the signatures the log records, and what is refused before the first install script runs. The command and its flags are on [patch.md](patch.md); the loop from export to promotion is on apex_round_trip.md.

<br>

## Examples

Land the tree on a derived sandbox id, beside the real application:

```bash
adtai patch -target DEV -name 601 -deploy -app 100601
```

Land the same tree on the application's own id, in the next environment:

```bash
adtai patch -target UAT -name 601 -deploy -app
```

Without `-app` nothing here runs, and the deploy is byte for byte what it was before the import existed.

<br>

## Where the tree lands

**The value is where the tree LANDS, not which applications ship.** Bare `-app` changes no application id. `-app <id>` installs the same tree on that id, with the alias derived in the same step, since an APEX alias is unique per workspace and a copied id keeping the source alias collides with the application it came from. One id per run: several applications cannot fold onto one id, and a second value is refused rather than reduced to the first.

The sandbox id is derived, never configured per developer: the application number carrying the task number, so application `100` under task `601` is `100601` and its alias `ORDERS_601`. Uniqueness comes from the task number, which is already unique across developers.

Retargeting is a flag on SQLcl's `apex import`, never an edit to the tree's `deployments/default.json`, which is what lets a promote install the byte-identical tree a sandbox import validated.

<br>

## A numbered target is stamped with the developer who deployed it

An APEXlang import writes no audit column. After `-app <id>` imports, the same session therefore reads the target application's version and hands it straight back through `SET_APPLICATION_VERSION`, which makes APEX stamp the row without changing the version.

One call, and the value it passes is identical: the API writes the audit columns whether or not the version changed. The version text never moves. `apex_applications.last_updated_by` then names you and `last_updated_on` the moment, so both a sandbox and an explicitly overwritten main application identify their deployer on sight in the Builder.

The name is `apex_account` from [`IDENTITY.yaml`](config.md), else `git config user.name`. A checkout naming no developer stamps nobody and imports exactly as before.

Only a numbered target is stamped. Bare `-app` still lands each application under its own id without rewriting its audit row. Numbered `-app <id>` records the deployment whether `<id>` is a sandbox id or the source application's own id.

`created_by` stays empty either way, because APEX exposes nothing that can write it, which is why [`patch -drop`](patch_drop.md) clears an ownerless sandbox rather than refusing one.

<br>

## The tree is imported where it sits

`export_apex -apexlang` omits the static-file payloads by design, and `apex import` validates before it writes, so an incomplete tree fails one `REFERENCE_NOT_FOUND` per payload.

The run therefore hardlinks the sibling `files/` export into the tree's own `shared-components/static-files/`, the same reconciliation [`validate`](validate.md) performs, so the bytes the import sees are the bytes the compile gate passed and the bytes that are committed.

An application the patch ships and nobody exported a tree for is a `NOTES:` row naming the export that would fix it, never a refusal: the patch may legitimately carry an application this run was not asked to import.

<br>

## Three signatures, read before anything is written

`LATEST ON TARGET` is the live checksum of the application about to be written, `CHANGE BASED ON` the one `export_apex` recorded when it wrote the tree, and `DEPLOYING` a content hash of the tree on disk.

The first two are APEX's own `CHECKSUM-SH256`, independent of ids and comparable across instances; the third opens `TREE:` because it answers a different question, what is on disk right now rather than what a live application looks like.

```log
-- APEX APPLICATION 100 IMPORTED AS 100601
--   LATEST ON TARGET | (no application)
--   CHANGE BASED ON  | SH256:795mkyqBRAN1UkZCYSV6l3ntA3JyqzBP8fmKN6LOT7k=
--   MERGE BASE       | 9f2c1ab7d0e34c5f8b6a2d1e7c0f4a93b5d8e621 (db/dev)
--   DEPLOYING        | TREE:785c6726ff679a37894c1eb3157b4f9862e797b5
--   DEPLOYED FROM    | sandbox/apex/100_ORDERS/apexlang
```

`DEPLOYED FROM` names the folder the application was read out of. The patch carries no copy of an APEXlang tree ([patch_content.md](patch_content.md)), so the log is the one place a reader finds where the bytes came from.

`MERGE BASE` is the commit `export_apex -apexlang` recorded when it wrote the tree, and in brackets the ref [`-mirror`](export_apex.md) shares it on. It is not a signature and moves no verdict; it is what the refusal below turns into an instruction.

The row is absent for a tree exported without a recorded commit, which is every tree exported before this existed.

**The target moving is a showstopper.** When the first two disagree, somebody changed the application after the tree was exported and an import would overwrite work this patch never saw, so the deploy refuses before its first install script. An application with no recorded signature refuses the same way: the run cannot say what the change was based on. An id nothing is installed on yet, the ordinary first sandbox import, is not drift and passes.

The refusal says who changed the application, when, and how old your base is:

```text
  APP 100 CHANGED SINCE YOUR EXPORT

  Deploying now would overwrite that work.

  CHANGED BY  | DEVELOPER
  CHANGED ON  | 2026-09-23 17:02
  YOUR BASE   | 2026-09-23 16:58 (a5e59eb0 on db/dev)

  Run: git rebase db/dev, then deploy again (or -force to overwrite)
```

`CHANGED BY` and `CHANGED ON` are APEX's own `LAST_UPDATED_BY` and `LAST_UPDATED_ON`, the newer of the application and its pages. A Builder save names the developer. A deploy names the developer identity it ran under ([`IDENTITY.yaml`](config.md), and the stamp above), or the database user when there is none.

They are read before the deploy locks the application, because the lock's build-status write stamps the application with this deploy's own user. An application nobody has touched since its import carries no author, and both rows then read `(not recorded)`.

`YOUR BASE` is when `export_apex` took the checksum the change was made against, so the gap to `CHANGED ON` is how far behind you are, with the commit and the ref the rebase lands on. A tree exported before ADT recorded that time reads `(export time not recorded)`.

The checksums stay in the import log, which also records the author as a `LAST CHANGED` row, left out when APEX has none.

**`Run: git rebase` needs both halves of the merge base.** A recorded commit is only a base this checkout has; a shared ref is what makes it everybody's.

With both, the state now live on the target is a commit on that ref, disjoint pages merge as text, and the recovery is a rebase. With either missing, the recovery is the one it always was, a re-export and a reconciliation by hand.

<br>

## What else is refused

**A target id refuses a patch that also installs a full export.** `-app <id>` moves where the TREE lands and can do nothing about an `f<source>.sql` install script, so the two together would write the source application in place while the tree went to the sandbox. Several applications on one target id are refused for the same class of reason: one would land and the other would be dropped behind a correct-looking screen.

**`-force` overrides every refusal here** in the sense it already carries on `patch`, and the log records that it was set whether or not it changed the outcome:

```log
--   OVERRIDDEN       | -force was set; the signature check passed on its own
```

A completed deployment of the same payload to the same target is skipped without `-force` ([patch_deploy.md](patch_deploy.md)). Changing the application target or source invalidates that completion. Retargeted imports verify the application they landed on; a failed verification leaves the deployment incomplete.

<br>

## The row in the deploy table

The import is a SQLcl command, not a file in the patch folder, and its row says so: `> BUILDING APP`. An APEXlang application's install script is two halves around it.

`<SCHEMA>.<APP>.init.sql` carries the commits and files the patch changed, the workspace block, the `apex_init` slot and the `APEXLANG SOURCE:` rows; `<SCHEMA>.<APP>.end.sql` carries the `apex_end` slot and the build status of the application the import landed, and repeats no change list. The schema's own script runs before either:

```text
  FILE                   SCHEMA    BLOCKS   TIMER   STATUS
  --------------------   -------   ------   -----   -----------
  SANDBOX.sql            SANDBOX      2/2      4s   SUCCESS
  SANDBOX.100.init.sql   SANDBOX      1/1      1s   SUCCESS
  > BUILDING APP         SANDBOX               5s   SUCCESS
  SANDBOX.100.end.sql    SANDBOX      1/1      1s   SUCCESS
```

A patch built with `-create -app 100926` names both halves for the target, `SANDBOX.100926.init.sql` and `SANDBOX.100926.end.sql`, so their rows, their SPOOL and their logs say where the tree lands.

The `init` half keeps the source in its header and its source rows. `DEPLOY.sql` names application 100, its tree and its new id between the two lines ([patch_deploy.md](patch_deploy.md)):

```text
PROMPT -- APP ID 100926
PROMPT -- SOURCE APP ID 100
...
PROMPT -- APEXLANG SOURCE: sandbox/apex/100_ORDERS/apexlang
PROMPT -- imported from that folder as application 100926 by patch -deploy -app 100926, not from this patch
```

One target id over a patch carrying two APEXlang applications is refused at `-create` as it is at `-deploy`.

The order is the point. A tree imports onto the objects its pages query, so the schema scripts and the `init` half run first; a failed script leaves the import and the `end` half `NOT RUN` rather than importing over a half-deployed schema, and a refused import leaves the `end` half `NOT RUN`.

The compiler's warnings are repeated in the log, which keeps the application id in its name, `<timestamp>_apex_import_<id>_<STATUS>.log` under the patch's `logs_<ENV>/` folder, and a compile error marks the row `ERROR` with the compiler's own rows under it. A folder built before the split holds one script per application and still deploys, the import following that script.

The sandbox an import created is removed with `-drop`, on [patch_drop.md](patch_drop.md).
