# Dropping Sandbox Applications (adtai patch -drop)

What `-drop` takes, the rail that decides an id is droppable, and what the run prints. Landing a tree on a sandbox id in the first place is on [patch_import.md](patch_import.md).

## Why the step exists

`patch -deploy -app <id>` lands an application's APEXlang tree on a derived sandbox id: application `100` on task `123` imports as `100123`, beside the real one rather than over it. Nothing overwrites anything, which is what makes the sandbox safe to test against and also what leaves it standing once the work is done.

So the round trip owes a drop step. Task `124` lands on `100124` beside task `123`'s `100123`, and without a way to remove them the workspace fills with dead copies of the application they were copied from.

## What a run takes

Ids, and nothing else. There is no `-name` and no patch folder, because removing a sandbox is the step after the work has landed exactly as archiving a patch folder is:

```bash
adtai patch -target DEV -drop 100123
adtai patch -target DEV -drop 100123 100124
```

`-target` is required. A destructive action against whichever environment the connection file happens to default to is the mistake a rail exists to prevent.

The run connects as the sandbox's source application's own parsing schema, read from `config/internal/apex.db`, which is the same schema an APEX install script deploys through. `schema_apex` is the fallback for an application the store has no row for. It reports what it removed:

```text
APEX APPLICATIONS:
------------------

  APPLICATION   ALIAS        SOURCE   STATUS
  -----------   ----------   ------   -----------
       100123   ORDERS_123      100   DELETED
```

`SOURCE` is the application the sandbox was derived from, which is what the rail below checked before anything was removed.

**The rows appear as the run works, not once it has finished.** Each application is on screen with its id, alias and source before the call that removes it, and its `STATUS` completes that same line when the drop answers, so a run naming ten ids shows you which one it is on rather than a header over an empty section. On a terminal the open row reads `IN PROGRESS` until the drop returns, which is why `STATUS` reserves eleven characters on every run; a redirected run and a CI log print exactly one finished line per application, unchanged.

## Where the receipt goes

Every application named in a drop run writes its own durable receipt below the resolved APEX export root:

```text
<path_apex>/logs_<ENV>/<timestamp>_apex_drop_<application-id>_<DELETED|FAILED>.log
```

For `adtai patch -target DEV -drop 100123`, a default-layout example is `sandbox/apex/logs_DEV/20260901-120000_apex_drop_100123_DELETED.log`. `DEV` comes from `-target`; `100123` is the application id passed to `-drop`, not an environment name.

The log records the environment, source application, target application, alias, recorded creator, dictionary-verified outcome, and SQLcl transcript, plus an `OVERRIDDEN` row when `-force` dropped a sandbox that was not yours. A run naming two applications writes two logs. `FAILED` means the target still existed in `apex_applications` after SQLcl returned.

## The rail, and why `-force` never widens it

An id is droppable only when it is a **derived sandbox id**, which is two facts rather than one:

| The check | What it rules out |
| --------- | ----------------- |
| `<application><task>`, where the application is one this workspace holds and the task is a positive number | an id nobody derived, and an application's own id, which is never a strict extension of itself |
| the alias is the derived `<SOURCE_ALIAS>_<task>` | a real application that merely happens to start with another application's number |

So `-drop 100` refuses and names what actually sits at that id, and an id the schema cannot see refuses rather than exiting quietly as though it had removed something.

**The first check is exact, so an id the derivation cannot produce is not a derived id however much it looks like one.** Application `100` on task `123` derives `100123` and nothing else, so `1000123` is refused even when the application sitting there carries the alias `ORDERS_123` that pair would have written. Reading it as task `123` was a leading zero being stripped, and it made the rail clearable by an application whose number merely resembles a sandbox's. This is the shape the drop is documented on: the id is derived, never chosen, so an application landed on some other number is removed in the Builder rather than by `-drop`.

The source has to sit in the same workspace as the target. An APEX alias is unique per workspace, so the derivation only holds inside one, and matching across the boundary would let an application in one workspace authorize removing an application in another.

**`-force` never reaches the rail.** Everywhere else on `patch` that flag overrides a refusal, and here the rail is the whole safety property: a destructive drop a flag can widen has no rail at all. What the flag overrides is the ownership check below.

Every id named in one run is checked before the first one is removed, so a run naming one sandbox and one production id removes neither.

## The ownership check, and what `-force` overrides

A sandbox is yours to drop when the creator APEX recorded for it is you. The run reads `created_by` live off the target's `apex_applications` row and compares it, ignoring case, with `apex_account` in your `config/IDENTITY.yaml` ([config.md](config.md)), falling back to `git config user.name` when the file names none.

A mismatch stops the run with both names on the screen:

```text
PATCH FAILED:
-------------
  APP 100124 (ORDERS_124) was created by ALEX.RIVERA, and config/IDENTITY.yaml apex_account says SAM.TAYLOR, so it is not yours to drop.
  Run: adtai patch -target DEV -drop 100124 -force drops somebody else's sandbox anyway
```

```bash
adtai patch -target DEV -drop 100124 -force
```

`-force` drops it anyway, and the receipt records that it did: the log carries a `CREATED BY` row on every drop, and an `OVERRIDDEN` row whenever the flag actually stepped over a refusal, naming the creator it stepped over. A forced drop that needed no flag carries no such row.

**A sandbox recording no creator drops without `-force`.** Measured on APEX 26.1: an APEXlang `apex import`, which is what `patch -deploy -app` runs through SQLcl, leaves `created_by` empty, a session user set beforehand does not change that, and APEX exposes no `p_created_by` at all on its flow-level import API. So an application imported from an export taken without audit columns can never carry the value this check reads, and the check lets it through rather than making the destructive override the routine way to remove an ordinary sandbox.

Nobody is stepped over by removing what nobody is recorded as having made, and the rail above has already proved the target is a derived sandbox, which is the safety property. A sandbox that does record a creator is still compared, so somebody else's still needs the flag. The check reads what APEX wrote and invents nothing.

## How the application is removed

SQLcl's `apex` command has no drop verb, so the transport is the one every legacy full export already runs before it imports: `wwv_flow_imp.import_begin` for the target, then `wwv_flow_imp.remove_flow`, then `import_end` and a commit. It runs as the parsing schema, `import_begin` having set the workspace context, so no instance-administrator grant is involved.

The version and release it declares are read live off `apex_release`, and the workspace id and owner off the target's own `apex_applications` row.

The outcome in the table is read back from `apex_applications` rather than parsed out of the SQLcl transcript: the question the row answers is whether the application is gone, and only the dictionary can say so. An application still standing afterwards is reported `FAILED` and the run exits non-zero.

A SQLcl failure part way through a multi-id run finds the applications already removed on screen with their `DELETED` rows, and completes the failing row with `FAILED` before the error screen. The ids behind it are left untouched: a run that has met an unexplained refusal stops removing applications rather than working down the list.
