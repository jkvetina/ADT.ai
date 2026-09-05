"""Every patch value a project answers for, read from `config.yaml` in one place.

`layout.py` resolves where an exported OBJECT sits, from `path_objects` and
`path_apex`. This resolves everything the PATCH itself is made of: which folders
it writes, what its generated scripts are called, which directives open them, and
which commits and files never enter one at all.

Two cards land here. ADT #430 carried over the seven old-ADT keys the rewrite had
spelled as literals; ADT #431 exposed the behaviours neither tool ever made
configurable. Splitting them across two modules would have put `patch_root` and
`patch_group_file` in different files for no reason a reader could see, since both
answer the same question: what does this project call the thing.

Three properties are deliberate and worth keeping:

* **Every default here is the literal it replaced**, so a project that sets
  nothing gets byte-identical output. That is also the trap the ADT.ai SOP names,
  a shipped default matching the literal is what hides an unread key, so the
  tests spell non-default values throughout.
* **Nothing here touches the filesystem.** Each function is a pure reading of a
  dict, which is what lets the whole surface be tested without a fixture tree.
  `patch_root` takes the root as an argument rather than discovering one.
* **A writer and its reader are derived from the same template.** The patch
  folder name is the worked example: `patch_folder_name` builds it and
  `patch_folder_re` parses it back, from one config value, because they disagreed
  once on `patch_scripts_dir` and every generated helper went unlinked (ADT #18).

Three rows were decided the other way and are recorded on their cards rather than
built, because a key nothing can act on is the accepted-but-unused setting SOP
§Command surface forbids:

* **`patch_skip_merge`** (#430) has no successor because it needs none. #309
  measured that ADT.ai scans with `git diff-tree` and no `-m`, so a merge commit
  carries no files at all and can never be the authoritative commit for one. The
  premise the key corrects does not hold here, and the pin that would reopen it is
  `tests/patch/test_commit_filters.py::test_a_merge_commit_carries_no_files_in_the_scan`.
* **The hash baseline's filename** (#431) stays fixed, settled by Jan under #453:
  `patch_hashes` already places the folder and `-hash FILE` spells a whole
  address, so a third knob only adds a way to disagree with itself.
* **The dependency graph path** (#431) stays `config/internal/dependencies.db`,
  ADT-owned and gitignored, centralized by `shared/internal_paths.py` precisely so
  its six readers, the `doctor -init` gitignore scaffold and the migration cannot
  drift apart. `repo_commits_file` is configurable for the opposite reason, a
  per-branch store is a file the user prunes.
"""

from __future__ import annotations

import fnmatch
import re
import shutil
from datetime import date, datetime
from pathlib import Path
from typing import Any

#: Every key this module reads, with the value ADT.ai ships. A project setting
#: none of them gets exactly what it got before these keys existed. Kept as one
#: map so `config.yaml`, the docs and the tests have a single thing to agree with.
DEFAULTS: dict[str, Any] = {
    "apex_files_ignore": [
        "/application/set_environment.sql",
        "/application/end_environment.sql",
        "/application/create_application.sql",
        "/application/delete_application.sql",
        "/application/pages/delete_*.sql",
        "/install.sql",
        "/install_component.sql",
    ],
    "apex_snapshots": "snapshots/",
    "patch_archive_format": "zip",
    "patch_archive_subfolder": "%Y-%m",
    "patch_deploy_log_file": "{$TIMESTAMP}_{$SCHEMA}_{$STATUS}.log",
    "patch_folder": "{$TODAY_PATCH}-#PATCH_SEQ#-#PATCH_CODE#",
    "patch_folder_splitter": "-",
    "patch_group_file": "#GROUP#.sql",
    "patch_harden": True,
    "patch_install_file": "INSTALL.sql",
    "patch_postfix_after": "_after",
    "patch_postfix_before": "_before",
    "patch_root": "patch/",
    "patch_rollback": True,
    "patch_scripts_snap": "patch_scripts/",
    "patch_session_directives": ["SET DEFINE OFF", "SET TIMING OFF", "SET SQLBLANKLINES ON"],
    "patch_spool_line": 'SPOOL "./{$FOLDER}/{$SCHEMA}.log" APPEND;',
    "deploy_verify_scan": True,
    "today_deploy": "%Y%m%d-%H%M%S",
    "today_patch": "%y%m%d",
}

