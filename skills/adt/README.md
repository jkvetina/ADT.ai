---
title: adt
created: 2026-06-10
updated: 2026-09-01 11:44
tags: [SKILL, DEVOPS]
---
# adt

Lean, agent-neutral router for operating the ADT.ai command line in Oracle and APEX projects.

## What it is and why you need it

ADT.ai is a Python CLI for Oracle/APEX deployment work. This skill helps Codex, Claude, Copilot, and similar agents select the correct command, then sends them to only that command's authoritative documentation. It keeps the few operational and safety rules that materially change what an agent should do.

## How it works

The entrypoint is intentionally small: one section per command, a documentation link, and a starting command shape. Full behavior, flags, and output stay in `docs/`, which remains the source of truth. Install and machine health are handled separately by the `adt-setup` skill.

## Usage

This skill is explicit-only. Invoke it through the agent's normal skill mechanism or request the ADT skill by name; agents must not auto-load it for ADT.ai repository work. Repository work follows AGENTS.md, which loads the project SOP and DOD directly. Full implementation is in [SKILL.md](SKILL.md), and command documentation starts at [docs/README.md](../../docs/README.md).

## Related skills

- [`adt-setup`](../adt-setup/), install ADT.ai, configure connections/wallets, and run `doctor`.

## Released

_None yet._
