"""`patch -create`'s codescan gate: what the patch carries, scanned before a write (ADT #1025).

`validate -codescan` holds whole trees to their baselines. A patch is the
narrower question, *does what I am about to ship add a finding*, so `-create`
scans the files it carries and lists findings in nothing else, when
`patch_codescan` in `config.yaml` says `warn` or `block`.

**The files are staged, never scanned in place.** codescan takes one `-path`, a
folder, and the files a patch carries sit across several trees beside files it
does not carry. So each one is copied, in the version the patch ships (the
committed blob in the default mode, the tree on disk for an APEXlang file, the
way `file_present` reads them), into a folder of its own under the project's
gitignored `config/temp/`, at its repository path. The folder is named for the
patch folder and made unique by `mkdtemp`, never derived from a folder's name
alone, the collision `validate`'s staging tree once had; it is removed once the
scan is read.

**An APEXlang file is staged with its application beside it.** codescan reads
application-scope settings off the rest of the tree: page 4 of the fixture
application scanned alone reported APEX-002, APEX-009 and APEX-026, all three
satisfied at application scope and absent from the same page scanned in its
tree (measured on SQLcl 26.3, the `#1025` story). So the application's other
source files go in as context, read off disk the way the import reads them,
and a finding in one of them is never listed: the patch does not carry it.

**Each finding is held to its own tree's baseline**, the one `validate
-codescan` recorded in `config/internal/codescan.db`, read for only the files
this patch carries, so a finding elsewhere in the tree is neither known nor
fixed here. A file in no tree, or in a tree with no baseline, has nothing
vouching for it, so every finding in it is new: the gate never records, so a
first-run pass the way `validate` takes one would pass every patch forever.
`codescan_fail_on` decides what counts, as it does for `validate`.

**`warn` lists and builds, `block` lists and refuses** before the folder is
made, so a refusal leaves nothing behind. A scan that could not be read, read
none of the files, or met a file opening on a UTF-8 BOM (read by codescan as
clean) is a violation under either: the gate cannot vouch for what it did not
read.
"""

from __future__ import annotations

import re
import shutil
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.patch.content import file_bytes
from adt_ai.patch.layout import is_apexlang_path
from adt_ai.patch.models import PatchError
from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import under_dot_folder
from adt_ai.shared.apexlang_line_endings import PAYLOAD_DIR
from adt_ai.shared.codescan_settings import (
    FAIL_ON_ANY,
    FAIL_ON_NEW,
    FAIL_ON_NONE,
    PATCH_CODESCAN_BLOCK,
    CodescanIgnore,
    rule_selected,
)
from adt_ai.shared.commit_discovery import CommitRecord
from adt_ai.validate.codescan import (
    SOURCE_SUFFIXES,
    CodescanRequest,
    CodescanRunner,
    bom_files,
    database_trees,
)
from adt_ai.validate.codescan_baseline import Comparison, baseline, compare, finding_order
from adt_ai.validate.codescan_report import CLEAN, FINDINGS, Finding, TreeScan
from adt_ai.validate.files import ValidateTarget, discover_targets, tree_label

#: Reads the bytes a carried file ships, ``None`` for one the patch deletes.
ShippedReader = Callable[[str], bytes | None]

#: What the refusal says; the sections above it already list what to fix.
REFUSAL = (
    "CODESCAN REFUSED THE PATCH\n\n"
    "patch_codescan is block, so nothing was written.\n"
    "Fix what is listed above and create the patch again."
)


@dataclass
class PatchCodescan:
    """One run's gate: what it may stop, how SQLcl is reached, who watches.

    ``render`` prints what the scan found, the CLI's; `None` prints nothing.
    """

    mode          : str
    fail_on       : str = FAIL_ON_NEW
    sqlcl_request : Callable[..., str] | None = None
    reporter      : Any = None
    render        : Callable[[PatchScan], None] | None = None
    #: `codescan_ignore` (ADT #1024), the same suppressions `validate` applies.
    ignore        : tuple[CodescanIgnore, ...] = ()
    #: `codescan_rules` (ADT #1023), the rules reported; empty for every rule.
    rules         : tuple[str, ...] = ()


