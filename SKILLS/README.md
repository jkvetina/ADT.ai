# ADT.ai Skills

ADT.ai ships two repo-local agent skills. The `adt` skill is explicit-only: expose it when needed, but never auto-load it for repository work. Use `adt-setup` only when setting up the tool, checking prerequisites, or troubleshooting the local environment.

## Which Skill To Use

- [`adt`](adt/SKILL.md) is the explicit-only, lean command router. It selects the relevant `adtai` command, loads only that command's documentation, and preserves the safety boundaries an agent cannot infer from `--help` alone; AGENTS.md governs repository work instead.
- [`adt-setup`](adt-setup/SKILL.md) is the setup checklist. Use it for initial install, Doctor checks, SQLcl/Python prerequisites, project bootstrap, and repair work.

For shorter human summaries, see [`adt/README.md`](adt/README.md) and [`adt-setup/README.md`](adt-setup/README.md).
