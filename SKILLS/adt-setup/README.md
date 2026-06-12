---
title: adt-setup
created: 2026-06-10
updated: 2026-06-10
tags: [SKILL, DEVOPS]
---
# adt-setup

Install ADT.ai on a machine, wire up the runtime environment and connections, and verify everything with `adtai doctor`.

## What it is and why you need it

Getting ADT.ai running takes more than `pip install` — it needs Python/Git/Java/SQLcl on `PATH`, the right environment variables for stable Oracle output, Instant Client for thick connections, and connection/wallet files in the right place. This skill is the ordered checklist for that, and it makes `adtai doctor` the single command for verifying setup and applying updates.

## How it works

The skill walks install → PATH prerequisites → environment variables → connections/wallets → `doctor`. The `doctor` section is the centerpiece: read-only by default, with `-offline` for local-only diagnostics, `-update` for the full toolchain upgrade, and `-sqlcl` for SQLcl alone. Troubleshooting tips map common failures back to the doctor row that flags them.

## Usage

Invoke with `/adt-setup`. Full implementation in [SKILL.md](SKILL.md). Setup reference in [SETUP.md](../../SETUP.md).

## Related skills

- [`adt`](../adt/) — day-to-day ADT.ai command usage (export, discovery, patch).

## Released

_None yet._
