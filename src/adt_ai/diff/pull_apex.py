"""`diff -apex -restore`: the target's applications, where the source keeps them (#893).

The `-apex` half of `diff/pull`. **An application is exported from the target
by `export_apex` itself**, into a throwaway root, in the formats the checkout
already tracks for it, and the checkout's copy of the application is then made
to match that export file for file: what the export wrote is copied over, and
what it did not write is deleted. The project keeps no list of formats in its
config, `export_apex` takes them as flags, so the ones on disk are the answer
to *which formats does this repository track*; an application the checkout has
never exported takes what its sibling applications carry, and split SQL when
there are none.

* **Exported elsewhere, then copied, so `-target-app` can land.** Jan: *"this
  will allow us to compare our app to a working copy"*. The working copy is
  another application id, whose folder and whose `f<id>.sql` carry its own id;
  its export is copied into the SOURCE application's folder and renamed to the
  source's id, so `git diff` reads as one application changing.
* **Only what `export_apex` owns is touched**: `f<id>.sql`, `f<id>.yaml`, and
  the `application/`, `embedded_code/`, `apexlang/`, `comments/` and static
  files folders. Anything else a user keeps beside them stays. The APEXlang
  payload links are git-excluded and relinked by `export_apex`'s own helper.
* **`-page` pulls only the selected pages' files**, in every format, the
  APEXlang and full exports included, which `export_apex -page` would otherwise
  write whole: a page file is named by its number in every format, so the
  copy keeps to the pages the screen listed. Application-wide changes and the
  workspace's files are left alone, as the comparison leaves them.
* **Workspace static files** that differ are written to, or deleted from, the
  `-files_ws` location, read from the target the way `export_apex -files_ws`
  reads them.
"""
from __future__ import annotations

import re
import tempfile
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from adt_ai.diff.apex import (
    WORKSPACE_FILES_APP_ID,
    WORKSPACE_START_QUERY,
    ApexDiff,
    ApexSide,
    FileChange,
    _apex_version,
)
from adt_ai.diff.pull import PullSides
from adt_ai.export_apex import queries
from adt_ai.export_apex.files import ApexFileResolver, _clean_relative
from adt_ai.export_apex.filters import ApexPageSelection
from adt_ai.export_apex.inventory import ApexApplication, ApexDiscovery
from adt_ai.export_apex.postprocess import _blob_bytes
from adt_ai.export_apex.request import ApexExportRequest
from adt_ai.export_apex.runner import ApexExportRunner
from adt_ai.shared import text_files
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.apex_payloads import PAYLOAD_DIR, link_payloads
from adt_ai.shared.row_values import row_value

#: The formats `export_apex` takes as flags, as `ApexExportRequest.actions` names them.
FORMATS = ("full", "split", "readable", "embedded", "apexlang", "files")

#: The folders under an application's own folder that `export_apex` writes.
_OWNED_FOLDERS = ("application", "embedded_code", APEXLANG_DIR, "comments")

#: A page's number in any format's file name: `pages/page_00010.sql`,
#: `pages/p00010-orders.apx`, `comments/p00010.yaml`.
_PAGE = re.compile(r"(?:^|/)(?:pages|comments)/(?:page_|p)(\d+)(?=[._/-])")

#: What a pull writes when the checkout tracks no application to learn from.
DEFAULT_FORMATS = frozenset({"split"})


def pull_apex(
    sides: PullSides,
    diff: ApexDiff,
    apex_sides: tuple[ApexSide, ApexSide],
    pages: ApexPageSelection | None = None,
) -> None:
    """Every application the screen lists, then the workspace's files."""
    source, target = apex_sides
    resolver = _resolver(sides.root, sides, source.owner)
    ours = _applications(source)
    theirs = _applications(target)
    version = _apex_version(target.gateway) if diff.applications else None
    for application in diff.applications:
        mine = ours.get(application.app_id)
        copy = theirs.get(target.own_id(application.app_id))
        # An application only the target has lands in a folder of its own; one
        # only the source has is exported from nowhere, which deletes it.
        home = mine or copy
        if home is None:
            continue
        with tempfile.TemporaryDirectory(prefix="adt_diff_pull_") as scratch:
            exported = Path(scratch) / "none"
            rename: Callable[[Path], Path] = _same
            if copy is not None:
                request = ApexExportRequest(
                    root           = Path(scratch),
                    schemas        = [source.owner],
                    applications   = {source.owner: [copy]},
                    actions        = dict.fromkeys(FORMATS, False)
                    | dict.fromkeys(detect_formats(resolver, home), True),
                    config         = sides.config,
                    environment    = sides.target.environment,
                    page_selection = pages,
                    apex_version   = version,
                )
                ApexExportRunner(lambda _schema: target.gateway).run(request)
                exported = _resolver(Path(scratch), sides, source.owner).app_root(copy)
                rename = _renamer(copy.app_id, home.app_id)
            mirror(exported, resolver.app_root(home), _owner(home.app_id, resolver, pages), rename)
        link_payloads(resolver.apexlang_root(home), resolver.application_file(home, ""))
    _pull_workspace_files(resolver, target, diff.workspace_files)