#: The scan log's name, deliberately NOT a config key and deliberately not
#: `.log`. `shared/deploy_status.DEPLOY_LOG_RE` reads every
#: `<stamp>_<stem>_<SUCCESS|ERROR>.log` for the latest script outcome. A scan
#: report is separate from that display; whole-run completion also requires
#: the verification result recorded in the target's deployment receipt.
APEX_SCAN_LOG_FILE = "{$TIMESTAMP}_apex_scan_{$APP}.txt"


def _text(config: dict[str, Any], key: str) -> str:
    """The configured string, or the shipped default when it is blank.

    Blank counts as unset on purpose: a key commented out to "turn it off" is the
    commonest way a project asks for the default, and an empty folder name would
    otherwise write into the patch root itself.
    """
    value = str(config.get(key) or "").strip()
    return value or str(DEFAULTS[key])


def _flag(config: dict[str, Any], key: str) -> bool:
    value = config.get(key)
    return bool(DEFAULTS[key]) if value is None else bool(value)


# --- folders -----------------------------------------------------------------


def snapshots_folder(config: dict[str, Any]) -> str:
    """`apex_snapshots`: the folder inside a patch holding the copied files.

    Named for APEX in old ADT (config.yaml:221) and never only about APEX: every
    snapshot a patch takes lands here, database objects included. The name is
    kept for parity with the projects already carrying it.
    """
    return _text(config, "apex_snapshots").strip("/")


def patch_root(root: Path, config: dict[str, Any]) -> Path:
    """`patch_root`: the folder holding live patches, `patch/` by default."""
    return root / Path(_text(config, "patch_root").strip("/"))


def scripts_snap_folder(config: dict[str, Any]) -> str:
    """`patch_scripts_snap`: where moved per-patch scripts land inside the patch.

    What ADT #309 dropped on purpose was the `<CODE>` level *inside* this folder,
    the patch folder's own name already carries the code, so repeating it would
    nest a directory answering a question nobody asked. The folder NAME was never
    the reason, so the key comes back.
    """
    return _text(config, "patch_scripts_snap").strip("/")


# --- slot names --------------------------------------------------------------


def slot_name(group: str, timing: str, config: dict[str, Any]) -> str:
    """`<group><patch_postfix_before|after>`, the template and script slot names.

    One function for both timings so a project cannot end up with a `before` slot
    reading from config and an `after` slot reading a literal.
    """
    key = "patch_postfix_before" if timing == "before" else "patch_postfix_after"
    return f"{group}{_text(config, key)}"


# --- the patch folder's own name ---------------------------------------------

#: The tokens `patch_folder` may carry, in both spellings ADT accepts. Old ADT
#: mixed them in one value (`'{$INFO_SCHEMA}{$TODAY_PATCH}-#PATCH_SEQ#-#PATCH_CODE#'`),
#: so both are read here rather than making a migrating project pick one.
_FOLDER_TOKENS = {
    "day": ("{$TODAY_PATCH}", "#TODAY_PATCH#"),
    "sequence": ("{$PATCH_SEQ}", "#PATCH_SEQ#"),
    "code": ("{$PATCH_CODE}", "#PATCH_CODE#"),
}

#: What each token matches when the name is read back. The code is greedy on
#: purpose: `260822-3-A-B` is code `A-B`, which is what `PATCH_FOLDER_RE` has
#: always answered, and a patch code carrying the separator is legal.
_FOLDER_PATTERNS = {"day": r"\d+", "sequence": r"\d+", "code": r".+"}

_ANY_TOKEN_RE = re.compile(
    "|".join(
        re.escape(token)
        for tokens in _FOLDER_TOKENS.values()
        for token in tokens
    )
)


