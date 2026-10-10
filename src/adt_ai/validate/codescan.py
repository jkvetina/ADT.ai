"""Drive SQLcl's `codescan` over exported trees, one call per tree (ADT #1026).

`codescan` is SQLcl's own code-quality scanner, and like `apex validate` it
answers on a bare `sql -S /nolog` session: no connection, no credentials. The
script is two lines, measured on SQLcl 26.3.0.0 and recorded by
`tests/fixtures/capture_codescan_transcripts.py`:

    codescan -path "<tree>" -format json -output "<report>"
    exit;

One call per tree for the reason `runner.py` gives: a batch is cheaper but a
single blocking call, and a per-tree row that streams its clock is the console
contract. The report goes to a folder of its own under the project's gitignored
`config/temp/`, unique per call and removed once read: a name derived from the
tree's folder would collide for two trees sharing one, the way `validate`'s old
staging tree once did.

**The scan reads the user's files and rewrites none of them.** A UTF-8 byte
order mark is the one input SQLcl 26.3 reads wrongly and silently, as a clean
file, so :func:`bom_files` names every source file carrying one and the run
fails on them, leaving each file exactly as it was.

**`codescan_ignore` reaches SQLcl as a settings file in the scanned folder**
(ADT #1024), since `-settings` resolves against `-path`: a dot-name unique per
call, kept out of git through `.git/info/exclude`, and removed however the scan
ends. What SQLcl 26.3 does with each entry shape is captured beside the other
transcripts (`ignored*`): a code alone suppresses it everywhere, a `file`
narrows it to that file, a `component` to that component, and a component with
no file is refused by SQLcl with no report written.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import under_dot_folder
from adt_ai.shared.apexlang_line_endings import PAYLOAD_DIR
from adt_ai.shared.codescan_settings import (
    APEX,
    DATABASE,
    FAIL_ON_ANY,
    FAIL_ON_NONE,
    CodescanIgnore,
    rule_selected,
)
from adt_ai.shared.config import DEFAULT_PATH_OBJECTS
from adt_ai.shared.db import run_sqlcl_script
from adt_ai.shared.git_exclude import exclude_from_git
from adt_ai.shared.path_template import object_type_token
from adt_ai.shared.sqlcl_quoting import reject_unquotable
from adt_ai.validate.codescan_baseline import Comparison, Key, compare, finding_order
from adt_ai.validate.codescan_report import CLEAN, FINDINGS, Finding, TreeScan, parse_codescan
from adt_ai.validate.files import ValidateTarget, discover_targets, resolve_targets, tree_label
from adt_ai.validate.runner import ValidateReporter, record_timer, timer_estimate

SqlclRequest = Callable[..., str]

CODESCAN_COMMAND = 'codescan -path "{path}" -format json -output "{report}"'

#: The `apex.db` timer one scan of an application is recorded under, beside
#: `validate`'s compile timer, so its row counts down the same way.
CODESCAN_TIMER = "codescan"

#: Where the throwaway reports go, the folder every SQLcl script already uses.
_TEMP_DIR = ("config", "temp")

#: A UTF-8 byte order mark, which SQLcl 26.3's codescan reads as a clean file.
BOM = b"\xef\xbb\xbf"

#: The suffixes the two trees hold that codescan reads: `export_db` writes
#: `.sql` throughout (`object_types` in `config.yaml`), PL/SQL is also commonly
#: kept as `.pks`/`.pkb`/`.pls`/`.plb`, and an APEXlang tree is `.apx`.
SOURCE_SUFFIXES = (".sql", ".pks", ".pkb", ".pls", ".plb", ".apx")


#: The name a run's `codescan_ignore` settings take inside the folder it scans,
#: made unique per call by `mkstemp` (ADT #1024), and the pattern that keeps
#: every such file out of git through `.git/info/exclude`.
SETTINGS_PREFIX = ".adt-codescan-"
SETTINGS_EXCLUDE = f"{SETTINGS_PREFIX}*.json"


@dataclass(frozen=True)
class CodescanRequest:
    targets  : tuple[ValidateTarget, ...]
    root     : Path
    reporter : ValidateReporter | Any | None = None
    #: `codescan_ignore`, written into each scanned folder for its run.
    ignore   : tuple[CodescanIgnore, ...] = ()
    #: The folder every target stands for when it is a staged copy, the project
    #: root for `patch`; ``None`` when each target is the tree itself.
    mirror   : Path | None = None


@dataclass(frozen=True)
class TreeOutcome:
    target : ValidateTarget
    scan   : TreeScan


@dataclass(frozen=True)
class TreeVerdict:
    #: ``None`` for a scan that could not be read or read nothing.
    comparison : Comparison | None
    #: What the console lists for this tree.
    listed     : tuple[Finding, ...]
    #: Whether this tree's findings fail the run.
    violated   : bool
    #: Whether this run becomes the tree's new baseline.
    records    : bool


class CodescanRunner:
    def __init__(self, sqlcl_request: SqlclRequest | None = None) -> None:
        self.sqlcl_request = sqlcl_request or run_sqlcl_script

    def run(self, request: CodescanRequest) -> tuple[TreeOutcome, ...]:
        reporter = request.reporter or ValidateReporter()
        outcomes: list[TreeOutcome] = []
        for target in request.targets:
            outcomes.append(TreeOutcome(target, self._scan(request, target, reporter)))
        return tuple(outcomes)

    def _scan(self, request: CodescanRequest, target: ValidateTarget, reporter: Any) -> TreeScan:
        root = request.root
        temp = root.joinpath(*_TEMP_DIR)
        temp.mkdir(parents=True, exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix="codescan-", dir=temp))
        report = folder / "report.json"
        settings: Path | None = None
        try:
            try:
                settings = write_settings(target.path, settings_entries(
                    request.ignore, root, request.mirror or target.path,
                ))
                script = build_script(target.path, report, settings)
            except Exception:
                reporter.begin(target.label)
                reporter.finish(target.label, "FAILED")
                raise
            reporter.request(script)
            expect = getattr(reporter, "expect", None)
            if expect is not None:
                expect(timer_estimate(root, target.app_id, CODESCAN_TIMER))
            reporter.begin(target.label)
            started = time.monotonic()
            try:
                output = self.sqlcl_request(script, root, project_root=root)
            except Exception:
                reporter.finish(target.label, "FAILED")
                raise
            record_timer(root, target.app_id, CODESCAN_TIMER, time.monotonic() - started)
            text = report.read_text(encoding="utf-8") if report.is_file() else None
        finally:
            shutil.rmtree(folder, ignore_errors=True)
            if settings is not None:
                settings.unlink(missing_ok=True)
        scan = parse_codescan(output, text, target.path)
        reporter.finish(target.label, scan.outcome)
        return scan


def build_script(tree: Path, report: Path, settings: Path | None = None) -> str:
    """The two-line script, refusing a path SQLcl's quoting cannot carry.

    ``settings`` is named by its bare name: SQLcl 26.3 resolves `-settings`
    against `-path`, and an absolute path came back as `<path>/<path>`.
    """
    path = tree.as_posix()
    reject_unquotable(path, role="codescan folder")
    command = CODESCAN_COMMAND.format(path=path, report=report.as_posix())
    if settings is not None:
        command += f' -settings "{settings.name}"'
    return "\n".join([command, "exit;"])


def settings_entries(
    ignores : tuple[CodescanIgnore, ...],
    root    : Path,
    mirror  : Path,
) -> list[dict[str, Any]]:
    """`codescan_ignore` as SQLcl's `ignore` list for one scanned folder.

    ``mirror`` is the folder the scan root stands for. A `file` is a path from
    the project root, so it is rewritten relative to that folder, and an entry
    whose file sits outside it does not reach this scan at all.
    """
    entries: list[dict[str, Any]] = []
    for ignore in ignores:
        entry: dict[str, Any] = {"code": ignore.code}
        if ignore.file is not None:
            relative = path_inside(root / ignore.file, mirror)
            if relative is None:
                continue
            entry["file"] = relative
            if ignore.component is not None:
                kind, ident = ignore.component
                entry["component"] = {"type": kind, "id": ident}
        entry["reason"] = ignore.reason
        entries.append(entry)
    return entries


def path_inside(path: Path, folder: Path) -> str | None:
    """``path`` relative to ``folder``, through a symlinked root as well; ``None`` outside it."""
    for candidate in (Path(os.path.normpath(path)), path.resolve()):
        for base in (Path(os.path.normpath(folder)), folder.resolve()):
            if candidate.is_relative_to(base):
                return candidate.relative_to(base).as_posix()
    return None


def write_settings(tree: Path, entries: list[dict[str, Any]]) -> Path | None:
    """The settings file one scan reads, inside ``tree``; ``None`` when none is owed.

    It has to sit in the scanned folder, so it is a dot-name unique per call,
    excluded from git the way `validate` keeps its linked payloads out, and the
    caller removes it however the scan ends. A single file is scanned without
    one: SQLcl resolves `-settings` under the file itself and finds nothing.
    """
    if not entries or not tree.is_dir():
        return None
    exclude_from_git(tree, SETTINGS_EXCLUDE)
    handle, name = tempfile.mkstemp(prefix=SETTINGS_PREFIX, suffix=".json", dir=tree)
    os.close(handle)
    settings = Path(name)
    text_files.write_text(settings, json.dumps({"ignore": entries}, indent=2) + "\n")
    return settings


def verdict(
    scan     : TreeScan,
    previous : Counter[Key] | None,
    fail_on  : str,
    *,
    bom      : bool,
    rules    : tuple[str, ...] = (),
) -> TreeVerdict:
    """What one tree's scan means under `codescan_fail_on`, and whether it records.

    The ratchet's rule is the same under every setting: a run records only when
    it passes and holds nothing new, so a failing run never becomes the
    baseline and a fixed finding drops out of it. A tree carrying a BOM file is
    never recorded either, since codescan read that file as clean.

    **`codescan_rules` narrows what is compared, never what is recorded** (ADT
    #1023): both this run and the baseline are read through ``rules``, so a
    finding outside the profile is neither listed, counted nor able to fail
    the run, and the caller still records every finding the scan made. A rule
    taken into the profile later therefore finds its old findings already
    known, and one dropped from it is not counted as fixed.
    """
    if scan.outcome not in (CLEAN, FINDINGS):
        return TreeVerdict(None, (), violated=False, records=False)
    findings = tuple(finding for finding in scan.findings if rule_selected(finding.rule, rules))
    if previous is not None and rules:
        previous = Counter({
            key: count for key, count in previous.items() if rule_selected(key[1], rules)
        })
    comparison = compare(previous, findings)
    if fail_on == FAIL_ON_ANY:
        listed = tuple(sorted(findings, key=finding_order))
        violated = bool(findings)
    else:
        listed = comparison.new_findings
        violated = fail_on != FAIL_ON_NONE and comparison.new > 0
    records = not bom and not violated and comparison.new == 0
    return TreeVerdict(comparison, listed, violated, records)


def bom_files(tree: Path) -> list[str]:
    """Every source file under ``tree`` opening on a UTF-8 BOM, relative and sorted.

    Hidden folders and static-file payloads are skipped: neither is code the
    project wrote for codescan to read. A single file is checked as itself.
    """
    if tree.is_file():
        return [tree.name] if _has_bom(tree) else []
    if not tree.is_dir():
        return []
    found: list[str] = []
    for path in sorted(tree.rglob("*")):
        relative = path.relative_to(tree)
        if path.suffix.lower() not in SOURCE_SUFFIXES or not path.is_file():
            continue
        if under_dot_folder(path, tree) or PAYLOAD_DIR in relative.parts[:-1]:
            continue
        if _has_bom(path):
            found.append(relative.as_posix())
    return found


def _has_bom(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(len(BOM)) == BOM


def database_trees(root: Path, config: Mapping[str, Any]) -> list[Path]:
    """Every export_db tree `path_objects` resolves to: the folder above the type.

    Read off the key rather than assumed, the way `doctor`'s layout check reads
    it: the template up to `<object_type>`, every other token globbed, so the
    shipped `<schema>/database/<object_type>/` gives one tree per schema and
    `database/<object_type>/` gives one. A layout with nothing above the type
    folder names no tree, since its root is the whole repository.
    """
    template = str(config.get("path_objects") or DEFAULT_PATH_OBJECTS)
    head = template.strip("/").partition(object_type_token(template) or "<object_type>")[0]
    pattern = _glob(head.strip("/"))
    if not pattern:
        return []
    return sorted(
        path
        for path in root.glob(pattern)
        if path.is_dir() and not under_dot_folder(path, root)
    )


def _glob(template: str) -> str:
    """Every `<token>` in ``template`` read back as a wildcard, `db_<schema>` as `db_*`."""
    parts = [part for part in template.split("/") if part and part != "."]
    return "/".join(_TOKEN_RE.sub("*", part) for part in parts)


_TOKEN_RE = re.compile(r"<[^<>]*>")


def codescan_targets(
    root    : Path,
    config  : Mapping[str, Any],
    kinds   : tuple[str, ...],
    inputs  : list[str] | None,
    app_ids : list[str] | None,
) -> tuple[list[ValidateTarget], list[str]]:
    """The trees a run scans, plus notes about the ones that could not be found.

    `-input` and `-app` name their trees exactly as `validate` resolves them and
    replace `codescan_paths` for the run. A bare run scans the kinds the key
    lists, in its order; a kind with nothing exported adds nothing, and a run
    left with no tree at all is the caller's refusal.
    """
    if inputs or app_ids:
        return resolve_targets(root, config, inputs=inputs, app_ids=app_ids)
    targets: list[ValidateTarget] = []
    for kind in kinds:
        if kind == DATABASE:
            targets.extend(
                ValidateTarget(path, tree_label(path, root))
                for path in database_trees(root, config)
            )
        elif kind == APEX:
            targets.extend(discover_targets(root, config))
    return targets, []


__all__ = [
    "BOM",
    "CODESCAN_COMMAND",
    "CODESCAN_TIMER",
    "SETTINGS_EXCLUDE",
    "SETTINGS_PREFIX",
    "SOURCE_SUFFIXES",
    "CodescanRequest",
    "CodescanRunner",
    "TreeOutcome",
    "TreeVerdict",
    "bom_files",
    "build_script",
    "codescan_targets",
    "database_trees",
    "path_inside",
    "settings_entries",
    "verdict",
    "write_settings",
]
