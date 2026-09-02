# Contributing

Thank you for wanting to help. Read the first section before you write any code, because it changes what a useful contribution looks like here.

## This repository is generated

The public `jkvetina/ADT.ai` tree is built from a private development repository and rewritten in full on every release. A pull request against it would be overwritten by the next build, and no amount of review can stop that, so pull requests are not the way in.

What reaches the project instead:

- **Issues.** A precise bug report is the most valuable thing you can send. See below for what makes one actionable.
- **Feature requests.** Say what you are trying to do, not only the flag you want; the shape of the answer is often different from the shape of the request.
- **Patches as issue attachments.** If you have a fix, open an issue and paste it there, or describe the change. It will be applied on the private side with credit in the changelog.
- **Security reports.** Never as an issue. See [SECURITY.md](SECURITY.md).

## What makes a bug report actionable

The tool talks to a database, so almost every report needs three things before anyone can act on it:

1. **The exact command**, with flags, as you ran it. Add `-debug` output when the failure is not obvious.
2. **The versions**: `adtai --version`, your Python version, your operating system, and the Oracle Database and APEX versions when the failure involves either.
3. **What you expected instead.** A console line that surprised you is a report; a console line with the line you expected beside it is a fix.

Redact schema names, hostnames and anything else you would not publish. A redacted transcript is more useful than no transcript.

## Running it locally

`SETUP.md` covers installing the tool, and `adtai doctor` checks a machine and explains what it found. `docs/README.md` is the command reference: every command has a page with its arguments and worked examples.

## Conduct

Participation is covered by the [Code of Conduct](CODE_OF_CONDUCT.md).

## Licence

The project is MIT licensed. Anything you contribute is contributed under those terms.
