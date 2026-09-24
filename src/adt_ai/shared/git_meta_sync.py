"""Keeps a project's root `.gitattributes` / `.gitignore` ADT-owned block current (ADT #938).

`doctor -init` scaffolds these two files once: it skips them when they already
exist and `-force` overwrites them whole. Neither shape reaches a project that
scaffolded before a rule was added to the shipped template, so an ADT.ai
upgrade that changes no user-facing behavior still never lands the fix (a new
`file_crlf` pin, say) on a customer's checkout.

The fix is a block both files carry between two fixed marker lines::

    # >>> adtai managed
    ... the shipped template, rendered for the project's `file_crlf` ...
    # <<< adtai managed

Markers carry no version number, so a template change that adds or edits a
line is exactly the diff `git status` shows; a run that changes nothing writes
nothing (`merge_block` compares bytes before touching disk, same rule
`shared/text_files.py` holds everywhere else). The block sits at the top of a
FRESH file; an already-migrated block is rewritten in place wherever it sits,
because moving text a developer put lines below is not this module's call to
make. Broken markers, a start with no end, an end with no start, or two
blocks, refuse the whole file rather than guess: nothing is written, and the
caller reports the file and the line the break sits on.

A file with no markers at all is a legacy scaffold: the block lands at the
top, and any of the file's own lines that exactly (whitespace-trimmed) repeat
a non-comment, non-blank block line are dropped, since keeping both would
either fight the git "later line wins" rule or just repeat it.

Two callers reach this: `doctor -init -sync` (`doctor/init.py`), which also
renormalizes already-committed files under the block's own patterns and
reports a user line below the block that overrides one of its attributes; and
every export module's `auto_sync_git` pre-write hook
(`cli/export_git_sync.py`), which converts already-tracked CRLF sources to LF
on disk -- never staged, never reported beyond one short row -- and never
reports overrides. `patch` never calls either path.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from adt_ai.shared import text_files
from adt_ai.shared.git_files import git_output

MARK_START = "# >>> adtai managed"
MARK_END = "# <<< adtai managed"


def _content(line: str) -> str:
    """`line`'s text with any trailing `\\r` / `\\n` removed, for marker/duplicate matching."""
    return line.rstrip("\n").rstrip("\r")


def _block_lines(block_content: str) -> list[str]:
    """The shipped template as bare lines, no trailing blank entry for its own newline."""
    stripped = block_content.rstrip("\n")
    return stripped.split("\n") if stripped else []


#: The APEXlang tree stays LF whatever `file_crlf` says: SQLcl's APEXlang
#: compiler reads nothing else (ADT #936), so its pins never follow the key.
_APEXLANG_PREFIX = "**/apexlang/"


def render_gitattributes_block(template: str, *, file_crlf: bool) -> str:
    """The `.gitattributes` block for a project, from the shipped template.

    The template pins every generated text type to `eol=lf`, the shipped
    `file_crlf: False`. A project with `file_crlf: True` gets those general
    pins as `eol=crlf` instead, so the block, the export's own bytes and the
    CRLF conversion all say the same thing and never fight each other on every
    export. The `**/apexlang/**` lines and every `-text` line stay exactly as
    shipped; comments and blank lines are untouched.
    """
    if not file_crlf:
        return template
    rendered: list[str] = []
    for line in template.splitlines(keepends=True):
        stripped = line.strip()
        if (
            stripped
            and not stripped.startswith("#")
            and not stripped.startswith(_APEXLANG_PREFIX)
            and "eol=lf" in stripped.split()
        ):
            line = line.replace("eol=lf", "eol=crlf")
        rendered.append(line)
    return "".join(rendered)


@dataclass(frozen=True)
class BlockMerge:
    """One file's block merged against the shipped template.

    `new_text` is the WHOLE file's new content; the caller writes it (or, on a
    refusal, writes nothing at all -- `new_text` is then the original text,
    unchanged, purely so a caller need not special-case the field away).
    """

    new_text            : str
    changed             : bool
    removed_duplicates  : tuple[str, ...] = ()
    refused             : bool = False
    refused_line        : int | None = None
    refused_reason      : str = ""


