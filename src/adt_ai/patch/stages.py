"""The two halves of an APEXlang application's install script (ADT #735).

Split out of `settings.py` when that module crossed the context-size cap; the
helpers are read by the plan order, the script writer, the report and the
`-continue` baseline, none of which need the rest of the settings surface.
"""

from __future__ import annotations

import re

#: An APEXlang application installs through SQLcl's `apex import`, which is a
#: command `patch -deploy -app` issues and never a file in the patch, so the
#: application's script is written as two (ADT #735): `<SCHEMA>.<APP>.init.sql`
#: runs before the import and `<SCHEMA>.<APP>.end.sql` after it. One script
#: around an import it does not contain read as an empty shell installing
#: nothing, which is how Jan read `<SCHEMA>.1000.sql` on 2026-09-07.
APP_SCRIPT_INIT = "init"
APP_SCRIPT_END = "end"
APP_SCRIPT_STAGES = (APP_SCRIPT_INIT, APP_SCRIPT_END)


def staged_group(group: str, stage: str) -> str:
    """`APP.100` + `init` -> `APP.100.init`, the group one half is named by."""
    return f"{group}.{stage}"


def split_stage(group: str) -> tuple[str, str | None]:
    """`APP.100.init` -> (`APP.100`, `init`); a group carrying no stage comes back whole.

    Only an application group splits, `<SCHEMA>.<digits>.<stage>`, so a schema
    whose own name happens to end in `.end` cannot be read as a half of
    something. The reverse of `staged_group`, kept beside it for the reason
    `group_from_script_name` sits beside `group_script_name`.
    """
    head, dot, stage = group.rpartition(".")
    if dot and stage in APP_SCRIPT_STAGES and head.rpartition(".")[2].isdigit():
        return head, stage
    return group, None


#: The header row a retargeted APEXlang script carries (ADT #935). Its file, its
#: SPOOL and its `APP ID` row name the id `-create -app <id>` lands the tree on,
#: so this row is where `-deploy` reads back the application whose tree it
#: imports. Jan, 2026-09-24: `-app 1226000` deployed application 122 as 1226000
#: and nothing in the patch said so.
SOURCE_APP_ROW = "PROMPT -- SOURCE APP ID "
_SOURCE_APP_RE = re.compile(rf"^{re.escape(SOURCE_APP_ROW)}(\d+)$", re.MULTILINE)


def retarget(group: str, target_id: int | None) -> str:
    """`APP.100.init` + `100926` -> `APP.100926.init`; any other group comes back whole.

    Only an application group moves, staged or not, and only onto a different
    id: no target, or the application's own id, is no retarget at all.
    """
    head, stage = split_stage(group)
    schema, dot, app = head.rpartition(".")
    if target_id is None or not dot or not app.isdigit() or int(app) == target_id:
        return group
    moved = f"{schema}.{target_id}"
    return staged_group(moved, stage) if stage else moved


def source_app_id(text: str) -> int | None:
    """The application a retargeted script's `SOURCE APP ID` row names, else ``None``."""
    match = _SOURCE_APP_RE.search(text)
    return int(match.group(1)) if match else None
