"""`DEPLOY.sql`, the deploy order a multi-script patch states in its own folder (ADT #850).

A patch folder holding two or more install scripts gets one, written by
`-create` in the order `-deploy` has always run them (`deploy._deployment_order_key`):
schema scripts first, then each application's `init` half, then its `end` half.
It is a plain SQLcl driver, one `@"./<SCRIPT>.sql"` line per script, so a person
running the patch by hand runs it in the same order, and it is the one place a
person changes that order.

A re-create keeps the order a person gave it for every script that is still
generated, drops the scripts that are gone and appends the new ones in the
default order; `-force` writes the default order again. `-deploy` runs the `@`
lines, and refuses before the first script when they and the folder disagree,
because a script the driver does not name is a script nobody decided where to
run.

A retargeted APEXlang application gets a comment between its `init` and `end`
lines naming the application, its tree and the id the import lands it on
(ADT #935). It is a `--` line, so the drift check never reads it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from adt_ai.patch import stages
from adt_ai.patch.create_apex import APEXLANG_SOURCE_ROW
from adt_ai.patch.deploy import _deployment_app_id, _deployment_order_key, _deployment_stage
from adt_ai.patch.models import PatchError
from adt_ai.shared import text_files
from adt_ai.shared.patch_folders import DEPLOY_DRIVER, install_scripts

# Three lines since ADT #935; the six that explained the rules are on
# `docs/patch_deploy.md`. A driver still carrying them reads the same, because
# every `--` line is a comment to `_read`, and a re-create writes this one.
HEADER = (
    "--",
    "-- PATCH DEPLOY ORDER",
    "--",
    "",
)

# `@"./X.sql"`, and the spellings a person types by hand: no quotes, no `./`, a
# trailing `;`. A path into a subfolder is not an install script of this folder.
_SCRIPT_LINE_RE = re.compile(r'^@\s*"?(?:\./)?(?P<name>[^"/\\;\s]+\.sql)"?\s*;?$', re.IGNORECASE)


def default_order(names: list[str], config: dict[str, Any]) -> list[str]:
    return sorted(names, key=lambda name: _deployment_order_key(name, config))


def _read(driver: Path) -> tuple[list[str], list[str]]:
    """The script names the driver lists, in order, and every line that is not one."""
    listed: list[str] = []
    stray: list[str] = []
    for line in driver.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _SCRIPT_LINE_RE.match(stripped)
        if match is None:
            stray.append(stripped)
        else:
            listed.append(match.group("name"))
    return listed, stray


def write_deploy_driver(folder: Path, config: dict[str, Any], *, force: bool) -> Path | None:
    """Write `DEPLOY.sql` for a folder of two or more scripts, or remove a stale one."""
    driver = folder / DEPLOY_DRIVER
    names = [path.name for path in install_scripts(folder)]
    if len(names) < 2:
        driver.unlink(missing_ok=True)
        return None
    ordered = default_order(names, config)
    if driver.is_file() and not force:
        listed, _stray = _read(driver)
        kept = list(dict.fromkeys(name for name in listed if name in names))
        ordered = [*kept, *(name for name in ordered if name not in kept)]
    lines = list(HEADER)
    for name in ordered:
        lines.append(f'@"./{name}"')
        lines.extend(_import_comment(folder / name, config))
    text_files.write_text(driver, "\n".join([*lines, ""]))
    return driver


def _import_comment(script: Path, config: dict[str, Any]) -> list[str]:
    """What happens after a retargeted application's `init` line, said in the file (ADT #935).

    The import is a SQLcl command `-deploy` issues, never a line of this file,
    so without this the driver read as two scripts back to back. Jan,
    2026-09-24: *"I explicitly asked you to create a comment in between init and
    end, so it is clearly visible what you will be doing"*. Only a retargeted
    `init` half earns one: read off its own `SOURCE APP ID` and `APEXLANG
    SOURCE:` rows, so a re-create that keeps the order writes it again.
    """
    if _deployment_stage(script.name, config) != stages.APP_SCRIPT_INIT:
        return []
    text = script.read_text(encoding="utf-8", errors="replace")
    source = stages.source_app_id(text)
    if source is None:
        return []
    target = _deployment_app_id(script.name, config)
    trees = ", ".join(
        line.removeprefix(APEXLANG_SOURCE_ROW)
        for line in text.splitlines()
        if line.startswith(APEXLANG_SOURCE_ROW)
    )
    return [
        "",
        "--",
        "-- APEX APP IMPORT HERE:",
        "--",
        f"-- patch -deploy -app {target} imports the APEXlang application {source} as {target}",
        f"-- from {trees} on the fly.",
        "-- Running this file by hand does not import it!",
        "--",
        "",
    ]


def deploy_order(folder: Path, config: dict[str, Any]) -> list[Path]:
    """The install scripts in the order they deploy: `DEPLOY.sql`'s, else the default."""
    scripts = {path.name: path for path in install_scripts(folder)}
    driver = folder / DEPLOY_DRIVER
    if not driver.is_file():
        return [scripts[name] for name in default_order(list(scripts), config)]
    listed, stray = _read(driver)
    problems: list[str] = []
    if missing := sorted(set(scripts) - set(listed)):
        problems.append("Not listed: " + ", ".join(missing))
    if unknown := sorted({name for name in listed if name not in scripts}):
        problems.append("No such script: " + ", ".join(unknown))
    if twice := sorted({name for name in listed if listed.count(name) > 1}):
        problems.append("Listed twice: " + ", ".join(twice))
    if stray:
        problems.append("Not a script line: " + ", ".join(stray))
    if problems:
        # A short uppercase headline, one problem per line under it (ADT #934).
        raise PatchError(
            f"{DEPLOY_DRIVER} DOES NOT MATCH PATCH FOLDER {folder.name}\n\n"
            + "\n".join(problems)
            + "\n\n1) fix its @ lines\n2) or rebuild it with -create -force"
        )
    return [scripts[name] for name in listed]


__all__ = [
    "DEPLOY_DRIVER",
    "HEADER",
    "default_order",
    "deploy_order",
    "install_scripts",
    "write_deploy_driver",
]