def merge_block(existing_text: str, block_content: str, newline: str = "\n") -> BlockMerge:
    """Merge `block_content` (the shipped template) into `existing_text`.

    `existing_text` is `""` for a file that does not exist yet, which lands in
    the legacy-migration branch below and produces a block-only file -- the
    same shape `doctor -init` writes for a fresh scaffold.

    The markers and the block's own lines end in `newline`, the project's
    `file_crlf` ending (ADT #944); every line outside the block keeps the
    bytes it already had, whichever ending that is.
    """
    lines = existing_text.splitlines(keepends=True)
    starts = [index for index, line in enumerate(lines) if _content(line) == MARK_START]
    ends = [index for index, line in enumerate(lines) if _content(line) == MARK_END]
    block_lines = _block_lines(block_content)
    fresh_block = "".join(f"{line}{newline}" for line in block_lines)

    if starts or ends:
        if len(starts) == 1 and len(ends) == 1 and starts[0] < ends[0]:
            start, end = starts[0], ends[0]
            before = "".join(lines[:start])
            after = "".join(lines[end + 1 :])
            new_text = f"{before}{MARK_START}{newline}{fresh_block}{MARK_END}{newline}{after}"
            return BlockMerge(new_text=new_text, changed=new_text != existing_text)
        return BlockMerge(
            new_text=existing_text,
            changed=False,
            refused=True,
            refused_line=_broken_marker_line(starts, ends) + 1,
            refused_reason=_broken_marker_reason(starts, ends),
        )

    # Legacy migration: no markers at all. Drop any user line that exactly
    # (whitespace-trimmed) repeats a non-comment, non-blank block line.
    meaningful = {
        stripped
        for raw in block_lines
        if (stripped := raw.strip()) and not stripped.startswith("#")
    }
    kept: list[str] = []
    removed: list[str] = []
    for raw in lines:
        if _content(raw).strip() in meaningful:
            removed.append(_content(raw))
        else:
            kept.append(raw)
    separator = newline if kept else ""
    new_text = f"{MARK_START}{newline}{fresh_block}{MARK_END}{newline}{separator}{''.join(kept)}"
    return BlockMerge(new_text=new_text, changed=True, removed_duplicates=tuple(removed))


def _broken_marker_line(starts: list[int], ends: list[int]) -> int:
    """The 0-indexed line the refusal is reported against."""
    if not ends:
        return starts[0]
    if not starts:
        return ends[0]
    if starts[0] > ends[0]:
        return starts[0]
    return starts[1] if len(starts) > 1 else ends[1]


def _broken_marker_reason(starts: list[int], ends: list[int]) -> str:
    if not ends:
        return f"`{MARK_START}` with no matching `{MARK_END}`"
    if not starts:
        return f"`{MARK_END}` with no matching `{MARK_START}`"
    if starts[0] > ends[0]:
        return f"`{MARK_START}` appears after `{MARK_END}`"
    return "a second managed block"


def _parse_pattern_lines(block_content: str) -> list[tuple[str, list[str]]]:
    """`(pattern, [attr, ...])` for every non-comment, non-blank `.gitattributes` line."""
    entries: list[tuple[str, list[str]]] = []
    for raw in _block_lines(block_content):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        entries.append((parts[0], parts[1:]))
    return entries


def block_patterns(block_content: str) -> list[str]:
    """Every pattern the block declares, in file order (`*.sql`, `**/files/**`, ...)."""
    return [pattern for pattern, _attrs in _parse_pattern_lines(block_content)]


def _attr_name(spec: str) -> str:
    return spec.lstrip("-!").split("=", 1)[0]


def _attr_expected(spec: str) -> str:
    if spec.startswith("-"):
        return "unset"
    if spec.startswith("!"):
        return "unspecified"
    if "=" in spec:
        return spec.split("=", 1)[1]
    return "set"


def is_git_work_tree(root: Path) -> bool:
    """Whether `root` is inside a git work tree -- `False`, never a crash, when
    `root` does not exist yet.

    `git_output` runs git with `cwd=root`; `subprocess.run` raises
    `FileNotFoundError` for a missing `cwd` before git ever starts, which an
    export's `auto_sync_git` hook hits routinely -- `export_db -root <out>`
    names an output folder the export itself has not created yet (ADT #938).
    """
    if not root.is_dir():
        return False
    return git_output(root, ["rev-parse", "--is-inside-work-tree"]) == "true"


