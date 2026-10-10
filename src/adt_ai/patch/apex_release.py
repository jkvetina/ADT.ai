"""A target running a newer APEX than the APEXlang tree was exported from (ADT #1064).

APEX 26.2's upgrade process for APEXlang says the source has to be upgraded
after an APEX upgrade: a tree exported on 26.1 is the 26.1 metadata, and
importing it onto a 26.2 instance ships whatever 26.1 was able to describe.
Nothing else on screen says so, because the import itself succeeds.

**The tree's release is the one it recorded itself.** Every APEXlang export
writes `.apex/apexlang.json` with `mmdVersion` (`26.1.0+3102`), which is the
APEX release that described it. `application.apx`'s own `version:` is the
application's version attribute, not APEX's, so it is never read for this.

**A warning, never a refusal**, on both `patch -create` and `patch -deploy`
(Jan's pick on the card): `-create` names it while re-exporting is still cheap,
and `-deploy` names it again on the target it is about to write. Only a NEWER
target warns, compared on major and minor: a patch level (`26.1.3` over
`26.1.0`) is not an upgrade of the metadata, and an older target is a different
problem the import reports on its own. A release either side cannot read says
nothing, the way every other version probe in ADT stays permissive.

Upgrading the tree, and chaining the export, are out of scope: the warning
names the command, the developer runs it.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from adt_ai.patch.apex_scan import resolve_apex_version
from adt_ai.patch.layout import apex_app_id, apex_app_root, is_apexlang_path
from adt_ai.patch.selection import apex_owner_schemas
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.apex_version import apex_version_tuple
from adt_ai.validate.files import ValidateTarget

#: Where an APEXlang export records the release that wrote it.
TREE_MANIFEST = Path(".apex") / "apexlang.json"

#: The export that brings a tree up to the target's release.
EXPORT_COMMAND = "adtai export_apex -app {app_id} -apexlang"


@dataclass(frozen=True)
class ReleaseDrift:
    """One application whose tree is older than the APEX it is going onto."""

    app_id  : int
    tree    : str
    target  : str


def tree_release(tree: Path) -> str:
    """The `mmdVersion` the tree's export recorded, build suffix dropped, or ``""``.

    ``""`` for a tree with no manifest, one that does not parse, or one that
    carries no version: an unknown release is never a warning.
    """
    try:
        manifest = json.loads((Path(tree) / TREE_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(manifest, dict):
        return ""
    return str(manifest.get("mmdVersion") or "").split("+", 1)[0].strip()


def release_drift(app_id: int, tree: Path, target_release: str | None) -> ReleaseDrift | None:
    """The drift when ``target_release`` is a newer major.minor than the tree's."""
    exported = tree_release(tree)
    tree_key = apex_version_tuple(exported)[:2]
    target_key = apex_version_tuple(target_release)[:2]
    if len(tree_key) < 2 or len(target_key) < 2 or target_key <= tree_key:
        return None
    return ReleaseDrift(
        app_id = app_id,
        tree   = ".".join(str(part) for part in tree_key),
        target = ".".join(str(part) for part in target_key),
    )


def release_drifts(
    trees          : Iterable[tuple[int, Path]],
    target_release : str | None,
) -> list[ReleaseDrift]:
    """Every ``(app_id, tree)`` the target has outgrown, in the order given."""
    return [
        drift
        for app_id, tree in trees
        if (drift := release_drift(app_id, tree, target_release)) is not None
    ]


def probe_target_release(gateway_factory: Callable[[str], Any] | None, schema: str) -> str:
    """The target's APEX release, read once, or ``""`` when it cannot be read.

    APEX is one release per instance, so one schema's connection answers for
    every application. A connection that fails says nothing: the warning is a
    courtesy and must not stop a build.
    """
    if gateway_factory is None:
        return ""
    try:
        return resolve_apex_version(gateway_factory(schema))
    except Exception:  # noqa: BLE001 - an unknown release is not a failed build
        return ""


