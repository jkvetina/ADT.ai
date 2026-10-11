"""`patch -deploy -app` over an `ords:` connection (ADT #1071).

Two things were measured on 2026-10-11 (ORDS 26.1, APEX 26.2, SQLcl 26.2)
before this module existed, and both closed the obvious routes:

* SQLcl's own `apex import` compiles the tree and then fails running it over its
  REST connector: `ORA-17004: Invalid column type`.
* `APEX_APPLICATION_INSTALL.INSTALL(p_source => ...)` refuses an APEXlang tree,
  as files, as a zip, with or without a root folder, even fed straight from
  `APEX_EXPORT.GET_APPLICATION`: `Expected either install.sql or application.apx`.

So the tree is compiled here, by SQLcl's own transpiler (`patch/apexlang_blocks`),
and the PL/SQL it produces goes to ORDS as ONE request: the install settings
first, then the blocks, then the stamp every import gets, then a read that the
application is there. One request is one session, which is what the settings
need to reach the blocks.

The whole tree is imported. SQLcl's changed-files import (`-files`, APEX 26.2)
is not available over ORDS, which the card scoped out.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_import import import_stamp
from adt_ai.patch.apexlang_blocks import ApexlangCompileError, transpile_blocks
from adt_ai.patch.queries import (
    ORDS_INSTALL_ALIAS_LINE,
    ORDS_INSTALL_SETTINGS_BLOCK,
    ORDS_INSTALLED_QUERY,
)
from adt_ai.shared.session_scope import batches_in_one_request

# Restricted on REST-enabled SQL (`SP2-0738`, measured 2026-10-11); ORDS goes on
# past a failed statement anyway, and every failure raises (`OrdsGateway`).
_RESTRICTED_LINE = re.compile(r"(?i)^\s*whenever\s+(sqlerror|oserror)\b")


def run_import(gateway: Any, script: str, item: Any, root: Path, *, account: str) -> str:
    """Land ``item``'s tree and return the transcript `parse_import_output` reads.

    A gateway holding a session runs ``script``, SQLcl's `apex import`, exactly
    as before. One that holds none but can batch is an `ords:` connection.
    """
    if not batches_in_one_request(gateway):
        return str(gateway.sqlcl_request(script, root))
    alias = str(item.alias_for_target or "")
    try:
        blocks = transpile_blocks(Path(item.staged), alias=alias)
    except ApexlangCompileError as error:
        # The marker `parse_import_output` reads a compile failure by.
        return f"APEXlang Compile Errors:\n{error}"
    settings = ORDS_INSTALL_SETTINGS_BLOCK.format(alias=ORDS_INSTALL_ALIAS_LINE if alias else "")
    values: dict[str, Any] = {"app_id": item.target_id, **({"alias": alias} if alias else {})}
    stamp = import_stamp(item.target_id, account).rstrip().rstrip("/").rstrip()
    rows = gateway.fetch_all_after(
        [(settings, values), *((block, None) for block in blocks), (stamp, None)],
        ORDS_INSTALLED_QUERY,
        {"app_id": item.target_id},
    )
    if rows and int(next(iter(rows[0].values())) or 0) > 0:
        return f"Import successful: application {item.target_id} installed over ORDS"
    return (
        f"Import over ORDS ran, but application {item.target_id} is not in "
        "apex_applications afterwards."
    )


def run_backup(gateway: Any, script: str, backup: Path) -> str:
    """Run a revert's backup, a full SQL export, back onto the target.

    A gateway holding a session runs ``script`` through SQLcl as before. Over
    ORDS the export is posted whole as one request, because its statements lean
    on the APEX install state the first of them sets, and SQLcl's REST connector
    sends each statement as a session of its own.
    """
    if not batches_in_one_request(gateway):
        return str(gateway.sqlcl_request(script, backup.parent))
    text = backup.read_text(encoding="utf-8")
    kept = "\n".join(line for line in text.splitlines() if not _RESTRICTED_LINE.match(line))
    # Reached only when no statement failed: `run_script` raises on the first.
    return f"{gateway.run_script(kept)}\nSUCCESS"


__all__ = ["ApexlangCompileError", "run_backup", "run_import"]