def _git_paths_with_pathspec(root: Path, args: list[str], patterns: list[str]) -> list[str]:
    """`git <args> -- <patterns>`, path output split on NUL.

    `git_files.run_git_paths` appends its own `-z` AFTER whatever the caller
    passes, so a caller that also needs `--` to separate pathspecs from
    options cannot use it directly: `-z` would land after `--` and be read as
    a literal pathspec instead of a flag. `-z` goes right after the
    subcommand here, ahead of `--`, so both survive.
    """
    from adt_ai.shared.git_files import run_git_bytes

    output = run_git_bytes(root, [args[0], "-z", *args[1:], "--", *patterns]).decode(
        "utf-8", errors="surrogateescape"
    )
    return [record for record in output.split("\0") if record]


def _eol_attributes(root: Path, paths: list[str]) -> dict[str, dict[str, str]]:
    """`{path: {"text": ..., "eol": ...}}`, git's EFFECTIVE attributes for `paths`.

    One batched `git check-attr --stdin -z text eol` call, paths on stdin, so
    its command line never grows with the list (ADT #943).
    """
    from adt_ai.shared.git_files import run_git_bytes

    output = run_git_bytes(
        root,
        ["check-attr", "--stdin", "-z", "text", "eol"],
        stdin="".join(f"{path}\0" for path in paths).encode("utf-8", errors="surrogateescape"),
    )
    fields = output.decode("utf-8", errors="surrogateescape").split("\0")
    values: dict[str, dict[str, str]] = {}
    # `-z` output is `<path> NUL <attr> NUL <value> NUL`, repeated.
    for index in range(0, len(fields) - 2, 3):
        path, attr, value = fields[index : index + 3]
        values.setdefault(path, {})[attr] = value
    return values


def _effective_lf_paths(root: Path, paths: list[str]) -> set[str]:
    """The subset of `paths` git itself would check out as LF text.

    Decided from git's EFFECTIVE attributes, never from the block's patterns:
    a pattern match alone would also claim a `-text` payload (`**/files/**`,
    an APEXlang `static-files/` tree, a PNG or a zip whose bytes merely
    contain `\\r\\n`) and a project's own `*.sql text eol=crlf` below the
    block. A path qualifies when `eol` is `lf` and `text` is not `unset`.
    """
    return {
        path
        for path, attrs in _eol_attributes(root, paths).items()
        if attrs.get("eol") == "lf" and attrs.get("text") != "unset"
    }


def checkout_eols(root: Path, paths: list[str]) -> dict[str, str]:
    """`{path: "CRLF" | "LF"}`, the ending each path checks out with.

    What `doctor -init -sync` names its normalized groups by (ADT #944): the
    reader wants the ending the file now has, not the git verb that got it
    there. Only an `eol=crlf` attribute checks out CRLF; everything else in a
    renormalize, the APEXlang pins under `file_crlf: True` included, is LF.
    """
    if not paths:
        return {}
    attributes = _eol_attributes(root, paths)
    return {
        path: "CRLF" if attributes.get(path, {}).get("eol") == "crlf" else "LF"
        for path in paths
    }


def convert_tracked_crlf(root: Path, patterns: list[str]) -> list[str]:
    """Rewrite every tracked LF-pinned file under `patterns` that holds CRLF, on disk only.

    Never stages anything -- this is what an export's `auto_sync_git` hook
    calls, and step 5's `git add --renormalize` is a different, doctor-only
    path. `patterns` only narrows the candidates; whether a file is converted
    is git's own effective-attribute answer (`_effective_lf_paths`), so a
    `-text` payload or a user's CRLF override is never touched. Root-relative
    paths, sorted, of every file actually rewritten; a file already LF is not
    touched.
    """
    if not patterns or not is_git_work_tree(root):
        return []
    tracked = sorted(set(_git_paths_with_pathspec(root, ["ls-files"], patterns)))
    if not tracked:
        return []
    lf_paths = _effective_lf_paths(root, tracked)
    converted: list[str] = []
    for relative in tracked:
        if relative not in lf_paths:
            continue
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        if b"\r\n" not in data:
            continue
        lf = data.replace(b"\r\n", b"\n")
        text_files.write_bytes(path, lf)
        converted.append(relative)
    return converted