def today_patch_format(config: dict[str, Any]) -> str:
    """`today_patch`: the `strftime` format the folder name stamps the day with."""
    return _text(config, "today_patch")


def patch_folder_name(
    config: dict[str, Any],
    *,
    day: date,
    sequence: int,
    code: str,
) -> str:
    """The folder one patch is built into, `260822-3-ADT_430` by default.

    Reads the SAME resolved template `patch_folder_re` parses, never the raw
    `patch_folder` value. Reading the raw one shipped in the first cut of ADT
    #430 and the unit tests missed it, each half being correct on its own: the
    writer ignored `patch_folder_splitter` while the reader honoured it, so a
    project setting only the splitter wrote `20260822-1-CODE` and then matched it
    against a pattern expecting dots. The live smoke caught it in one run.
    """
    values = {
        "day": day.strftime(today_patch_format(config)),
        "sequence": str(sequence),
        "code": code,
    }
    name = _resolved_folder_template(config)
    for field, tokens in _FOLDER_TOKENS.items():
        for token in tokens:
            name = name.replace(token, values[field])
    return name


def patch_folder_re(config: dict[str, Any]) -> re.Pattern[str]:
    """The same template, read back: `day`, `sequence` and `code` groups.

    Built from `patch_folder` rather than written out separately, so a project
    that renames its folders can still find them. Everything the template holds
    between tokens is escaped, which is what makes `patch_folder_splitter: '.'`
    match a dot rather than any character.

    `patch_folder_splitter` is old ADT's own key for the character sitting between
    the three parts (config.yaml:127). ADT.ai reads it as a *substitution* over the
    default template's separators, so setting it alone is enough for the common
    case and `patch_folder` is there for a project that wants a different shape
    entirely.
    """
    template = _resolved_folder_template(config)
    pattern: list[str] = ["^"]
    position = 0
    for match in _ANY_TOKEN_RE.finditer(template):
        pattern.append(re.escape(template[position:match.start()]))
        pattern.append(f"(?P<{_field_for(match.group(0))}>{_FOLDER_PATTERNS[_field_for(match.group(0))]})")
        position = match.end()
    pattern.append(re.escape(template[position:]))
    pattern.append("$")
    return re.compile("".join(pattern))


def _field_for(token: str) -> str:
    for field, tokens in _FOLDER_TOKENS.items():
        if token in tokens:
            return field
    raise KeyError(token)


def _resolved_folder_template(config: dict[str, Any]) -> str:
    """`patch_folder` with `patch_folder_splitter` applied to the default shape.

    A project setting only the splitter gets its separator everywhere; one that
    spells its own `patch_folder` is taken at its word, because the template
    already says where every character goes.
    """
    template = _text(config, "patch_folder")
    splitter = _text(config, "patch_folder_splitter")
    default_splitter = str(DEFAULTS["patch_folder_splitter"])
    if template == str(DEFAULTS["patch_folder"]) and splitter != default_splitter:
        return template.replace(default_splitter, splitter)
    return template


# --- which commits and files enter a patch -----------------------------------

def apex_ignore_patterns(config: dict[str, Any]) -> list[str]:
    """`apex_files_ignore`: exported APEX files that never enter a patch.

    Old ADT's seven (config.yaml:255-262). Every one either recreates the
    application from scratch, deletes it, or is an installer APEX generates for a
    full import, so shipping one inside a patch is at best a no-op and at worst
    drops the application the patch was meant to change.
    """
    raw = config.get("apex_files_ignore")
    if raw is None:
        return list(DEFAULTS["apex_files_ignore"])
    if not isinstance(raw, list | tuple):
        return list(DEFAULTS["apex_files_ignore"])
    return [str(pattern) for pattern in raw]


