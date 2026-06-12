---
title: adt
created: 2026-06-10
updated: 2026-06-10
tags: [SKILL, DEVOPS]
---
# adt

Operating cheat-sheet for the ADT.ai command line — export Oracle database objects and APEX applications, explore the database safely, query the dependency graph, and build and deploy patches.

## What it is and why you need it

ADT.ai is a Python CLI for Oracle/APEX deployment work. This skill gives an agent the common, correct invocations for every implemented command so it can run real export/inspect/search/deploy workflows without re-reading the full manual each time. It encodes the safety rules that matter in practice: drive `export_db` with `-silent`, never combine `-type JOB` with `-recent`, and reach for read-only `discovery` before any ad-hoc SQL.

## How it works

The skill maps each `adtai` command to a short set of runnable examples plus the rules that keep output clean and safe. Full argument tables stay in the repo's `USAGE.md`, so the skill stays lean and does not drift when flags change. Install and machine health are handled separately by the `adt-setup` skill.

## Usage

Invoke with `/adt`. Full implementation in [SKILL.md](SKILL.md). Argument reference in [USAGE.md](../../USAGE.md).

## Related skills

- [`adt-setup`](../adt-setup/) — install ADT.ai, configure connections/wallets, and run `doctor`.

## Released

_None yet._