#: `git add` reads its paths from stdin, NUL-separated, and never from argv: a
#: list that grows with the repository outgrew `ARG_MAX` on a real project
#: (`OSError: [Errno 7] Argument list too long: 'git'`, ADT #943). Each path
#: is taken literally, since it is a file name `ls-files` or the caller already
#: resolved, and a `[` or `*` in one must match that one file and no other.
_ADD_PATHS_FROM_STDIN = [
    "--literal-pathspecs", "add", "--pathspec-from-file=-", "--pathspec-file-nul",
]


def _nul_joined(paths: list[str]) -> str:
    return "".join(f"{path}\0" for path in paths)


def stage_paths(root: Path, paths: list[str]) -> None:
    """`git add` the block files `doctor -init -sync` rewrote, paths on stdin.

    Doctor-only, like `renormalize_staged`; an export never stages. A silent
    no-op outside a git work tree or with nothing to stage. Tolerant of a
    refusal (a project that ignores its own `.gitignore`, say): the file is
    already written, and staging is a convenience on top of that.
    """
    if not paths or not is_git_work_tree(root):
        return
    git_output(root, _ADD_PATHS_FROM_STDIN, stdin=_nul_joined(paths))


def renormalize_staged(root: Path, patterns: list[str]) -> list[str]:
    """`git add --renormalize` over `patterns`, staged only; the paths it newly staged.

    Doctor-only (`-init -sync`); an export's on-disk conversion never stages.
    Outside a git work tree this is a silent no-op, same as the caller's own
    renormalize step is documented to be.
    """
    if not patterns or not is_git_work_tree(root):
        return []
    # `git add` (unlike `ls-files`/`diff`) is FATAL when ANY ONE pathspec token
    # matches no file at all, which a bare pattern list hits constantly: a repo
    # with `.sql` files but nothing under `files/` fails the whole call over
    # the one pattern that matched nothing. Resolving to the concrete tracked
    # paths first sidesteps that -- each one necessarily matches itself -- and
    # doubles as the "nothing tracked" no-op below.
    tracked = _git_paths_with_pathspec(root, ["ls-files"], patterns)
    if not tracked:
        return []
    from adt_ai.shared.git_files import run_git_bytes

    # The before/after snapshots filter on the block's PATTERNS, which are
    # bounded by the template, never on `tracked`, which is bounded by nothing
    # (ADT #943). `git diff` tolerates a pattern that matches no file, and a
    # staged deletion the patterns catch but `tracked` cannot is in both
    # snapshots, so the difference is the same set the file list gave.
    diff_args = ["diff", "--cached", "--name-only"]
    before = set(_git_paths_with_pathspec(root, diff_args, patterns))
    run_git_bytes(
        root,
        [*_ADD_PATHS_FROM_STDIN, "--renormalize"],
        stdin=_nul_joined(tracked).encode("utf-8", errors="surrogateescape"),
    )
    after = set(_git_paths_with_pathspec(root, diff_args, patterns))
    return sorted(after - before)


def attribute_overrides(root: Path, block_content: str) -> list[str]:
    """Every pattern/attribute the block sets that a lower user line overrides.

    Reads the file back through `git check-attr`, which already resolves
    "later line wins", so this reports a real behavioral disagreement rather
    than merely a second mention of the pattern. Empty outside a git work
    tree, or once `block_content` names no pattern.
    """
    if not is_git_work_tree(root):
        return []
    from adt_ai.shared.git_files import run_git

    findings: list[str] = []
    for pattern, attrs in _parse_pattern_lines(block_content):
        names = [_attr_name(spec) for spec in attrs]
        expected = {_attr_name(spec): _attr_expected(spec) for spec in attrs}
        output = run_git(root, ["check-attr", *names, "--", pattern])
        for line in output.splitlines():
            # `<path>: <attr>: <value>`, and <path> here is `pattern` itself.
            parts = line.split(": ", 2)
            if len(parts) != 3:
                continue
            _path, attr, value = parts
            if attr in expected and value != expected[attr]:
                findings.append(
                    f"{pattern}: {attr} is {value}, the managed block sets {expected[attr]}"
                )
    return findings


__all__ = [
    "MARK_START",
    "MARK_END",
    "BlockMerge",
    "merge_block",
    "render_gitattributes_block",
    "block_patterns",
    "is_git_work_tree",
    "convert_tracked_crlf",
    "stage_paths",
    "renormalize_staged",
    "checkout_eols",
    "attribute_overrides",
]