def is_ignored_apex_file(path: str, config: dict[str, Any]) -> bool:
    """Does ``path`` end with one of the configured ignore patterns?

    Matched on the TAIL rather than the whole path, because the pattern is
    written relative to an application's own folder (`/install.sql`) while the
    path carries whatever `path_apex` and `apex_path_app` put above it. The
    leading `/` in each pattern is what keeps `/install.sql` from matching a file
    called `pre_install.sql`.
    """
    posix = "/" + Path(path).as_posix().lstrip("/")
    return any(
        fnmatch.fnmatchcase(posix, f"*{str(pattern)}")
        for pattern in apex_ignore_patterns(config)
        if str(pattern).strip()
    )


# --- what the generated scripts are called -----------------------------------


def group_script_name(group: str, config: dict[str, Any]) -> str:
    """`patch_group_file`: the per-group install script, `APP_OWNER.sql` by default.

    A template naming no `#GROUP#` token still gets the group prepended: every
    group writing one filename is data loss rather than a layout a project meant.
    """
    template = _text(config, "patch_group_file")
    if "#GROUP#" in template or "{$GROUP}" in template:
        return template.replace("#GROUP#", group).replace("{$GROUP}", group)
    return f"{group}{Path(template).suffix}" if Path(template).suffix else f"{group}{template}"


def group_from_script_name(name: str, config: dict[str, Any]) -> str:
    """The group `group_script_name` wrote into this filename, read back.

    The reverse of the writer above, derived from the same template, because a
    deploy reads the schema and the application id out of the script's own name.
    That reader was `stem.split(".", 1)[0]` and it survived `patch_group_file`
    landing intact, so `install_APP_OWNER.sql` reported its schema as
    `INSTALL_APP_OWNER` and the deploy connected to nothing. Found by the live
    smoke on the run that configured the key, not by the suite.

    A name the template cannot explain falls back to the stem, which is the
    pre-key behaviour: a patch folder built under a different `patch_group_file`
    is still on disk and still has to deploy.
    """
    stem = Path(name).stem
    pattern = _group_script_re(config)
    if pattern is not None:
        match = pattern.fullmatch(Path(name).name)
        if match:
            return match.group("group")
    return stem


def _group_script_re(config: dict[str, Any]) -> re.Pattern[str] | None:
    """A reader for `patch_group_file`, or None when it names no group.

    The group is greedy, so a group carrying the template's own separators still
    comes back whole; a template with no token cannot say which group a file
    holds, so nothing is guessed from it and the stem answers instead.
    """
    template = _text(config, "patch_group_file")
    token = next((t for t in ("#GROUP#", "{$GROUP}") if t in template), None)
    if token is None:
        return None
    before, _, after = template.partition(token)
    return re.compile(f"{re.escape(before)}(?P<group>.+){re.escape(after)}")


def install_script_name(config: dict[str, Any]) -> str:
    """`patch_install_file`: the per-schema script `patch -create` writes."""
    return _text(config, "patch_install_file")


# --- what opens and closes a generated script --------------------------------


def session_directives(config: dict[str, Any]) -> tuple[str, ...]:
    """`patch_session_directives`: the `SET` lines every install script opens with.

    `SET DEFINE OFF` is the load-bearing one, SQLcl reads `&` as a substitution
    prompt, so a body holding a literal `&APP_ID.` stops a terminal-less deploy
    dead. A project overriding this list owns that risk; an empty list emits
    nothing, which is a legitimate answer for a project whose own `db_init`
    template sets the session up.
    """
    raw = config.get("patch_session_directives")
    if raw is None:
        return tuple(DEFAULTS["patch_session_directives"])
    if not isinstance(raw, list | tuple):
        return tuple(DEFAULTS["patch_session_directives"])
    return tuple(str(directive) for directive in raw)


def rollback_directives(config: dict[str, Any]) -> tuple[str, ...]:
    """`patch_rollback`: the failure policy the generated script carries.

    Old ADT's own key (patch.py:153). `True` is the safe default and what ships:
    a failing statement rolls the deploy back rather than leaving a schema half
    patched. A project that would rather run every statement and read the log
    sets it `False` and gets `CONTINUE` in the script itself, instead of having to
    remember `-deploy -continue` on every run. The flag still overrides per run.
    """
    if _flag(config, "patch_rollback"):
        return ("WHENEVER OSERROR  EXIT ROLLBACK;", "WHENEVER SQLERROR EXIT ROLLBACK;")
    return ("WHENEVER OSERROR  CONTINUE;", "WHENEVER SQLERROR CONTINUE;")