def patch_trees(root: Path, config: dict[str, Any], files: Iterable[str]) -> list[tuple[int, Path]]:
    """Each application the patch ships an APEXlang tree for, and that tree, by id."""
    trees: dict[int, Path] = {}
    for path in files:
        app_root = apex_app_root(path, config)
        app_id = apex_app_id(path, config)
        if app_root is None or app_id is None or not is_apexlang_path(path, config):
            continue
        trees.setdefault(app_id, Path(root, *app_root, APEXLANG_DIR))
    return sorted(trees.items())


def create_release_drifts(
    root            : Path,
    config          : dict[str, Any],
    files           : Iterable[str],
    gateway_factory : Callable[[str], Any] | None,
) -> list[ReleaseDrift]:
    """`patch -create`'s answer: the shipped trees the target has outgrown.

    The target is the connection the build's table ALTERs already resolve
    (`-target`, else the connection file's default), which is the one `-deploy
    -target` writes to. A patch shipping no APEXlang tree connects to nothing.
    """
    trees = patch_trees(root, config, files)
    if not trees:
        return []
    owner = apex_owner_schemas(root).get(trees[0][0], "")
    return release_drifts(trees, probe_target_release(gateway_factory, owner))


class ReleaseReads:
    """`-create`'s release reads, each inside its app's validation row.

    The seam ADT #988 drew for the signature read (`apex_drift.DriftReads`):
    a database read after the `VALIDATING APEXLANG APPS:` rows have closed runs
    under a screen of finished rows, so the probe is made inside the first
    tree's open row, once, and every tree is compared under its own. ``after``
    is the hook that row already ran, kept first so its clock order holds.
    """

    def __init__(
        self,
        root            : Path,
        gateway_factory : Callable[[str], Any] | None,
        after           : Callable[[ValidateTarget], None],
    ) -> None:
        self.root = root
        self.gateway_factory = gateway_factory
        self.after = after
        self.release: str | None = None
        self.drifts: list[ReleaseDrift] = []

    def read(self, target: ValidateTarget) -> None:
        self.after(target)
        if self.gateway_factory is None or target.app_id is None:
            return
        if self.release is None:
            owner = apex_owner_schemas(self.root).get(target.app_id, "")
            self.release = probe_target_release(self.gateway_factory, owner)
        if (drift := release_drift(target.app_id, target.path, self.release)) is not None:
            self.drifts.append(drift)

    def answer(
        self, config: dict[str, Any], files: Iterable[str], *, compiled: bool
    ) -> list[ReleaseDrift]:
        """What the rows found, or, for a caller that compiles nothing, a read now."""
        if compiled:
            return self.drifts
        return create_release_drifts(self.root, config, files, self.gateway_factory)


def deploy_release_drifts(
    reporter     : Any,
    items        : Sequence[Any],
    apex_version : str | None,
    gateways     : Mapping[str, Any],
) -> list[ReleaseDrift]:
    """`patch -deploy`'s answer: the staged trees the target has outgrown.

    Asked only when there is an import and a reporter that prints the warning,
    so a deploy with no APEXlang tree pays no query. ``apex_version`` is the
    release the caller already probed, else the first import's own connection
    is asked; ``items`` are `ApexImportItem`s, whose ``source`` is the tree.
    """
    if not items or getattr(reporter, "apex_releases", None) is None:
        return []
    release = apex_version or resolve_apex_version(gateways[items[0].schema])
    return release_drifts([(item.app_id, item.source) for item in items], release)


def release_warning_rows(drift: ReleaseDrift) -> list[str]:
    """The way out under the header, numbered like the drift warning's (ADT #961).

    The header names the application and both releases, so the rows are only
    the steps; the header itself is spelled at its printer
    (`cli/patch_create_warnings.print_release_drifts`) so the console
    inventory reads it.
    """
    steps = [
        f"run: {EXPORT_COMMAND.format(app_id=drift.app_id)} on APEX {drift.target}",
        "commit the export",
        "create the patch again",
    ]
    return [f"  {number}) {step}" for number, step in enumerate(steps, start=1)]


__all__ = [
    "EXPORT_COMMAND",
    "TREE_MANIFEST",
    "ReleaseDrift",
    "ReleaseReads",
    "create_release_drifts",
    "deploy_release_drifts",
    "patch_trees",
    "probe_target_release",
    "release_drift",
    "release_drifts",
    "release_warning_rows",
    "tree_release",
]
