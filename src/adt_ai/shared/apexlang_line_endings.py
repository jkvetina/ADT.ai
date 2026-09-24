"""An APEXlang tree carries LF line endings, because SQLcl's compiler reads nothing else.

ADT #928. A database whose APEX answers the export with CRLF wrote a tree SQLcl
could not compile: every `\\r` is a `token recognition error`, and on a whole
application the compiler did not even get that far, `global-template-options.apx`
crashed it with a `NullPointerException` and the import block SQLcl ran next
printed `ORA-01403`. The same tree with LF line endings compiles.

`export_apex` writes LF since then, whatever `file_crlf` says (ADT #936, Jan:
*"we need to force export apexlang in LF, even if user has CRLF in config"*),
and a tree already committed with CRLF, or
checked out that way by `core.autocrlf` on Windows, is converted here before
`validate` or a `patch -deploy` import hands it to SQLcl. **Converted, never
refused**: the tree is the developer's source, and the remedy a refusal could name,
a re-export, throws away an `.apx` edited by hand, which is the whole reason a
tree is deployed from the files rather than from the database.

Only the compiler's own inputs are touched, the `.apx` and `.json` files. Static
file payloads are the application's bytes, a `\\r\\n` inside a zip included, and
the payload folders hold hard links into `export_apex -files`' folder, so a
rewrite there would reach through into the export itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

from adt_ai.shared import text_files
from adt_ai.shared.progress import print_adt_header

SOURCE_SUFFIXES = (".apx", ".json")
PAYLOAD_DIR = "static-files"


def lf_bytes(content: str) -> bytes:
    """``content`` as UTF-8 with every line break, CRLF, CR or LF, one LF.

    What `export_apex -apexlang` writes, never the configured `file_crlf` ending.
    """
    return text_files.normalize(content).encode("utf-8")


def is_source(relative: Path) -> bool:
    """Is ``relative``, a path inside a tree, one of the compiler's own inputs?"""
    return relative.suffix in SOURCE_SUFFIXES and PAYLOAD_DIR not in relative.parts[:-1]


def import_bytes(data: bytes) -> bytes:
    """A source file's bytes as the import reads them, after :func:`convert_crlf`."""
    return data.replace(b"\r\n", b"\n")


def convert_crlf(tree_root: Path) -> list[str]:
    """Rewrite every CRLF source file under ``tree_root`` with LF; the paths, sorted.

    A file with no `\\r\\n` is not rewritten at all, so an LF tree keeps every
    timestamp and a second run is a no-op.
    """
    if not tree_root.is_dir():
        return []
    converted: list[str] = []
    for path in sorted(tree_root.rglob("*")):
        relative = path.relative_to(tree_root)
        if not is_source(relative) or not path.is_file():
            continue
        data = path.read_bytes()
        if (lf := import_bytes(data)) == data:
            continue
        text_files.write_bytes(path, lf)
        converted.append(relative.as_posix())
    return converted


class PrecheckIssue(NamedTuple):
    """One tree the precheck had to change before SQLcl could read it.

    Rides in the same notes list as a plain `NOTES:` row, so neither `validate`
    nor a `patch -deploy` import grows a second list to thread through; the
    printers below pull it out by type.
    """

    folder      : str
    description : str


PRECHECK_HEADER = "WARNING - APEXLANG PRECHECK ISSUE:"


def crlf_issue(label: str, converted: list[str]) -> PrecheckIssue:
    """The warning for one converted tree: what changed and what is left to do.

    The conversion is on disk and not in git, so it asks for the commit; a
    checkout that converts again on every run is a `core.autocrlf` setting, and
    the commit is what shows it.
    """
    return PrecheckIssue(
        label,
        f"{len(converted)} file(s) converted from CRLF to LF, "
        "which the APEXlang compiler needs - commit them",
    )


def print_precheck_issues(notes: Sequence[str | PrecheckIssue]) -> list[str]:
    """Print the precheck issues among ``notes``; the plain notes, for `NOTES:`.

    ADT #934. As a `NOTES:` row the conversion read like a violation, and it is
    a warning: the run changed the developer's files and carried on. The folder
    sits on its own line with what happened one level under it, the shape every
    other per-folder warning has.
    """
    issues = [note for note in notes if isinstance(note, PrecheckIssue)]
    if issues:
        print_adt_header(PRECHECK_HEADER)
        for issue in issues:
            print(f"  {issue.folder}")
            print(f"    {issue.description}")
        print()
    return [note for note in notes if not isinstance(note, PrecheckIssue)]


__all__ = [
    "PAYLOAD_DIR",
    "PRECHECK_HEADER",
    "PrecheckIssue",
    "SOURCE_SUFFIXES",
    "convert_crlf",
    "crlf_issue",
    "import_bytes",
    "is_source",
    "lf_bytes",
    "print_precheck_issues",
]