def spool_line(config: dict[str, Any], *, folder: str, schema: str) -> str:
    """`patch_spool_line`: the SPOOL the install script opens its own log with.

    The destination is known at create time because the script is generated per
    target, which is what lets a hand-run in SQLcl land where an `adtai patch
    -deploy` does rather than dropping a stray log in the folder root (ADT #260).
    """
    return (
        _text(config, "patch_spool_line")
        .replace("{$FOLDER}", folder)
        .replace("#FOLDER#", folder)
        .replace("{$SCHEMA}", schema)
        .replace("#SCHEMA#", schema)
    )


# --- what a deploy leaves behind ---------------------------------------------


def deploy_log_name(
    config: dict[str, Any],
    *,
    moment: datetime,
    stem: str,
    status: str,
) -> str:
    """`patch_deploy_log_file`: one deploy's log, stamped with its outcome.

    The stamp's FORMAT is `today_deploy`, old ADT's key for exactly this
    (config.yaml:9), kept separate from the name so a project can change either
    without restating the other.
    """
    stamp = moment.strftime(_text(config, "today_deploy"))
    name = _text(config, "patch_deploy_log_file")
    for token, value in (
        ("TIMESTAMP", stamp),
        ("SCHEMA", stem),
        ("STATUS", status),
    ):
        name = name.replace(f"{{${token}}}", value).replace(f"#{token}#", value)
    return name


def apex_scan_log_name(config: dict[str, Any], *, moment: datetime, app_id: int) -> str:
    """One application's post-deploy scan log, beside that deploy's own logs.

    Shares `today_deploy` with `deploy_log_name` so the scan and the deploy it
    verifies sort together in the folder; the rest of the name is fixed, see
    `APEX_SCAN_LOG_FILE` for why it is not configurable and not `.log`.
    """
    stamp = moment.strftime(_text(config, "today_deploy"))
    name = APEX_SCAN_LOG_FILE
    for token, value in (("TIMESTAMP", stamp), ("APP", str(app_id))):
        name = name.replace(f"{{${token}}}", value).replace(f"#{token}#", value)
    return name


def verify_deploy_scan(config: dict[str, Any]) -> bool:
    """`deploy_verify_scan`: ask the application whether its own SQL still parses.

    On by default. After a deploy lands an APEX application -- as a per-app
    install script or as an APEXlang import -- ADT runs the APEX dependency scan
    against it and reads back every component property the scan could not
    compile. Findings are written to a log beside the deploy's own and mark the
    deploy ERROR, because an application that imported cleanly and cannot run a
    region query has not been deployed, it has been installed.

    False skips the scan entirely: no scan, no log, no effect on the status. For
    a target where the extra minute per application is not wanted, or an APEX
    older than 24.2, where the dictionary cannot answer.
    """
    return _flag(config, "deploy_verify_scan")


def archive_format(config: dict[str, Any]) -> str:
    """`patch_archive_format`: the format `-archive` writes, `zip` by default.

    Validated against what `shutil` can actually write, and an unknown value
    falls back rather than raising: a patch that built correctly must not die at
    the archive step over a typo in config.
    """
    requested = _text(config, "patch_archive_format")
    available = {name for name, _description in shutil.get_archive_formats()}
    return requested if requested in available else str(DEFAULTS["patch_archive_format"])


def harden_scripts(config: dict[str, Any]) -> bool:
    """`patch_harden`: rewrite each per-patch script into a guarded block.

    On by default, which is what makes a re-deploy safe: every `CREATE`, `ALTER`
    and `DROP` becomes existence-checked, so running a patch twice is not an
    error. A project whose scripts already carry their own guards, or whose DDL
    the rewrite cannot parse, turns it off and ships them verbatim.
    """
    return _flag(config, "patch_harden")


__all__ = [name for name in globals() if not name.startswith("_")]