@dataclass(frozen=True)
class PatchScan:
    """What one patch's scan found, and whether it fails the gate."""

    label      : str
    scan       : TreeScan
    #: What the console lists, each file by its repository path.
    listed     : tuple[Finding, ...]
    #: ``None`` for a scan that could not be read or read nothing.
    comparison : Comparison | None
    boms       : tuple[str, ...]
    #: Under `codescan_fail_on: any` every finding is listed, as errors.
    every      : bool
    violated   : bool


class CodescanGateError(PatchError):
    """`block` met a violation: the build stops before anything is written."""

    def __init__(self) -> None:
        super().__init__(REFUSAL)


def shipped_reader(
    root    : Path,
    config  : Mapping[str, Any],
    *,
    mode    : str,
    records : list[CommitRecord],
    pinned  : Mapping[str, str],
) -> ShippedReader:
    """The version of each file the build ships, read the way the build reads it."""

    def read(path: str) -> bytes | None:
        if is_apexlang_path(path, dict(config)):
            source = root / path
            return source.read_bytes() if source.is_file() else None
        return file_bytes(root, path, mode=mode, records=records, pinned_ref=pinned.get(path))

    return read


def check_patch_codescan(
    root     : Path,
    config   : Mapping[str, Any],
    files    : Sequence[str],
    codescan : PatchCodescan | None,
    *,
    folder   : Path,
    read     : ShippedReader,
) -> PatchScan | None:
    """Scan the code ``files`` carries; raise `CodescanGateError` when `block` refuses.

    ``None`` when there is no gate or nothing to scan: a patch carrying no code,
    or only deletions, starts no SQLcl.
    """
    if codescan is None:
        return None
    carried = sorted(path for path in files if Path(path).suffix.lower() in SOURCE_SUFFIXES)
    if not carried:
        return None
    label = folder.name
    temp = root / "config" / "temp"
    temp.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f"codescan-patch-{_safe(label)}-", dir=temp))
    try:
        staged = _stage(stage, carried, read)
        if not staged:
            return None
        _stage_applications(root, config, stage, staged)
        # Only a file the patch carries is the patch's to answer for.
        boms = tuple(path for path in bom_files(stage) if path in staged)
        (outcome,) = CodescanRunner(codescan.sqlcl_request).run(CodescanRequest(
            targets  = (ValidateTarget(stage, label),),
            root     = root,
            reporter = codescan.reporter,
            ignore   = codescan.ignore,
            # The stage holds each file at its repository path.
            mirror   = root,
        ))
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    result = judge(
        root, config, label, outcome.scan, staged, boms, codescan.fail_on, rules=codescan.rules,
    )
    if codescan.render is not None:
        codescan.render(result)
    if result.violated and codescan.mode == PATCH_CODESCAN_BLOCK:
        raise CodescanGateError()
    return result


def judge(
    root    : Path,
    config  : Mapping[str, Any],
    label   : str,
    scan    : TreeScan,
    staged  : Sequence[str],
    boms    : tuple[str, ...],
    fail_on : str,
    *,
    rules   : tuple[str, ...] = (),
) -> PatchScan:
    """What the scan means under `codescan_fail_on`, against each tree's baseline.

    Only findings in a carried file count; one in an application file staged as
    context belongs to a patch that carries that file. Only a rule
    `codescan_rules` names counts, on both sides of the comparison, the way
    `validate` reads it.
    """
    every = fail_on == FAIL_ON_ANY
    if scan.outcome not in (CLEAN, FINDINGS):
        # Unreadable, or read none of the files: nothing here can vouch for them.
        return PatchScan(label, scan, (), None, boms, every, violated=True)
    carried = set(staged)
    findings = [
        finding for finding in scan.findings
        if finding.file in carried and rule_selected(finding.rule, rules)
    ]
    comparison = held_to_baselines(root, config, findings, staged, rules=rules)
    if every:
        listed = tuple(sorted(findings, key=finding_order))
        violated = bool(findings)
    else:
        listed = comparison.new_findings
        violated = fail_on != FAIL_ON_NONE and comparison.new > 0
    return PatchScan(label, scan, listed, comparison, boms, every, violated or bool(boms))


