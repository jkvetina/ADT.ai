# ADT.ai Setup

`SETUP.md` is the install and environment reference for humans setting up a machine. It intentionally stays short: use `adtai doctor` to verify the machine, and use [USAGE/doctor.md](USAGE/doctor.md) for the detailed Doctor command contract.

## Install

Install ADT.ai from this checkout:

```bash
python3 -m pip install -e .
```

Windows uses `python` instead of `python3`:

```powershell
python -m pip install -e .
```

This installs both command names:

```bash
adtai --help
adt-ai --help
```

Use `adtai` for normal shell usage.

## Required Tools

Confirm these commands are available on `PATH`:

```bash
python3 --version
git --version
java --version
sql -V
```

On Windows:

```powershell
python --version
git --version
java --version
sql -V
```

Python and Git are required for normal ADT.ai operation. Java and SQLcl are required for SQLcl-backed flows.

## Environment

Set Java output to English so SQLcl and Oracle tooling produce stable messages:

```bash
export JAVA_TOOL_OPTIONS="-Duser.language=en"
```

Windows:

```powershell
setx JAVA_TOOL_OPTIONS "-Duser.language=en"
```

For thick Oracle connections, make Instant Client available.

Optional ADT-compatible defaults:

```bash
export ADT_ENV="DEV"
export ADT_SCHEMA="CORE"
export ADT_KEY="your-password-key"
```

Windows:

```powershell
setx ADT_ENV "DEV"
setx ADT_SCHEMA "CORE"
setx ADT_KEY "your-password-key"
```

`ADT_KEY` is reserved for ADT compatibility and future encrypted-password parity. Current ADT.ai does not decrypt encrypted connection or wallet passwords from it. Doctor redacts non-empty values.

## Connections And Wallets

Keep real connection files and wallet contents out of Git. `adtai doctor -init` creates safe placeholders only:

```bash
adtai doctor -init -root /path/to/project
```

Connection files resolve by first match. The common local locations are:

- `<project>/connections.yaml`
- `<project>/connections/<FOLDER>.yaml`
- this checkout's ignored `connections/<FOLDER>.yaml`

Use config keys such as `connections.path`, `connections.file`, and `connections.wallet_path` when secrets live outside the project repo.

## Verify

Check the current machine setup:

```bash
adtai doctor
```

Plain `adtai doctor` is read-only. It reports versions, environment state, warnings, failures, and available explicit update actions.

Use local checks only when remote metadata calls are not wanted:

```bash
adtai doctor -offline
```

Update actions are intentionally explicit:

```bash
adtai doctor -update
adtai doctor -sqlcl
```

Full Doctor behavior, argument details, output sections, statuses, and project bootstrap rules live in [USAGE/doctor.md](USAGE/doctor.md).
