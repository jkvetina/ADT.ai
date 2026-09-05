# Importing an APEXlang Application (adtai patch -deploy -app)

What `-app` on a `-deploy` run does with the application's committed `apexlang/` tree: which id it lands on, where it is staged, the signatures the log records, and what is refused before the first install script runs. The command and its flags are on [patch.md](patch.md); the loop from export to promotion is on apex_round_trip.md.

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

## Where the tree lands

**The value is where the tree LANDS, not which applications ship.** Bare `-app` changes no application id. `-app <id>` installs the same tree on that id, with the alias derived in the same step, since an APEX alias is unique per workspace and a copied id keeping the source alias collides with the application it came from. One id per run: several applications cannot fold onto one id, and a second value is refused rather than reduced to the first.

The sandbox id is derived, never configured per developer: the application number carrying the task number, so application `100` under task `601` is `100601` and its alias `ORDERS_601`. Uniqueness comes from the task number, which is already unique across developers.

Retargeting is a flag on SQLcl's `apex import`, never an edit to the tree's `deployments/default.json`, which is what lets a promote install the byte-identical tree a sandbox import validated.

## A sandbox is stamped with the developer who deployed it

An APEXlang import writes no audit column, so a sandbox lands owned by nobody and the Builder shows it that way. The import session therefore reads the application's own version and hands it straight back through `SET_APPLICATION_VERSION`, which is what makes APEX stamp the row.

One call, and the value it passes is identical: the API writes the audit columns whether or not the version changed. The version text never moves. `apex_applications.last_updated_by` then names you and `last_updated_on` the moment, so your sandbox is yours on sight in the Builder's application list.

The name is `apex_account` from [`IDENTITY.yaml`](config.md), else `git config user.name`. A checkout naming no developer stamps nobody and imports exactly as before.

Only a retarget is stamped. A bare `-app` lands each application under its own id, and rewriting a real application's audit row would erase who last worked it in the Builder.

`created_by` stays empty either way, because APEX exposes nothing that can write it, which is why [`patch -drop`](patch_drop.md) clears an ownerless sandbox rather than refusing one.

## The tree is staged, never imported where it sits

`export_apex -apexlang` omits the static-file payloads by design, and `apex import` validates before it writes, so an unstaged tree fails one `REFERENCE_NOT_FOUND` per payload.

The run therefore hardlinks `apexlang/` plus its sibling `files/` export into `config/temp/apexlang/<app>/`, the same staging tree [`validate`](validate.md) builds, so the bytes the import sees are the bytes the compile gate passed.

An application the patch ships and nobody exported a tree for is a `NOTES:` row naming the export that would fix it, never a refusal: the patch may legitimately carry an application this run was not asked to import.

## Three signatures, read before anything is written

`LATEST ON TARGET` is the live checksum of the application about to be written, `CHANGE BASED ON` the one `export_apex` recorded when it wrote the tree, and `DEPLOYING` a content hash of the tree on disk.

The first two are APEX's own `CHECKSUM-SH256`, independent of ids and comparable across instances; the third opens `TREE:` because it answers a different question, what is on disk right now rather than what a live application looks like.

```log
-- APEX APPLICATION 100 IMPORTED AS 100601
--   LATEST ON TARGET | (no application)
--   CHANGE BASED ON  | SH256:795mkyqBRAN1UkZCYSV6l3ntA3JyqzBP8fmKN6LOT7k=
--   DEPLOYING        | TREE:785c6726ff679a37894c1eb3157b4f9862e797b5
--   DEPLOYED FROM    | sandbox/apex/100_ORDERS/apexlang
```

`DEPLOYED FROM` names the folder the application was read out of. The patch carries no copy of an APEXlang tree ([patch_content.md](patch_content.md)), so the log is the one place a reader finds where the bytes came from.

**The target moving is a showstopper.** When the first two disagree, somebody changed the application after the tree was exported and an import would overwrite work this patch never saw, so the deploy refuses before its first install script and names the re-export that clears it. An application with no recorded signature refuses the same way: the run cannot say what the change was based on. An id nothing is installed on yet, the ordinary first sandbox import, is not drift and passes.

## What else is refused

**A target id refuses a patch that also installs a full export.** `-app <id>` moves where the TREE lands and can do nothing about an `f<source>.sql` install script, so the two together would write the source application in place while the tree went to the sandbox. Several applications on one target id are refused for the same class of reason: one would land and the other would be dropped behind a correct-looking screen.

**`-force` overrides every refusal here** in the sense it already carries on `patch`, and the log records that it was set whether or not it changed the outcome:

```log
--   OVERRIDDEN       | -force was set; the signature check passed on its own
```

A completed deployment of the same payload to the same target is skipped without `-force` ([patch_deploy.md](patch_deploy.md)). Changing the application target or source invalidates that completion. Retargeted imports verify the application they landed on; a failed verification leaves the deployment incomplete.

## The row in the deploy table

The import is a row like any install script, named `apex_import_<id>` and carrying a log of its own under the patch's `logs_<ENV>/` folder:

```text
  FILE                 SCHEMA    FILES   TIMER   STATUS
  ------------------   -------   -----   -----   -----------
  SANDBOX.100.sql      SANDBOX              2s   SUCCESS
  apex_import_100601   SANDBOX              5s   SUCCESS
```

It runs after the install scripts, never before: a tree imports onto the objects its pages query, so a failed script leaves it `NOT RUN` rather than importing over a half-deployed schema. The compiler's warnings are repeated in the log, and a compile error marks the row `ERROR` with the compiler's own rows under it.

The sandbox an import created is removed with `-drop`, on [patch_drop.md](patch_drop.md).