def _resolver(root: Path, sides: PullSides, schema: str) -> ApexFileResolver:
    """The resolver `export_apex` writes through under `root`, for `schema`."""
    return ApexFileResolver.from_config(root, dict(sides.config)).for_schema(schema)


def _applications(side: ApexSide) -> dict[int, ApexApplication]:
    discovered = ApexDiscovery(side.gateway).applications(
        owner     = side.owner,
        workspace = side.workspace,
        group     = side.group,
    )
    return {application.app_id: application for application in discovered}


def detect_formats(resolver: ApexFileResolver, application: ApexApplication) -> frozenset[str]:
    """The formats the checkout tracks for `application`, else for its siblings."""
    found = _formats_in(resolver.app_root(application), resolver)
    if found:
        return found
    apex_root = resolver.apex_root()
    folders = sorted(apex_root.iterdir()) if apex_root.is_dir() else []
    for folder in folders:
        if folder.is_dir() and folder != resolver.workspace_root():
            found |= _formats_in(folder, resolver)
    return found or DEFAULT_FORMATS


def _formats_in(folder: Path, resolver: ApexFileResolver) -> frozenset[str]:
    application = folder / "application"
    markers = {
        "full"     : any(folder.glob("f*.sql")),
        "split"    : any(application.rglob("*.sql")),
        "readable" : any(folder.glob("f*.yaml")) or any(application.rglob("*.yaml")),
        "embedded" : (folder / "embedded_code").is_dir(),
        "apexlang" : (folder / APEXLANG_DIR).is_dir(),
        "files"    : (folder / _clean_relative(resolver.path_files)).is_dir(),
    }
    return frozenset(name for name, present in markers.items() if present)


def _owner(
    app_id: int, resolver: ApexFileResolver, pages: ApexPageSelection | None
) -> Callable[[Path], bool]:
    """Whether a path under the application's folder is one `export_apex` writes."""
    files = _clean_relative(resolver.path_files).parts
    payload = (APEXLANG_DIR, *PAYLOAD_DIR)
    top = re.compile(rf"f{app_id}\.(?:sql|yaml)")

    def owned(relative: Path) -> bool:
        parts = relative.parts
        if parts[: len(payload)] == payload:
            return False
        exported = (
            parts[0] in _OWNED_FOLDERS
            or parts[: len(files)] == files
            or (len(parts) == 1 and top.fullmatch(parts[0]) is not None)
        )
        if not exported or pages is None:
            return exported
        match = _PAGE.search(relative.as_posix())
        return match is not None and pages.matches(int(match.group(1)))

    return owned


def _renamer(their_id: int, our_id: int) -> Callable[[Path], Path]:
    """`f101.sql` read as `f100.sql`: a working copy's export under the original's id."""
    pattern = re.compile(rf"^f{their_id}(?=\.)")
    return lambda relative: Path(*(pattern.sub(f"f{our_id}", part) for part in relative.parts))


def _same(relative: Path) -> Path:
    return relative


def mirror(
    exported: Path,
    checkout: Path,
    owned: Callable[[Path], bool],
    rename: Callable[[Path], Path] = _same,
) -> None:
    """Make the owned part of `checkout` hold exactly what `exported` holds."""
    kept: set[Path] = set()
    for path in _files(exported):
        relative = rename(path.relative_to(exported))
        if not owned(relative):
            continue
        kept.add(relative)
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        text_files.write_bytes(target, path.read_bytes())
    for path in _files(checkout):
        relative = path.relative_to(checkout)
        if owned(relative) and relative not in kept:
            path.unlink()
    _drop_empty_folders(checkout)


def _files(root: Path) -> Iterable[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file()) if root.is_dir() else []


def _drop_empty_folders(root: Path) -> None:
    if not root.is_dir():
        return
    for folder in sorted((path for path in root.rglob("*") if path.is_dir()), reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
    if not any(root.iterdir()):
        root.rmdir()


def _pull_workspace_files(
    resolver: ApexFileResolver, target: ApexSide, changes: Sequence[FileChange]
) -> None:
    """The target's copy of each differing workspace file, or no file when it has none."""
    if not changes:
        return
    gateway = target.gateway
    gateway.execute(WORKSPACE_START_QUERY, {"owner": target.owner, "workspace": target.workspace})
    payloads = {
        str(row_value(row, "FILENAME") or ""): _blob_bytes(row_value(row, "BLOB_CONTENT"))
        for row in gateway.fetch_all(queries.APEX_FILES_QUERY, {"app_id": WORKSPACE_FILES_APP_ID})
    }
    for change in changes:
        path = resolver.workspace_file(change.name)
        if change.name in payloads:
            path.parent.mkdir(parents=True, exist_ok=True)
            text_files.write_bytes(path, payloads[change.name])
        else:
            path.unlink(missing_ok=True)


__all__ = [name for name in globals() if not name.startswith("_")]
