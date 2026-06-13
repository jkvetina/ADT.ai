# Changelog — adt-setup

## Unreleased

- Updated command-alias guidance from `adt-ai` to `adt` and added explicit `DPI-1047` / `libclntsh.dylib` troubleshooting for missing Instant Client.
- Aligned setup guidance with current ADT.ai CLI: project bootstrap now uses `doctor -init`, standalone update/upgrade names are described as generic error-screen guidance, and `ADT_KEY` is no longer described as active decryption support.
- Initial ADT.ai setup skill. Covers `pip install -e .`, PATH prerequisites, environment variables (`JAVA_TOOL_OPTIONS`, `ORACLE_HOME`, `ADT_ENV`/`ADT_SCHEMA`/`ADT_KEY`), connection/wallet resolution, and project bootstrap through `doctor -init`.
- `adtai doctor` documented as the verification and update centerpiece: read-only default, `-offline`, `-update`, and `-sqlcl`.
- Troubleshooting section maps common failures to the doctor row that flags them.
