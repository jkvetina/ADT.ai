"""What `export_apex` owns in its folders, and how it deletes what a run did not write.

A format's folder is a complete snapshot of the application, so a file a whole-app
run did not write is gone from APEX and goes from the folder too. Two readers act
on that: the export after every unfiltered run, and `diff -restore`
(`diff/pull_apex.py`), which makes a checkout match an export file for file. Both
delete by the sets below, so a file a user keeps beside an export survives the
one exactly as it survives the other (ADT #923).
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

# `WrittenFiles` and the disk question it asks moved to `shared/written_files.py`
# (ADT #1094) so `export_data`'s sidecar sweep could read them too; the names stay
# importable from here.
from adt_ai.shared.apex_paths import APEXLANG_DIR
from adt_ai.shared.apex_payloads import payload_root
from adt_ai.shared.written_files import SameFile, WrittenFiles, same_file

# Every extension `-apexlang` can put under `apexlang/`, and therefore every one
# it may delete from there. The export block drops the application's own
# `shared-components/static-files/` payloads, and the plugin and theme payloads it
# keeps are swept by `prune_plugin_payloads` (ADT #930), so what else lands is the `.apx`
# source, the `.json` metadata (`.apex/apexlang.json`, `deployments/*.json`), the
# supporting objects' `.sql` scripts under `supporting-objects/`, and the `.rtf`
# report layouts under `shared-components/report-layouts/`: every member suffix
# of the 26.1 sample-application exports in `tests/fixtures/sample/`. The live
# probe that first set this list (apps 800 and 808, 2026-07-27) saw only `.apx`
# and `.json`, so a dropped install script's `.sql` survived every re-export
# (ADT #923).
#
# It used to be `None`, meaning "every file", and that swept a developer's own
# `NOTES.md` out of the folder on the next unfiltered run; reproduced live on ADT
# #670. A stale member of some extension APEX has not shipped yet surviving a
# sweep is the cheaper failure by a wide margin.
APEXLANG_SUFFIXES = frozenset({".apx", ".json", ".rtf", ".sql"})

# The same question for `embedded_code/`, which carried the same `None` and so
# the same defect (ADT #670). APEX's `EMBEDDED_CODE` export is an application's
# embedded JavaScript, CSS and PL/SQL, and this writer copies each member name
# verbatim: `postprocess._embedded_relative` only strips the `embedded_code/`
# prefix and rewrites `pages/p` to `pages/page_`, so the suffix on disk is the
# suffix APEX emitted. `.sql` and `.js` are the two this suite's own fixtures
# pin; `.css` is the third member of that triple, listed so a renamed stylesheet
# does not survive forever.
#
# Conservative in the same direction as the APEXlang set above: a stale member
# of an extension APEX has not shipped yet outliving a sweep costs one diff,
# where sweeping every file costs a developer whatever they kept in the folder.
EMBEDDED_SUFFIXES = frozenset({".sql", ".js", ".css"})

#: `-split` writes SQL under `application/`, and `-rest` SQL under its own root.
SQL_SUFFIXES = frozenset({".sql"})

#: `-readable` writes YAML under `application/`, and the page comments are YAML.
YAML_SUFFIXES = frozenset({".yaml"})

#: The folders under an application's own folder that `export_apex` writes, and
#: the extensions it writes in each. `-split` and `-readable` share
#: `application/`, so it owns both of theirs.
OWNED_SUFFIXES: dict[str, frozenset[str]] = {
    "application"  : SQL_SUFFIXES | YAML_SUFFIXES,
    "embedded_code": EMBEDDED_SUFFIXES,
    APEXLANG_DIR   : APEXLANG_SUFFIXES,
    "comments"     : YAML_SUFFIXES,
}


def prune_folder(
    root: Path,
    written: Iterable[Path],
    suffixes: frozenset[str] | None = None,
    same: SameFile | None = None,
    skip: Path | None = None,
) -> None:
    """Delete everything under ``root`` this export did not write.

    The replacement for clearing the folder up front: same end state, minus the
    delete-and-rewrite that gave every surviving file a fresh mtime. Empty
    folders go too, so a component type that lost its last member leaves no
    directory behind either.

    ``suffixes`` narrows the sweep to the extensions the action owns, for a root
    two formats share: `readable/` lands its `.yaml` under the same
    `application/` tree the split export fills with `.sql`, so a split prune that
    took every file would delete the readable export beside it. It limits what is
    deleted only; a file this run wrote is kept whatever its extension.

    ``skip`` is one folder under ``root`` the walk never enters, files and empty
    subfolders alike. The application's own `shared-components/static-files/`
    under an APEXlang tree holds the hardlinks `link_payloads` reconciles against
    `-files`, and a payload there carries any extension, so the `-apexlang` sweep
    deleted its `.json`, `.sql` and `.rtf` members and left `static-files.apx`
    naming files the tree lacked (ADT #1094).
    """
    if not root.is_dir():
        return
    keep = WrittenFiles(written, same)
    for path in sorted(root.rglob("*"), reverse=True):
        if skip is not None and (path == skip or skip in path.parents):
            continue
        if path.is_file():
            if not keep.claims(path) and (suffixes is None or path.suffix in suffixes):
                path.unlink()
        elif path.is_dir() and not any(path.iterdir()):
            path.rmdir()


def prune_plugin_payloads(apexlang_root: Path, written: Iterable[Path]) -> None:
    """Sweep the plugin and theme `static-files/` folders of every file not written.

    Those payloads are the export's own since ADT #930, whatever their extension,
    so the `APEXLANG_SUFFIXES` narrowing does not reach them: a file deleted from
    a plugin in APEX would otherwise survive every re-export. The application's
    own `shared-components/static-files/` is left alone, here and by the
    `prune_folder` sweep above (its ``skip``, ADT #1094), being the links
    `link_payloads` reconciles against `-files`.
    """
    own = payload_root(apexlang_root)
    written = list(written)
    for folder in sorted(apexlang_root.rglob("static-files")):
        if folder.is_dir() and folder != own and own not in folder.parents:
            prune_folder(folder, written)


def sweepable(folder: Path, *homes: Path) -> bool:
    """May `folder` be swept? Not when it is a folder other formats also write into.

    A path key configured as `./` resolves to its parent: `apex_path_files: ./`
    puts the static files in the application's own folder, beside every other
    format, and a sweep there would delete all of them.
    """
    return all(folder != home and folder not in home.parents for home in homes)


__all__ = [
    "APEXLANG_SUFFIXES",
    "EMBEDDED_SUFFIXES",
    "OWNED_SUFFIXES",
    "SQL_SUFFIXES",
    "YAML_SUFFIXES",
    "SameFile",
    "WrittenFiles",
    "prune_folder",
    "prune_plugin_payloads",
    "same_file",
    "sweepable",
]