def held_to_baselines(
    root     : Path,
    config   : Mapping[str, Any],
    findings : Sequence[Finding],
    staged   : Sequence[str],
    *,
    rules    : tuple[str, ...] = (),
) -> Comparison:
    """Every finding against the baseline of the tree its file sits in.

    Each tree's baseline is narrowed to the files this patch carries, so
    `fixed` counts only what vanished from them. The new findings come back
    under their repository paths, in file order.
    """
    trees = _trees(root, config)
    groups: dict[str | None, list[tuple[Finding, Finding]]] = {}
    carried: dict[str | None, set[str]] = {}
    for path in staged:
        owner, relative = _owner(root, trees, path)
        carried.setdefault(owner, set()).add(relative)
    for finding in findings:
        owner, relative = _owner(root, trees, finding.file)
        local = Finding(relative, finding.line, finding.column, finding.rule,
                        finding.message, finding.target)
        groups.setdefault(owner, []).append((local, finding))
    known = new = fixed = 0
    listed: list[Finding] = []
    for owner, files in carried.items():
        pairs = groups.get(owner, [])
        previous = baseline(root, owner) if owner is not None else None
        allowed = Counter({
            key: count for key, count in (previous or Counter()).items()
            if key[0] in files and rule_selected(key[1], rules)
        })
        result = compare(allowed, [local for local, _ in pairs])
        originals = {id(local): finding for local, finding in pairs}
        known += result.known
        new += result.new
        fixed += result.fixed
        listed.extend(originals[id(local)] for local in result.new_findings)
    return Comparison(known, new, fixed, tuple(sorted(listed, key=finding_order)), False)


def _trees(root: Path, config: Mapping[str, Any]) -> list[tuple[Path, str]]:
    """Every tree `validate -codescan` keeps a baseline for, deepest first."""
    trees = [(path, tree_label(path, root)) for path in database_trees(root, config)]
    trees.extend((target.path, target.label) for target in discover_targets(root, config))
    return sorted(trees, key=lambda tree: len(tree[0].parts), reverse=True)


def _owner(root: Path, trees: list[tuple[Path, str]], path: str) -> tuple[str | None, str]:
    """The label of the tree ``path`` sits in and the path inside it, or no tree."""
    absolute = root / path
    for tree, label in trees:
        if absolute.is_relative_to(tree):
            return label, absolute.relative_to(tree).as_posix()
    return None, path


def _stage(stage: Path, carried: Sequence[str], read: ShippedReader) -> list[str]:
    """Copy each carried file's shipped bytes in at its repository path."""
    staged: list[str] = []
    for path in carried:
        payload = read(path)
        if payload is None:
            continue
        target = stage / path
        target.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_bytes(target, payload)
        staged.append(path)
    return staged


def _stage_applications(
    root   : Path,
    config : Mapping[str, Any],
    stage  : Path,
    staged : Sequence[str],
) -> None:
    """The rest of each application a carried APEXlang file belongs to, as context.

    Its source files only, off disk, at their repository paths; hidden folders
    and static-file payloads are skipped, as `bom_files` skips them.
    """
    carried = set(staged)
    for target in discover_targets(root, config):
        tree = target.path
        if not tree.is_relative_to(root) or not any(
            (root / path).is_relative_to(tree) for path in staged
        ):
            continue
        for path in sorted(tree.rglob("*")):
            relative = path.relative_to(tree)
            if path.suffix.lower() not in SOURCE_SUFFIXES or not path.is_file():
                continue
            if under_dot_folder(path, tree) or PAYLOAD_DIR in relative.parts[:-1]:
                continue
            repo = path.relative_to(root).as_posix()
            if repo in carried:
                continue
            copy = stage / repo
            copy.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_bytes(copy, path.read_bytes())


_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(label: str) -> str:
    """The patch folder's name as a file name, whatever `patch_folder` put in it."""
    return _UNSAFE_RE.sub("_", label) or "patch"


__all__ = [
    "REFUSAL",
    "CodescanGateError",
    "PatchCodescan",
    "PatchScan",
    "ShippedReader",
    "check_patch_codescan",
    "held_to_baselines",
    "judge",
    "shipped_reader",
]
