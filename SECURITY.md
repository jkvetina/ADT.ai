# Security Policy

ADT.ai connects to Oracle databases, reads credentials and wallets from disk, and drives SQLcl as a child process. A defect in any of those paths is a security defect, not a bug, so it has its own reporting route.

## Supported versions

| Version | Supported |
| --- | --- |
| 0.9.x | Yes |
| Older | No |

One release line is supported: the latest published one. Fixes land in a new release rather than as a patch to an older tag, because there is no separate maintenance branch to put one on.

## Reporting a vulnerability

Report it privately through GitHub, never as a public issue:

**<https://github.com/jkvetina/ADT.ai/security/advisories/new>**

That form authenticates you, keeps the report out of the public tracker, and opens a draft advisory in the same step, so the fix and the disclosure can be prepared together.

Please include:

- the ADT.ai version (`adtai --version`) and how it was installed,
- the operating system and Python version,
- what an attacker gains, and the smallest input or configuration that shows it,
- any output you can share once secrets are removed.

A public issue that describes a vulnerability discloses it by being filed. If you have already opened one, say so in the advisory and it will be handled from there.

## What happens next

Reports are read and answered. No response deadline is promised here, because this is a single-maintainer project and a stated deadline nobody staffs is worse than an honest silence about timing. Accepted reports are fixed in the next release and credited in the advisory unless you ask otherwise.

## Scope

In scope:

- credential, wallet and secret handling, including anything that writes a secret to a log, a console line or an exception,
- the SQLcl and Git subprocess boundaries, and the environment those children inherit,
- files the tool writes: exported objects, patch scripts, the Doctor scaffold, and any path built from user input,
- the published wheel and source distribution, and the workflows that build them.

Out of scope:

- vulnerabilities in Oracle Database, APEX or SQLcl themselves; report those to Oracle,
- misconfiguration of a database, a wallet or a network that ADT.ai merely connects through,
- findings that require an attacker to already control the machine running the tool.

## Hardening notes for operators

- Connection files and wallets under `connections/` are runtime secrets and are gitignored for that reason. Keep them out of the repository you deploy from.
- Encrypt stored passwords, or supply them from a secret command, rather than leaving plaintext on disk. See `docs/connection_security.md`.
- Run the tool as a user that owns only the schemas it needs. ADT.ai never requires DBA.
